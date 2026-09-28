import contextlib
from datetime import datetime, timezone
import hashlib
import io
import json
import os
from pathlib import Path
import socket
import ssl
import subprocess
import sys
import tempfile
from threading import Event
import time
from unittest.mock import MagicMock, patch
import urllib.error

import diagnostics
import notify
from providers import Notification, ProviderError, ntfy
from scripts import smoke_test
from tests.test_notify import OfflineTest, ROOT, VALUES


EVENT = {"type": "agent-turn-complete", "cwd": "/work/demo",
         "thread-id": "00000000-0000-4000-8000-000000000001", "turn-id": "turn-1"}


class DiagnosticTests(OfflineTest):
    def setUp(self):
        super().setUp()
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name) / "logs"
        patch("diagnostics.LOG_DIR", self.root).start()

    def rows(self):
        rows = []
        for path in sorted(self.root.glob("*.jsonl")):
            rows.extend(json.loads(line) for line in path.read_text(encoding="ascii").splitlines())
        return rows

    def invoke(self, *, event=EVENT, values=None, error=None, args=None, manual=False):
        stderr = io.StringIO()
        with patch("notify.read_env", return_value={}), \
                patch.dict(os.environ, VALUES if values is None else values, clear=True), \
                patch("notify.lookup_task_title", return_value="Private task label"), \
                patch("notify.ntfy.send_notification", return_value=200, side_effect=error) as send, \
                patch.object(sys, "argv", ["notify.py", *([json.dumps(event)] if args is None else args)]), \
                contextlib.redirect_stderr(stderr), contextlib.redirect_stdout(io.StringIO()):
            result = smoke_test.main() if manual else notify.main()
        return result, stderr.getvalue(), send.call_count

    def test_accepted_call_records_start_result_timing_and_no_content(self):
        event = dict(EVENT, **{"input-messages": ["PRIVATE PROMPT"],
                              "last-assistant-message": "PRIVATE REPLY", "unknown": "PRIVATE EXTRA"})
        self.assertEqual(self.invoke(event=event), (0, "", 1))
        start, end = self.rows()
        self.assertEqual((start["phase"], end["phase"]), ("started", "finished"))
        self.assertEqual(start["invocation"], end["invocation"])
        self.assertEqual(end["outcome"], "http_accepted")
        self.assertEqual(end["http_status"], 200)
        self.assertTrue(end["task_title_included"])
        self.assertEqual(end["timeout_ms"], 5000)
        self.assertGreaterEqual(end["elapsed_ms"], end["publish_ms"])
        self.assertIsNotNone(datetime.fromisoformat(end["time"]).tzinfo)
        text = json.dumps(self.rows(), ensure_ascii=False)
        for private in ("PRIVATE", "Private task label", VALUES["NTFY_TOPIC"],
                        VALUES["CODEX_NOTIFY_DEVICE"], "/work/demo", EVENT["thread-id"], EVENT["turn-id"]):
            self.assertNotIn(private, text)

    def test_repeat_event_is_correlated_but_distinct_invocations_are_preserved(self):
        self.invoke()
        self.invoke()
        first, second = [row for row in self.rows() if row["phase"] == "finished"]
        self.assertEqual(first["event_ref"], second["event_ref"])
        self.assertEqual(first["thread_ref"], hashlib.sha256(EVENT["thread-id"].encode()).hexdigest()[:24])
        self.assertNotEqual(first["invocation"], second["invocation"])
        self.invoke(event=dict(EVENT, **{"turn-id": "turn-2"}))
        self.assertNotEqual(first["event_ref"], self.rows()[-1]["event_ref"])

    def test_bad_or_missing_ids_never_log_raw_values_or_invent_event_identity(self):
        for value in (None, {}, "PRIVATE", "x" * 10000):
            self.invoke(event=dict(EVENT, **{"thread-id": value, "turn-id": value}))
        for row in self.rows():
            self.assertNotIn("thread_ref", row)
            self.assertNotIn("event_ref", row)
        self.assertNotIn("PRIVATE", json.dumps(self.rows()))
        self.invoke(event={"type": "agent-turn-complete", "thread-id": EVENT["thread-id"]})
        self.assertIn("thread_ref", self.rows()[-1])
        self.assertNotIn("event_ref", self.rows()[-1])

    def test_skip_input_and_configuration_outcomes_are_distinct(self):
        cases = [
            ({"event": {"type": "PRIVATE unsupported"}, "values": {}}, "unsupported_event", 0),
            ({"values": {"CODEX_NOTIFY_ENABLED": "0"}}, "disabled", 0),
            ({"values": dict(VALUES, CODEX_NOTIFY_PROJECT_ROOTS='["/elsewhere"]')}, "out_of_scope", 0),
            ({"values": {}}, "configuration_error", 2),
            ({"args": []}, "input_error", 2),
            ({"args": ["PRIVATE invalid JSON"]}, "input_error", 2),
        ]
        for kwargs, outcome, exit_code in cases:
            with self.subTest(outcome=outcome):
                result, _, sends = self.invoke(**kwargs)
                self.assertEqual((result, sends), (exit_code, 0))
                end = self.rows()[-1]
                self.assertEqual(end["outcome"], outcome)
                if outcome in ("disabled", "unsupported_event"):
                    self.assertNotIn("thread_ref", end)
                self.assertNotIn("publish_ms", end)
        self.assertNotIn("PRIVATE", json.dumps(self.rows()))

    def test_disabled_does_not_add_metadata_access_for_logging(self):
        with patch("diagnostics.Attempt.correlate", side_effect=AssertionError("no event metadata")), \
                patch("notify.build_notification", side_effect=AssertionError("no metadata")), \
                patch("notify.socket.gethostname", side_effect=AssertionError("no hostname")):
            self.assertEqual(self.invoke(values={"CODEX_NOTIFY_ENABLED": "0"}), (0, "", 0))
        self.assertEqual(self.rows()[-1]["outcome"], "disabled")

    def test_provider_failure_and_timeout_never_record_acceptance(self):
        for error in (ProviderError("safe HTTP failure", code="http_error", http_status=429),
                      ProviderError("safe timeout", code="delivery_timeout")):
            self.assertEqual(self.invoke(error=error)[::2], (0, 1))
            row = self.rows()[-1]
            self.assertEqual(row["outcome"], "provider_error")
            self.assertEqual(row["error_code"], error.code)
            self.assertEqual(row.get("http_status"), error.http_status)
            self.assertIn("publish_ms", row)

    def test_unexpected_failure_is_sanitized_and_best_effort(self):
        result, stderr, count = self.invoke(error=RuntimeError("PRIVATE token path response"))
        self.assertEqual((result, count), (0, 1))
        self.assertIn("unexpected internal error", stderr)
        self.assertNotIn("Traceback", stderr)
        self.assertNotIn("PRIVATE", stderr + json.dumps(self.rows()))
        self.assertEqual(self.rows()[-1]["outcome"], "internal_error")

    def test_manual_path_is_identified_without_bypassing_delivery(self):
        self.assertEqual(self.invoke(manual=True), (0, "", 1))
        self.assertEqual(self.rows()[-1]["source"], "manual")
        self.assertEqual(self.rows()[-1]["outcome"], "http_accepted")

    def test_missing_task_name_is_still_accepted_without_name_in_log(self):
        with patch("notify.lookup_task_title", return_value=None), \
                patch("notify.read_env", return_value={}), patch.dict(os.environ, VALUES, clear=True), \
                patch("notify.ntfy.send_notification", return_value=200) as send:
            self.assertEqual(notify.handle_event(EVENT), 0)
        send.assert_called_once()
        self.assertFalse(self.rows()[-1]["task_title_included"])

    def test_unwritable_logging_does_not_change_success_or_errors(self):
        with patch("diagnostics._append", side_effect=PermissionError("PRIVATE path")):
            self.assertEqual(self.invoke(), (0, "", 1))
            self.assertEqual(self.invoke(error=ProviderError("safe failure"))[::2], (0, 1))
            self.assertEqual(self.invoke(values={})[::2], (2, 0))
        self.assertFalse(self.root.exists())

    def test_stuck_disk_does_not_delay_sending_and_only_bounds_exit_wait(self):
        release = Event()
        entered = Event()
        finished = Event()

        def stuck(*args):
            entered.set()
            release.wait(3)

        original_writer = diagnostics._writer

        def writer(*args):
            try:
                original_writer(*args)
            finally:
                finished.set()

        def send(*args):
            self.assertFalse(release.is_set())
            return 200

        try:
            with patch("diagnostics._append", side_effect=stuck), patch("diagnostics._writer", side_effect=writer), \
                    patch("notify.read_env", return_value={}), patch.dict(os.environ, VALUES, clear=True), \
                    patch("notify.lookup_task_title", return_value=None), \
                    patch("notify.ntfy.send_notification", side_effect=send) as transport:
                start = time.monotonic()
                self.assertEqual(notify.handle_event(EVENT), 0)
                self.assertLess(time.monotonic() - start, 1)
                transport.assert_called_once()
                self.assertTrue(entered.is_set())
                release.set()
                self.assertTrue(finished.wait(2))
        finally:
            release.set()

    def test_logger_thread_start_failure_does_not_prevent_delivery(self):
        with patch("diagnostics.Thread.start", side_effect=RuntimeError("PRIVATE thread error")):
            self.assertEqual(self.invoke(), (0, "", 1))

    def test_only_allowlisted_codes_and_scalar_fields_can_be_written(self):
        with diagnostics.Attempt("PRIVATE SOURCE") as attempt:
            attempt.update(outcome={"PRIVATE": "value"}, error_code={"PRIVATE": "value"},
                           http_status="PRIVATE", task_title_included="PRIVATE",
                           publish_ms=float("inf"), timeout_ms=-1)
        end = self.rows()[-1]
        self.assertEqual((end["source"], end["outcome"], end["error_code"]),
                         ("hook", "internal_error", "internal_error"))
        for key in ("http_status", "task_title_included", "publish_ms", "timeout_ms"):
            self.assertNotIn(key, end)
        self.assertNotIn("PRIVATE", json.dumps(self.rows()))

    def test_configuration_error_identifies_setting_without_value(self):
        for key, code in (("CODEX_NOTIFY_ENABLED", "enabled_invalid"),
                          ("CODEX_NOTIFY_TASK_TITLE", "task_title_invalid"),
                          ("CODEX_NOTIFY_PROJECT_ROOTS", "scope_invalid"),
                          ("NTFY_SERVER", "server_invalid"),
                          ("CODEX_NOTIFY_TIMEOUT", "timeout_invalid")):
            with self.subTest(key=key):
                result, stderr, count = self.invoke(values=dict(VALUES, **{key: "PRIVATE invalid"}))
                self.assertEqual((result, count), (2, 0))
                self.assertEqual(self.rows()[-1]["error_code"], code)
                self.assertNotIn("PRIVATE", stderr + json.dumps(self.rows()))

    def test_real_unwritable_target_does_not_prevent_delivery(self):
        self.root.write_text("not a directory", encoding="utf-8")
        self.assertEqual(self.invoke(), (0, "", 1))
        self.assertEqual(self.root.read_text(), "not a directory")

    def test_retention_rotation_total_limit_and_unrelated_files(self):
        self.root.mkdir()
        unrelated = self.root / "notes.txt"
        unrelated.write_text("keep me", encoding="utf-8")
        (self.root / "2026-09-14.jsonl").write_bytes(b"old")
        (self.root / "2026-09-15.jsonl").write_bytes(b"within retention\n")
        now = datetime(2026, 9, 28, tzinfo=timezone.utc)
        with patch("diagnostics._utc_now", return_value=now), \
                patch("diagnostics.FILE_BYTES", 250), patch("diagnostics.TOTAL_BYTES", 600):
            for number in range(25):
                diagnostics._append(self.root, {"sequence": number, "padding": "x" * 80})
                self.assertLessEqual(sum(p.stat().st_size for p in self.root.glob("*.jsonl")), 600)
        self.assertFalse((self.root / "2026-09-14.jsonl").exists())
        self.assertTrue((self.root / "2026-09-15.jsonl").exists())
        self.assertTrue((self.root / "2026-09-28.1.jsonl").exists())
        self.assertEqual(unrelated.read_text(), "keep me")
        latest = (self.root / "2026-09-28.jsonl").read_text().splitlines()
        self.assertEqual(json.loads(latest[-1])["sequence"], 24)

    def test_size_pruning_removes_oldest_owned_file(self):
        self.root.mkdir()
        for day in (26, 27):
            (self.root / f"2026-09-{day}.jsonl").write_bytes(b"x" * 150)
        with patch("diagnostics._utc_now", return_value=datetime(2026, 9, 28, tzinfo=timezone.utc)), \
                patch("diagnostics.TOTAL_BYTES", 310):
            diagnostics._append(self.root, {"outcome": "http_accepted"})
        self.assertFalse((self.root / "2026-09-26.jsonl").exists())
        self.assertTrue((self.root / "2026-09-27.jsonl").exists())

    def test_interrupted_last_record_does_not_corrupt_later_records(self):
        self.root.mkdir()
        now = datetime(2026, 9, 28, tzinfo=timezone.utc)
        active = self.root / "2026-09-28.jsonl"
        active.write_bytes(b'{"sequence":1}\n{"sequence":')
        with patch("diagnostics._utc_now", return_value=now):
            diagnostics._append(self.root, {"sequence": 2})
        self.assertEqual(self.rows(), [{"sequence": 1}, {"sequence": 2}])
        active.write_bytes(b'{"sequence":')
        with patch("diagnostics._utc_now", return_value=now):
            diagnostics._append(self.root, {"sequence": 3})
        self.assertEqual(self.rows(), [{"sequence": 3}])

    def test_busy_process_lock_is_bounded_and_released_after_process_exit(self):
        self.root.mkdir()
        lock_path = self.root / "writer.lock"
        program = """
import diagnostics, os, sys, time
with os.fdopen(os.open(sys.argv[1], os.O_CREAT | os.O_RDWR, 0o600), 'r+b') as stream:
    assert diagnostics._lock(stream)
    print('locked', flush=True)
    sys.stdin.readline()
"""
        process = subprocess.Popen([sys.executable, "-B", "-c", program, str(lock_path)], cwd=ROOT,
                                   stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
        try:
            self.assertEqual(process.stdout.readline().strip(), "locked")
            start = time.monotonic()
            diagnostics._append(self.root, {"phase": "started"})
            self.assertLess(time.monotonic() - start, 1)
            self.assertEqual(self.rows(), [])
        finally:
            # Abrupt exit must not leave a permanent stale lock.
            process.kill()
            process.communicate(timeout=5)
        diagnostics._append(self.root, {"phase": "started"})
        self.assertEqual(len(self.rows()), 1)

    def test_multiple_processes_write_parseable_records_with_rotation(self):
        program = """
import diagnostics, pathlib, sys
diagnostics.FILE_BYTES = 512
diagnostics.TOTAL_BYTES = 4096
for i in range(6):
    diagnostics._append(pathlib.Path(sys.argv[1]), {'writer': sys.argv[2], 'sequence': i})
"""
        processes = [subprocess.Popen([sys.executable, "-B", "-c", program, str(self.root), str(i)],
                                     cwd=ROOT, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
                     for i in range(6)]
        try:
            for process in processes:
                output, error = process.communicate(timeout=15)
                self.assertEqual((process.returncode, output, error), (0, "", ""))
        finally:
            for process in processes:
                if process.poll() is None:
                    process.kill()
                    process.communicate(timeout=5)
        rows = self.rows()
        self.assertGreater(len(rows), 0)
        self.assertEqual(len({(row["writer"], row["sequence"]) for row in rows}), len(rows))
        self.assertLessEqual(sum(p.stat().st_size for p in self.root.glob("*.jsonl")), 4096)
        self.assertTrue(all(p.stat().st_size <= 512 for p in self.root.glob("*.jsonl")))

    def test_multiple_processes_preserve_all_records_without_rotation(self):
        program = """
import diagnostics, pathlib, sys
for i in range(6):
    diagnostics._append(pathlib.Path(sys.argv[1]), {'writer': sys.argv[2], 'sequence': i})
"""
        processes = [subprocess.Popen([sys.executable, "-B", "-c", program, str(self.root), str(i)],
                                     cwd=ROOT, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
                     for i in range(6)]
        try:
            for process in processes:
                output, error = process.communicate(timeout=15)
                self.assertEqual((process.returncode, output, error), (0, "", ""))
        finally:
            for process in processes:
                if process.poll() is None:
                    process.kill()
                    process.communicate(timeout=5)
        rows = self.rows()
        self.assertEqual(len(rows), 36)
        self.assertEqual(len({(row["writer"], row["sequence"]) for row in rows}), 36)

    def test_daemon_logging_does_not_hold_subprocess_open(self):
        program = """
import diagnostics, time
diagnostics._append = lambda *a: time.sleep(60)
with diagnostics.Attempt() as attempt:
    attempt.update(outcome='disabled')
"""
        subprocess.run([sys.executable, "-B", "-c", program], cwd=ROOT,
                       check=True, capture_output=True, timeout=5)


class TransportDiagnosticTests(OfflineTest):
    def test_http_cleanup_failure_keeps_safe_http_diagnostic(self):
        body = MagicMock()
        body.close.side_effect = RuntimeError("PRIVATE response cleanup")
        failure = urllib.error.HTTPError("https://private.invalid", 429, "PRIVATE", {}, body)
        with patch("threading.excepthook") as thread_error, self.assertRaises(ProviderError) as raised:
            ntfy.send_notification(ntfy.load_config(VALUES), Notification("title", "body"),
                                   opener=MagicMock(side_effect=failure))
        self.assertEqual((raised.exception.code, raised.exception.http_status), ("http_error", 429))
        self.assertNotIn("PRIVATE", str(raised.exception))
        thread_error.assert_not_called()

    def test_specific_network_categories_without_raw_error_details(self):
        config = ntfy.load_config(VALUES)
        failures = [(socket.gaierror("PRIVATE DNS"), "dns_error"),
                    (ssl.SSLError("PRIVATE TLS"), "tls_error"),
                    (TimeoutError("PRIVATE timeout"), "socket_timeout"),
                    (OSError("PRIVATE OS error"), "network_error")]
        for error, code in failures:
            for cause in (error, urllib.error.URLError(error)):
                with self.subTest(code=code), self.assertRaises(ProviderError) as raised:
                    ntfy.send_notification(config, Notification("title", "body"),
                                           opener=MagicMock(side_effect=cause))
                self.assertEqual(raised.exception.code, code)
                self.assertNotIn("PRIVATE", str(raised.exception))

    def test_http_status_is_preserved_for_accepted_and_rejected_requests(self):
        config = ntfy.load_config(VALUES)
        response = MagicMock()
        response.__enter__.return_value = response
        response.status = 202
        self.assertEqual(ntfy.send_notification(config, Notification("title", "body"),
                                               opener=lambda *a, **k: response), 202)
        failure = urllib.error.HTTPError("https://private.invalid", 429, "PRIVATE", {}, io.BytesIO())
        with self.assertRaises(ProviderError) as raised:
            ntfy.send_notification(config, Notification("title", "body"), opener=MagicMock(side_effect=failure))
        self.assertEqual((raised.exception.code, raised.exception.http_status), ("http_error", 429))
