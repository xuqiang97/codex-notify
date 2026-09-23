import contextlib
import io
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

import notify
from providers import ConfigurationError, ProviderError


# Public, deliberately fake fixtures. Never subscribed to or published.
VALUES = {"NTFY_TOPIC": "unit-test-topic-not-a-secret", "CODEX_NOTIFY_DEVICE": "测试电脑"}
ROOT = Path(__file__).resolve().parents[1]


class OfflineTest(unittest.TestCase):
    def setUp(self):
        # Fail closed if a future change accidentally introduces real network I/O.
        self.addCleanup(patch.stopall)
        patch("socket.socket", side_effect=AssertionError("tests must be offline")).start()
        patch("socket.getaddrinfo", side_effect=AssertionError("tests must be offline")).start()


class EventTests(OfflineTest):
    def invoke(self, args, values=None):
        error = io.StringIO()
        with patch.object(sys, "argv", ["notify.py", *args]), \
                patch.dict(os.environ, VALUES if values is None else values, clear=True), \
                patch("notify.read_env", return_value={}), \
                patch("notify.ntfy.send_notification") as send, \
                contextlib.redirect_stderr(error):
            result = notify.main()
        return result, error.getvalue(), send

    def test_valid_event_publishes_exactly_once(self):
        event = {"type": "agent-turn-complete", "cwd": "/work/demo",
                 "last-assistant-message": "Finished tests.", "future-field": {"ignored": True}}
        result, error, send = self.invoke([json.dumps(event)])
        self.assertEqual((result, error), (0, ""))
        send.assert_called_once()
        notification = send.call_args.args[1]
        self.assertEqual(notification.title, "demo · 本轮已结束")
        self.assertEqual(notification.message, "设备：测试电脑\n项目：demo\n状态：本轮已结束")

    def test_unsupported_events_do_not_load_config_or_publish(self):
        for kind in ("approval-requested", "other", None, [], {}):
            with self.subTest(kind=kind):
                with patch("notify.load_config", side_effect=AssertionError("must not load")):
                    result, error, send = self.invoke([json.dumps({"type": kind})], {})
                self.assertEqual((result, error), (0, ""))
                send.assert_not_called()

    def test_reply_content_never_changes_notification_or_delivery(self):
        replies = [
            "检查失败，需要补充配置。", "检查通过。", '{"suggestions":[]}',
            '{"password":"private-fixture"}', '{"api_key":"private-fixture"}',
            "密码：private-fixture", "演示客户甲的内部报价为 12345 元。",
            "return customer.private_field;", "普通短回复", "x" * 50000,
            None, "", " \n ", {}, [], 42, "\ud800\u202e",
        ]
        for reply in replies:
            with self.subTest(reply_type=type(reply).__name__):
                event = {"type": "agent-turn-complete", "cwd": "/work/demo",
                         "last-assistant-message": reply,
                         "input-messages": ["PRIVATE INPUT"]}
                result, error, send = self.invoke([json.dumps(event)])
                self.assertEqual((result, error), (0, ""))
                send.assert_called_once()
                notification = send.call_args.args[1]
                self.assertEqual(notification.title, "demo · 本轮已结束")
                self.assertEqual(notification.message, "设备：测试电脑\n项目：demo\n状态：本轮已结束")

    def test_missing_and_extra_arguments(self):
        for args in ([], ["{}", "{}"]):
            result, error, send = self.invoke(args)
            self.assertEqual(result, 2)
            self.assertIn("one event JSON", error)
            send.assert_not_called()

    def test_quoted_credentials_in_metadata_fall_back_without_skipping_push(self):
        # Fake labels only; check the actual HTTP body without sending it.
        for prefix in ("", "x" * 55, "x" * 100):
            with self.subTest(prefix_length=len(prefix)):
                label = prefix + '{"password":"private-fixture"}'
                values = dict(VALUES, CODEX_NOTIFY_DEVICE=label, CODEX_NOTIFY_TASK_TITLE="1")
                event = {"type": "agent-turn-complete", "cwd": "/work/" + label}
                with patch("notify.lookup_task_title", return_value=label):
                    result, error, send = self.invoke([json.dumps(event)], values)
                self.assertEqual((result, error), (0, ""))
                send.assert_called_once()
                config, notification = send.call_args.args
                request = notify.ntfy.build_request(config, notification)
                body = json.loads(request.data)
                self.assertEqual(body["title"], "Codex · 本轮已结束")
                self.assertEqual(body["message"],
                                 "设备：unknown-device\n项目：unknown-project\n状态：本轮已结束")
                self.assertNotIn("private-fixture", request.data.decode("utf-8"))

    def test_invalid_payloads_are_safe(self):
        for raw in ("private malformed input", "[]", "null", "42", '"text"', "[" * 2000):
            with self.subTest(raw=raw[:20]):
                result, error, send = self.invoke([raw])
                self.assertEqual(result, 2)
                self.assertNotIn("private malformed input", error)
                self.assertNotIn("Traceback", error)
                send.assert_not_called()

    def test_missing_optional_fields(self):
        result, error, send = self.invoke(['{"type":"agent-turn-complete"}'])
        self.assertEqual((result, error), (0, ""))
        self.assertTrue(send.call_args.args[1].message.endswith("状态：本轮已结束"))
        self.assertEqual(len(send.call_args.args[1].message.splitlines()), 3)

    def test_missing_topic_is_a_local_error(self):
        result, error, send = self.invoke(['{"type":"agent-turn-complete"}'], {})
        self.assertEqual(result, 2)
        self.assertIn("NTFY_TOPIC is required", error)
        send.assert_not_called()

    def test_provider_failure_is_best_effort(self):
        with patch("notify.send_notification", side_effect=ProviderError("ntfy delivery timed out")):
            result, error, _ = self.invoke(['{"type":"agent-turn-complete"}'])
        self.assertEqual(result, 0)
        self.assertIn("timed out", error)
        self.assertNotIn("Traceback", error)

    def test_unknown_provider_no_network_or_value_leak(self):
        values = dict(VALUES, CODEX_NOTIFY_PROVIDER="private-provider-value")
        result, error, send = self.invoke(['{"type":"agent-turn-complete"}'], values)
        self.assertEqual(result, 2)
        self.assertNotIn("private-provider-value", error)
        send.assert_not_called()


