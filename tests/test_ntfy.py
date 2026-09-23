import contextlib
import http.client
import io
import json
from pathlib import Path
import subprocess
import sys
from threading import Event
import time
import unittest
from unittest.mock import MagicMock, patch
import urllib.error
import urllib.request

import notify
from providers import ConfigurationError, Notification, ProviderError
from providers import ntfy
from tests.test_notify import OfflineTest, VALUES


class ProviderConfigTests(OfflineTest):
    def test_https_origin_and_token(self):
        config = ntfy.load_config(dict(VALUES, NTFY_SERVER="https://push.example.invalid:8443/",
                                       NTFY_TOKEN="fake-token-fixture"))
        self.assertEqual(config.server, "https://push.example.invalid:8443")
        self.assertEqual(config.token, "fake-token-fixture")

    def test_rejects_unsafe_server_without_echo(self):
        for server in ("http://example.invalid", "https://user:private@example.invalid", "https://",
                       "https://example.invalid/path", "https://example.invalid/?private=query",
                       "https://example.invalid/#private", "https://example.invalid:bad",
                       "https://example.invalid:0", "https://example.invalid:99999", "https://[",
                       "https://example.invalid\n/private", "https://example.invalid\\private",
                       "https://example.invalid/?", "https://中文.invalid"):
            with self.subTest(server=server), self.assertRaises(ConfigurationError) as raised:
                ntfy.load_config(dict(VALUES, NTFY_SERVER=server))
            self.assertNotIn("private", str(raised.exception))

    def test_topic_validation(self):
        for topic in ("", "a/b", "a?b", "a#b", "a b", "中文", "x" * 65, "a\r\nb"):
            with self.subTest(topic=topic), self.assertRaises(ConfigurationError):
                ntfy.load_config(dict(VALUES, NTFY_TOPIC=topic))
        self.assertEqual(ntfy.load_config(dict(VALUES, NTFY_TOPIC="A_a-9")).topic, "A_a-9")

    def test_token_cannot_inject_headers(self):
        for token in ("private\r\nX-Header: yes", "private token", "中文", "a\x00b"):
            with self.subTest(token=token), self.assertRaises(ConfigurationError) as raised:
                ntfy.load_config(dict(VALUES, NTFY_TOKEN=token))
            self.assertNotIn("private", str(raised.exception))

    def test_timeout_is_finite_and_bounded(self):
        for timeout in ("nan", "inf", "-inf", "0", "-1", "31", "invalid", ""):
            with self.subTest(timeout=timeout), self.assertRaises(ConfigurationError):
                ntfy.load_config(dict(VALUES, CODEX_NOTIFY_TIMEOUT=timeout))
        self.assertEqual(ntfy.load_config(dict(VALUES, CODEX_NOTIFY_TIMEOUT="0.1")).timeout, 0.1)


