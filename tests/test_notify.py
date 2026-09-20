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
        self.assertEqual(notification.title, "demo · 本轮已完成")
        self.assertEqual(notification.message, "Device: 测试电脑\nProject: demo\nStatus: turn completed\nSummary: Finished tests.")

    def test_unsupported_events_do_not_load_config_or_publish(self):
        for kind in ("approval-requested", "other", None, [], {}):
            with self.subTest(kind=kind):
                with patch("notify.load_config", side_effect=AssertionError("must not load")):
                    result, error, send = self.invoke([json.dumps({"type": kind})], {})
                self.assertEqual((result, error), (0, ""))
                send.assert_not_called()

    def test_missing_and_extra_arguments(self):
        for args in ([], ["{}", "{}"]):
            result, error, send = self.invoke(args)
            self.assertEqual(result, 2)
            self.assertIn("one event JSON", error)
            send.assert_not_called()

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
        self.assertIn(notify.DEFAULT_SUMMARY, send.call_args.args[1].message)

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
        self.assertEqual(config.summary_max, 300)
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
        self.path.write_text("NTFY_TOPIC=file-fixture\nCODEX_NOTIFY_DEVICE=file-host\nCODEX_NOTIFY_SUMMARY_MAX=123\n", encoding="utf-8")
        values = dict(VALUES)
        config = self.load(values)
        self.assertEqual(config.ntfy.topic, VALUES["NTFY_TOPIC"])
        self.assertEqual(config.device, "测试电脑")
        self.assertEqual(config.summary_max, 123)
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

    def test_summary_limit_validation(self):
        for value in ("-1", "501", "3.5", "nan", "", "9" * 5000):
            with self.subTest(value=value[:20]), self.assertRaises(ConfigurationError):
                self.load(dict(VALUES, CODEX_NOTIFY_SUMMARY_MAX=value))
        self.assertEqual(self.load(dict(VALUES, CODEX_NOTIFY_SUMMARY_MAX="0")).summary_max, 0)

    def test_example_is_not_a_working_topic(self):
        with self.assertRaisesRegex(ConfigurationError, "template"):
            notify.load_config({}, ROOT / ".env.example")

    def test_default_env_is_next_to_script_not_cwd(self):
        with patch("notify.read_env", return_value={}) as read:
            notify.load_config(VALUES)
        read.assert_called_once_with(ROOT / ".env")


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
        self.assertEqual(notify.build_summary("  已完成\n\t测试　✅  👩‍💻 ", 300), "已完成 测试 ✅ 👩‍💻")

    def test_controls_surrogates_and_bidi_are_removed(self):
        self.assertEqual(notify.build_summary("done\x00\ud800\u202eevil", 300), "doneevil")

    def test_normalization_cannot_reintroduce_sensitive_content(self):
        for text in ("pass\x00word=fixture", "https:\u202e//private.invalid/", "secret-fi\x00xture"):
            self.assertEqual(notify.build_summary(text, 300, ("secret-fixture",)), notify.DEFAULT_SUMMARY)

    def test_truncation_and_minimum_limit(self):
        self.assertEqual(notify.build_summary("你好世界完成", 4), "你好世…")
        self.assertEqual(notify.build_summary("abcd", 4), "abcd")
        self.assertEqual(notify.build_summary("abcd", 1), "…")

    def test_non_string_empty_and_disabled_summaries(self):
        for value in (None, {}, [], 12, "", " \t\n "):
            self.assertEqual(notify.build_summary(value, 300), notify.DEFAULT_SUMMARY)
        self.assertEqual(notify.build_summary("private business prose", 0), notify.DEFAULT_SUMMARY)

    def test_obvious_paths_code_and_credentials_use_generic_summary(self):
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
                self.assertEqual(notify.build_summary(value, 300), notify.DEFAULT_SUMMARY)

    def test_prompts_thread_ids_unknown_fields_and_full_response_are_not_sent(self):
        with patch("notify.read_env", return_value={}):
            config = notify.load_config(VALUES)
        event = {"cwd": "/private/home/demo", "input-messages": ["PRIVATE USER PROMPT"],
                 "thread-id": "PRIVATE THREAD", "turn-id": "PRIVATE TURN", "unknown": "PRIVATE UNKNOWN",
                 "last-assistant-message": "Completed " + "x" * 400 + "PRIVATE TAIL"}
        notification = notify.build_notification(event, config)
        outgoing = notification.title + notification.message
        self.assertNotIn("PRIVATE", outgoing)
        self.assertNotIn("/private/home", outgoing)
        self.assertIn("Project: demo", outgoing)
        self.assertTrue(notification.message.endswith("…"))

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