class ConfigTests(OfflineTest):
    def setUp(self):
        super().setUp()
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.path = Path(temporary.name) / ".env"

    def load(self, values=None):
        return notify.load_config(VALUES if values is None else values, self.path)

    def test_defaults_and_hostname(self):
        with patch("notify.socket.gethostname", return_value="host-A"):
            config = self.load({"NTFY_TOPIC": VALUES["NTFY_TOPIC"]})
        self.assertEqual(config.device, "host-A")
        self.assertEqual(config.provider, "ntfy")
        self.assertEqual(config.ntfy.server, "https://ntfy.sh")
        self.assertEqual(config.ntfy.timeout, 5)
        self.assertFalse(config.task_title)

    def test_task_title_opt_in_validation(self):
        self.assertTrue(self.load(dict(VALUES, CODEX_NOTIFY_TASK_TITLE="1")).task_title)
        for value in ("", "true", "2", "private-value"):
            with self.assertRaises(ConfigurationError) as raised:
                self.load(dict(VALUES, CODEX_NOTIFY_TASK_TITLE=value))
            self.assertNotIn("private-value", str(raised.exception))

    def test_blank_device_uses_hostname_and_hostname_failure_degrades(self):
        with patch("notify.socket.gethostname", side_effect=OSError("private hostname")):
            self.assertEqual(self.load(dict(VALUES, CODEX_NOTIFY_DEVICE=" ")).device, "unknown-device")

    def test_override_precedence_and_no_environment_mutation(self):
        self.path.write_text("NTFY_TOPIC=file-fixture\nCODEX_NOTIFY_DEVICE=file-host\nCODEX_NOTIFY_TIMEOUT=3\n", encoding="utf-8")
        values = dict(VALUES)
        config = self.load(values)
        self.assertEqual(config.ntfy.topic, VALUES["NTFY_TOPIC"])
        self.assertEqual(config.device, "测试电脑")
        self.assertEqual(config.ntfy.timeout, 3)
        self.assertEqual(values, VALUES)
        with self.assertRaisesRegex(ConfigurationError, "required"):
            self.load({"NTFY_TOPIC": ""})

    def test_bom_crlf_comments_quotes_and_literal_windows_path(self):
        self.path.write_bytes(
            b"\xef\xbb\xbf# comment\r\n\r\nNTFY_TOPIC='file-fixture'\r\n"
            b'CODEX_NOTIFY_DEVICE="machine #1"\r\nOTHER=C:\\Users\\test\r\n'
            b"LITERAL=$(never-execute)${NEVER_EXPAND}\r\n"
        )
        values = notify.read_env(self.path)
        self.assertEqual(values["CODEX_NOTIFY_DEVICE"], "machine #1")
        self.assertEqual(values["OTHER"], r"C:\Users\test")
        self.assertEqual(values["LITERAL"], "$(never-execute)${NEVER_EXPAND}")

    def test_invalid_env_has_safe_line_diagnostic(self):
        for line in ("private broken text", "export NTFY_TOPIC=x", 'NTFY_TOPIC="private'):
            self.path.write_text(line, encoding="utf-8")
            with self.assertRaises(ConfigurationError) as raised:
                self.load()
            self.assertIn("line 1", str(raised.exception))
            self.assertNotIn("private", str(raised.exception))

    def test_invalid_env_encoding_and_permission_error(self):
        self.path.write_bytes(b"\xff\xff")
        with self.assertRaisesRegex(ConfigurationError, "UTF-8"):
            self.load()
        with patch.object(Path, "read_text", side_effect=PermissionError("private path")):
            with self.assertRaisesRegex(ConfigurationError, "UTF-8"):
                self.load()

    def test_obsolete_preview_setting_cannot_restore_content(self):
        event = {"cwd": "/work/demo", "last-assistant-message": "PRIVATE REPLY"}
        for value in ("300", "500", "0", "", "invalid", "9" * 5000):
            with self.subTest(value=value[:20]):
                self.path.write_text("CODEX_NOTIFY_SUMMARY_MAX=" + value + "\n", encoding="utf-8")
                for config in (self.load(), self.load(dict(VALUES, CODEX_NOTIFY_SUMMARY_MAX=value))):
                    notification = notify.build_notification(event, config)
                    self.assertEqual(notification.message, "设备：测试电脑\n项目：demo\n状态：本轮已结束")

    def test_example_is_not_a_working_topic(self):
        with self.assertRaisesRegex(ConfigurationError, "template"):
            notify.load_config({}, ROOT / ".env.example")

    def test_default_env_is_next_to_script_not_cwd(self):
        with patch("notify.read_env", return_value={}) as read:
            notify.load_config(VALUES)
        read.assert_called_once_with(ROOT / ".env")