class HttpTests(OfflineTest):
    def setUp(self):
        super().setUp()
        self.config = ntfy.load_config(VALUES)
        self.notification = Notification("项目 · 本轮已结束", "设备：测试电脑\n项目：项目\n状态：本轮已结束")

    def response(self, status=200):
        response = MagicMock()
        response.__enter__.return_value = response
        response.status = status
        return response

    def test_json_request_unicode_headers_timeout_and_one_publish(self):
        response = self.response()
        opener = MagicMock(return_value=response)
        ntfy.send_notification(self.config, self.notification, opener=opener)
        opener.assert_called_once()
        request = opener.call_args.args[0]
        self.assertEqual(request.full_url, "https://ntfy.sh/")
        self.assertEqual(request.get_method(), "POST")
        self.assertEqual(request.get_header("Content-type"), "application/json; charset=utf-8")
        self.assertIsNone(request.get_header("Authorization"))
        self.assertEqual(opener.call_args.kwargs, {"timeout": 5})
        self.assertEqual(json.loads(request.data), {
            "topic": VALUES["NTFY_TOPIC"], "title": self.notification.title,
            "message": self.notification.message, "priority": 3,
            "tags": ["computer"],
        })
        self.assertIn("测试电脑".encode("utf-8"), request.data)
        response.read.assert_not_called()
        response.__exit__.assert_called_once()

    def test_optional_bearer_header(self):
        config = ntfy.load_config(dict(VALUES, NTFY_TOKEN="fake-token-fixture"))
        request = ntfy.build_request(config, self.notification)
        self.assertEqual(request.get_header("Authorization"), "Bearer fake-token-fixture")
        self.assertNotIn(b"fake-token-fixture", request.data)
        self.assertNotIn(config.topic, request.full_url)

    def test_socket_timeout_tls_dns_disconnect_errors_are_safe(self):
        for failure in (TimeoutError("private"), OSError("private"),
                        urllib.error.URLError("https://private:token@example.invalid/topic"),
                        http.client.BadStatusLine("private"), ValueError("private proxy URL")):
            with self.subTest(failure=type(failure).__name__):
                opener = MagicMock(side_effect=failure)
                with self.assertRaises(ProviderError) as raised:
                    ntfy.send_notification(self.config, self.notification, opener=opener)
                self.assertNotIn("private", str(raised.exception))
                opener.assert_called_once()

    def test_http_rejections_and_redirects_are_safe_and_closed(self):
        for code in (301, 302, 307, 308, 401, 403, 429, 500):
            body = io.BytesIO(b"private server error body")
            failure = urllib.error.HTTPError("https://private.invalid/topic", code,
                                             "private response", {}, body)
            with self.subTest(code=code), self.assertRaises(ProviderError) as raised:
                ntfy.send_notification(self.config, self.notification, opener=MagicMock(side_effect=failure))
            self.assertIn(str(code), str(raised.exception))
            self.assertNotIn("private", str(raised.exception))
            self.assertTrue(body.closed)

    def test_non_success_status_without_httperror(self):
        with self.assertRaisesRegex(ProviderError, "non-success"):
            ntfy.send_notification(self.config, self.notification, opener=lambda *a, **k: self.response(400))

    def test_real_opener_installs_no_redirect_handler(self):
        with patch("urllib.request.build_opener") as build:
            build.return_value.open.return_value = self.response()
            ntfy.send_notification(self.config, self.notification)
        self.assertIsInstance(build.call_args.args[0], ntfy._NoRedirect)

    def test_redirect_handler_never_follows_even_same_host_or_https(self):
        handler = ntfy._NoRedirect()
        for code in (301, 302, 303, 307, 308):
            for destination in ("http://example.invalid/", "https://ntfy.sh/other", "https://other.invalid/"):
                request = ntfy.build_request(self.config, self.notification)
                self.assertIsNone(handler.redirect_request(request, None, code, "", {}, destination))

    def test_overall_deadline_bounds_a_stuck_resolver_or_slow_headers(self):
        release = Event()
        started = Event()
        finished = Event()
        config = ntfy.load_config(dict(VALUES, CODEX_NOTIFY_TIMEOUT="0.1"))

        def stuck(*args, **kwargs):
            started.set()
            release.wait(3)
            finished.set()
            return self.response()

        beginning = time.monotonic()
        try:
            with self.assertRaisesRegex(ProviderError, "status is unknown"):
                ntfy.send_notification(config, self.notification, opener=stuck)
            self.assertTrue(started.is_set())
            self.assertLess(time.monotonic() - beginning, 1.5)
        finally:
            release.set()
            self.assertTrue(finished.wait(2))

    def test_daemon_worker_does_not_hold_cli_process_open(self):
        program = """
import time
from providers import ntfy, Notification, ProviderError
config = ntfy.load_config({'NTFY_TOPIC': 'unit-test-topic-not-a-secret', 'CODEX_NOTIFY_TIMEOUT': '0.05'})
try:
    ntfy.send_notification(config, Notification('title', 'body'), opener=lambda *a, **k: time.sleep(60))
except ProviderError:
    pass
"""
        subprocess.run([sys.executable, "-c", program], cwd=Path(__file__).resolve().parents[1],
                       check=True, capture_output=True, timeout=5)

    def test_oversized_envelope_never_opens_network(self):
        opener = MagicMock()
        with self.assertRaisesRegex(ProviderError, "4096"):
            ntfy.send_notification(self.config, Notification("title", "字" * 4096), opener=opener)
        opener.assert_not_called()

    def test_maximum_unicode_configuration_fits_envelope(self):
        with patch("notify.read_env", return_value={}):
            config = notify.load_config(dict(VALUES, CODEX_NOTIFY_DEVICE="😀" * 200,
                                            NTFY_TOPIC="x" * 64))
        notification = notify.build_notification({"cwd": "/work/" + "😀" * 200,
                                                 "last-assistant-message": "😀" * 1000}, config)
        request = ntfy.build_request(config.ntfy, notification)
        self.assertLessEqual(len(request.data), 4096)

    def test_core_to_http_end_to_end_without_network(self):
        response = self.response()
        with patch("notify.read_env", return_value={}), \
                patch.dict("os.environ", VALUES, clear=True), \
                patch.object(sys, "argv", ["notify.py", json.dumps({
                    "type": "agent-turn-complete", "cwd": "C:/work/demo",
                    "input-messages": ["private prompt"], "last-assistant-message": "完成 ✅",
                })]), patch("urllib.request.build_opener") as build:
            build.return_value.open.return_value = response
            self.assertEqual(notify.main(), 0)
        build.return_value.open.assert_called_once()
        body = json.loads(build.return_value.open.call_args.args[0].data)
        self.assertNotIn("完成 ✅", body["message"])
        self.assertEqual(body["message"], "设备：测试电脑\n项目：demo\n状态：本轮已结束")
        self.assertNotIn("private prompt", body["message"])
        self.assertNotIn("C:/work", body["message"])

    def test_legacy_preview_settings_never_export_conversation_content(self):
        event = {
            "type": "agent-turn-complete", "cwd": "/work/demo",
            "thread-id": "00000000-0000-4000-8000-000000000001",
            "input-messages": ["PRIVATE INPUT"],
            "last-assistant-message": '{"password":"PRIVATE REPLY"}',
            "unknown": "PRIVATE EXTRA",
        }
        for named in (False, True):
            for failure in (False, True):
                with self.subTest(named=named, failure=failure):
                    stdout, stderr = io.StringIO(), io.StringIO()
                    environment = dict(VALUES, CODEX_NOTIFY_TASK_TITLE=str(int(named)),
                                       CODEX_NOTIFY_SUMMARY_MAX="invalid-old-value")
                    with patch("notify.read_env", return_value={"CODEX_NOTIFY_SUMMARY_MAX": "500"}), \
                            patch.dict("os.environ", environment, clear=True), \
                            patch("notify.lookup_task_title", return_value="测试任务"), \
                            patch.object(sys, "argv", ["notify.py", json.dumps(event)]), \
                            patch("urllib.request.build_opener") as build, \
                            contextlib.redirect_stdout(stdout), contextlib.redirect_stderr(stderr):
                        if failure:
                            build.return_value.open.side_effect = OSError("PRIVATE ERROR")
                        else:
                            build.return_value.open.return_value = self.response()
                        self.assertEqual(notify.main(), 0)
                    build.return_value.open.assert_called_once()
                    request = build.return_value.open.call_args.args[0]
                    body = json.loads(request.data)
                    expected = "设备：测试电脑\n项目：demo\n"
                    if named:
                        expected += "任务：测试任务\n"
                    self.assertEqual(body["message"], expected + "状态：本轮已结束")
                    self.assertEqual(set(body), {"topic", "title", "message", "priority", "tags"})
                    self.assertNotIn("PRIVATE", request.data.decode("utf-8") +
                                     str(request.header_items()) + stdout.getvalue() + stderr.getvalue())


if __name__ == "__main__":
    unittest.main()