class ProjectScopeTests(OfflineTest):
    def config(self, roots):
        with patch("notify.read_env", return_value={}):
            return notify.load_config(dict(VALUES, CODEX_NOTIFY_PROJECT_ROOTS=json.dumps(roots)))

    def test_default_scope_preserves_missing_cwd_fallback(self):
        with patch("notify.read_env", return_value={}):
            config = notify.load_config(VALUES)
        self.assertEqual(config.project_roots, ())
        self.assertTrue(notify.is_in_project_scope({}, config))

    def test_windows_scope_matches_components_case_and_both_separators(self):
        config = self.config(["D:/Projects/"])
        for cwd in ("D:/Projects", "d:/projects/app", "D:\\Projects\\中文 项目\\src"):
            with self.subTest(cwd=cwd):
                self.assertTrue(notify.is_in_project_scope({"cwd": cwd}, config))
        for cwd in ("D:/Projects-other/app", "E:/Projects/app", "D:/Projects/../private", "/Projects/app"):
            with self.subTest(cwd=cwd):
                self.assertFalse(notify.is_in_project_scope({"cwd": cwd}, config))

    def test_posix_scope_is_case_sensitive_and_accepts_multiple_roots(self):
        config = self.config(["/Users/test/Projects", "/work/other"])
        for cwd in ("/Users/test/Projects/app", "/work/other", "/work/other/中文项目"):
            self.assertTrue(notify.is_in_project_scope({"cwd": cwd}, config))
        for cwd in ("/users/test/Projects/app", "/work/other-app", "/work/elsewhere"):
            self.assertFalse(notify.is_in_project_scope({"cwd": cwd}, config))

    def test_unc_scope_does_not_match_another_share(self):
        config = self.config([r"\\server\share\Projects"])
        self.assertTrue(notify.is_in_project_scope({"cwd": r"\\SERVER\share\Projects\app"}, config))
        self.assertFalse(notify.is_in_project_scope({"cwd": r"\\server\share-other\Projects\app"}, config))

    def test_missing_or_invalid_event_cwd_never_uses_process_cwd(self):
        config = self.config(["D:/Projects"])
        for cwd in (None, {}, [], 42, "", "relative", "D:Projects", "D:/Projects/../other", "D:/Projects/\x00bad"):
            with self.subTest(cwd=cwd), patch("notify.os.getcwd", side_effect=AssertionError("no fallback")):
                self.assertFalse(notify.is_in_project_scope({"cwd": cwd}, config))

    def test_bad_scope_config_never_echoes_values(self):
        invalid = ("", "private-value", "[" * 2000, "null", "{}", '"private-value"',
                   '["relative-private-value"]', '[null]', '[42]', '[{}]',
                   '["/private-value/../other"]', '["https://private-value.invalid"]')
        for raw in invalid:
            with self.subTest(raw=raw[:40]), patch("notify.read_env", return_value={}):
                with self.assertRaises(ConfigurationError) as raised:
                    notify.load_config(dict(VALUES, CODEX_NOTIFY_PROJECT_ROOTS=raw))
                self.assertNotIn("private-value", str(raised.exception))

    def test_environment_scope_overrides_file_and_can_disable_it(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / ".env"
            path.write_text('CODEX_NOTIFY_PROJECT_ROOTS=["/work/file"]\n', encoding="utf-8")
            config = notify.load_config(VALUES, path)
            self.assertTrue(notify.is_in_project_scope({"cwd": "/work/file/project"}, config))
            config = notify.load_config(dict(VALUES, CODEX_NOTIFY_PROJECT_ROOTS='["/work/env"]'), path)
            self.assertFalse(notify.is_in_project_scope({"cwd": "/work/file/project"}, config))
            self.assertTrue(notify.is_in_project_scope({"cwd": "/work/env/project"}, config))
            config = notify.load_config(dict(VALUES, CODEX_NOTIFY_PROJECT_ROOTS="[]"), path)
            self.assertTrue(notify.is_in_project_scope({}, config))
            self.assertNotIn("/work/", repr(config))

    def invoke(self, event):
        with patch("notify.read_env", return_value={}), \
                patch.dict(os.environ, dict(VALUES, CODEX_NOTIFY_PROJECT_ROOTS='["D:/Projects"]'), clear=True), \
                patch.object(sys, "argv", ["notify.py", json.dumps(event)]), \
                patch("notify.ntfy.send_notification") as send, \
                patch("notify.lookup_task_title", side_effect=AssertionError("no index reads")), \
                contextlib.redirect_stderr(io.StringIO()) as error:
            result = notify.main()
        return result, error.getvalue(), send

    def test_runtime_suggestion_event_is_silently_skipped_before_building(self):
        event = {"type": "agent-turn-complete", "cwd": "C:/Users/test/AppData/Local/OpenAI/Codex/bin/0123456789abcdef",
                 "last-assistant-message": '{"suggestions":[]}'}
        with patch("notify.build_notification", side_effect=AssertionError("must skip content")):
            result, error, send = self.invoke(event)
        self.assertEqual((result, error), (0, ""))
        send.assert_not_called()

    def test_valid_project_json_and_hash_names_are_not_filtered(self):
        for message in ('{"suggestions":[]}', '{"ok":true}', "Work mode completed."):
            result, error, send = self.invoke({"type": "agent-turn-complete", "cwd": "D:/Projects/0123456789abcdef",
                                                "last-assistant-message": message})
            self.assertEqual((result, error), (0, ""))
            send.assert_called_once()
            self.assertNotIn(message, send.call_args.args[1].message)

    def test_main_skips_missing_cwd_in_scoped_mode(self):
        result, error, send = self.invoke({"type": "agent-turn-complete"})
        self.assertEqual((result, error), (0, ""))
        send.assert_not_called()


class ContentTests(OfflineTest):
    def test_project_paths_from_both_platforms(self):
        cases = {
            "/Users/person/work/project/": "project",
            "C:\\Users\\person\\Project Name\\": "Project Name",
            "C:/Users/person/中文项目/": "中文项目",
            r"\\server\share\repository": "repository",
            "/work/.hidden": ".hidden",
        }
        for path, expected in cases.items():
            with self.subTest(path=path):
                self.assertEqual(notify.project_name({"cwd": path}), expected)

    def test_untrusted_or_root_paths_fall_back(self):
        for value in (None, 5, {}, "relative", "/", "C:\\", "/a/../b", "bad\x00path", "https://example.com/repo"):
            with self.subTest(value=value), patch("notify.os.getcwd", return_value="/safe/fallback"):
                self.assertEqual(notify.project_name({"cwd": value}), "fallback")

    def test_missing_cwd_and_unavailable_process_directory(self):
        with patch("notify.os.getcwd", side_effect=FileNotFoundError("private path")):
            self.assertEqual(notify.project_name({}), "unknown-project")

    def test_unicode_and_whitespace(self):
        self.assertEqual(notify.normalize("  已完成\n\t测试　✅  👩‍💻 "), "已完成 测试 ✅ 👩‍💻")

    def test_controls_surrogates_and_bidi_are_removed(self):
        self.assertEqual(notify.normalize("done\x00\ud800\u202eevil"), "doneevil")

    def test_normalization_cannot_reintroduce_sensitive_content(self):
        for text in ("pass\x00word=fixture", "https:\u202e//private.invalid/", "secret-fi\x00xture"):
            self.assertTrue(notify.private_text(text, ("secret-fixture",)))

    def test_truncation_and_minimum_limit(self):
        self.assertEqual(notify.truncate("你好世界完成", 4), "你好世…")
        self.assertEqual(notify.truncate("abcd", 4), "abcd")
        self.assertEqual(notify.truncate("abcd", 1), "…")

    def test_obvious_paths_code_and_credentials_are_private_metadata(self):
        fixtures = (
            "Created /Users/person/private/file.txt", r"Created C:\Users\person\file",
            r"Saved \\server\share\file", "```python\nprint('secret')\n```",
            "Changed `private_source()`", "diff --git a/private b/private", "def private():\n    pass",
            "API_KEY=fixture-value", "password: fixture-value", "Authorization: Bearer fixture-value",
            "Review https://example.invalid/?token=fixture", "sk-" + "FAKE" * 10,
            "-----BEGIN PRIVATE KEY-----", "Summary " + "x" * 400 + " token=private",
        )
        for value in fixtures:
            with self.subTest(value=value[:30]):
                self.assertTrue(notify.private_text(value, ()))

    def test_quoted_credential_keys_are_private_metadata(self):
        for key in ("password", "passwd", "secret", "token", "authorization",
                    "api_key", "API-KEY", "api key", "access_token", "access-token"):
            for quote in ('"', "'"):
                for separator in (":", " = ", " \n : "):
                    value = "{" + quote + key + quote + separator + quote + "private-fixture" + quote + "}"
                    with self.subTest(key=key, quote=quote, separator=separator):
                        self.assertTrue(notify.private_text(value, ()))
        self.assertTrue(notify.private_text('{"pass\x00word"\u202e: "private-fixture"}', ()))
        for label in ("修复 password 校验", 'Review "token" handling', "普通任务名称"):
            with self.subTest(label=label):
                self.assertFalse(notify.private_text(label, ()))

    def test_prompts_thread_ids_unknown_fields_and_reply_are_not_sent(self):
        with patch("notify.read_env", return_value={}):
            config = notify.load_config(VALUES)
        event = {"cwd": "/private/home/demo", "input-messages": ["PRIVATE USER PROMPT"],
                 "thread-id": "PRIVATE THREAD", "turn-id": "PRIVATE TURN", "unknown": "PRIVATE UNKNOWN",
                 "last-assistant-message": "Completed " + "x" * 400 + "PRIVATE TAIL"}
        notification = notify.build_notification(event, config)
        outgoing = notification.title + notification.message
        self.assertNotIn("PRIVATE", outgoing)
        self.assertNotIn("/private/home", outgoing)
        self.assertIn("项目：demo", outgoing)
        self.assertEqual(notification.message, "设备：测试电脑\n项目：demo\n状态：本轮已结束")

    def test_configured_secrets_never_appear_in_display_content(self):
        values = dict(VALUES, CODEX_NOTIFY_DEVICE=VALUES["NTFY_TOPIC"], NTFY_TOKEN="fake-bearer-fixture")
        with patch("notify.read_env", return_value={}):
            config = notify.load_config(values)
        notification = notify.build_notification({
            "cwd": "/work/" + VALUES["NTFY_TOPIC"],
            "last-assistant-message": "Result fake-bearer-fixture",
        }, config)
        for secret in (VALUES["NTFY_TOPIC"], "fake-bearer-fixture"):
            self.assertNotIn(secret, notification.title + notification.message)
            self.assertNotIn(secret, repr(config))
            self.assertNotIn(secret, repr(config.ntfy))

    def test_project_privacy_is_checked_before_truncation(self):
        values = dict(VALUES, NTFY_TOKEN="fake-bearer-fixture")
        with patch("notify.read_env", return_value={}):
            config = notify.load_config(values)
        for secret in (values["NTFY_TOPIC"], values["NTFY_TOKEN"]):
            # The old 64-character cap exposed a prefix of this configured secret.
            name = "p" * 55 + secret
            for cwd in ("C:/work/" + name, "/work/" + name):
                with self.subTest(cwd=cwd):
                    result = notify.build_notification({"cwd": cwd}, config)
                    self.assertEqual(result.title, "Codex · 本轮已结束")
                    self.assertIn("项目：unknown-project", result.message)
                    self.assertNotIn(secret[:8], result.message)

    def test_project_fallback_and_late_sensitive_markers_are_checked(self):
        for name in ("p" * 100 + " password=fixture", "p" * 55 + VALUES["NTFY_TOPIC"]):
            with patch("notify.os.getcwd", return_value="/work/" + name):
                self.assertEqual(notify.project_name({}, (VALUES["NTFY_TOPIC"],)), "unknown-project")


class CliTests(OfflineTest):
    def test_script_path_with_spaces_and_unicode(self):
        with tempfile.TemporaryDirectory(prefix="codex notify 中文 ") as directory:
            script = Path(directory) / "notify.py"
            shutil.copyfile(ROOT / "notify.py", script)
            shutil.copyfile(ROOT / "task_metadata.py", Path(directory) / "task_metadata.py")
            shutil.copytree(ROOT / "providers", Path(directory) / "providers",
                            ignore=shutil.ignore_patterns("__pycache__"))
            result = subprocess.run([sys.executable, str(script), '{"type":"unsupported"}'],
                                    capture_output=True, text=True, timeout=5)
            self.assertEqual((result.returncode, result.stdout, result.stderr), (0, "", ""))

    def test_actual_cli_from_unrelated_directory_with_space(self):
        with tempfile.TemporaryDirectory(prefix="codex notify ") as directory:
            # This hostile cwd config must never be read by the script.
            Path(directory, ".env").write_text("THIS FILE MUST NOT BE LOADED", encoding="utf-8")
            environment = dict(os.environ, NTFY_TOPIC="", CODEX_NOTIFY_PROVIDER="ntfy")
            for args, expected in (([], 2), (["invalid private input"], 2),
                                   (['{"type":"unsupported"}'], 0),
                                   (['{"type":"agent-turn-complete"}'], 2)):
                result = subprocess.run([sys.executable, str(ROOT / "notify.py"), *args],
                                        cwd=directory, env=environment, capture_output=True,
                                        text=True, timeout=5)
                self.assertEqual(result.returncode, expected, result.stderr)
                self.assertEqual(result.stdout, "")
                self.assertNotIn("Traceback", result.stderr)
                self.assertNotIn("private input", result.stderr)


if __name__ == "__main__":
    unittest.main()
