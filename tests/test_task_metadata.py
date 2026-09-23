from dataclasses import replace
import json
from pathlib import Path
import tempfile
from unittest.mock import patch

import notify
from providers import ntfy
from task_metadata import lookup_task_title
from tests.test_notify import OfflineTest, VALUES


THREAD = "00000000-0000-4000-8000-000000000001"
OTHER = "00000000-0000-4000-8000-000000000002"
EVENT = {"thread-id": THREAD, "cwd": "/work/demo"}


class MetadataTests(OfflineTest):
    def setUp(self):
        super().setUp()
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.home = Path(temporary.name)
        self.index = self.home / "session_index.jsonl"

    def write(self, rows):
        self.index.write_text("\n".join(json.dumps(row, ensure_ascii=False) for row in rows) + "\n",
                              encoding="utf-8")

    def test_exact_matching_thread_and_latest_rename(self):
        self.write([{"id": THREAD, "thread_name": "Old name"},
                    {"id": OTHER, "thread_name": "Other private task"},
                    {"id": THREAD, "thread_name": "修复登录问题"}])
        before = self.index.read_bytes()
        self.assertEqual(lookup_task_title(EVENT, self.home), "修复登录问题")
        self.assertEqual(self.index.read_bytes(), before)

    def test_no_match_does_not_use_another_tasks_name(self):
        self.write([{"id": OTHER, "thread_name": "Another private task"}])
        self.assertIsNone(lookup_task_title(EVENT, self.home))

    def test_invalid_or_missing_id_never_reads_index(self):
        for identifier in (None, {}, "not-an-id", "../private", "0" * 1000):
            with patch.object(Path, "is_file", side_effect=AssertionError("must not access disk")):
                self.assertIsNone(lookup_task_title({"thread-id": identifier}, self.home))

    def test_missing_index_and_directory_are_safe(self):
        self.assertIsNone(lookup_task_title(EVENT, self.home))
        self.index.mkdir()
        self.assertIsNone(lookup_task_title(EVENT, self.home))

    def test_unreadable_index_is_optional(self):
        self.write([])
        with patch.object(Path, "open", side_effect=PermissionError("private path")):
            self.assertIsNone(lookup_task_title(EVENT, self.home))

    def test_invalid_json_unicode_partial_records_and_schema_are_skipped(self):
        self.write([{"id": THREAD, "thread_name": "Target task"}])
        with self.index.open("ab") as stream:
            stream.write(b'\xff\n[]\nnull\n{"unknown":"value"}\n{"id":')
        self.assertEqual(lookup_task_title(EVENT, self.home), "Target task")

    def test_empty_latest_title_does_not_resurrect_old_title(self):
        for title in (None, "", "  ", {}, 3):
            self.write([{"id": THREAD, "thread_name": "Old private title"},
                        {"id": THREAD, "thread_name": title}])
            self.assertIsNone(lookup_task_title(EVENT, self.home))

    def test_bounded_tail_reads_recent_records(self):
        self.index.write_bytes(b"x" * 3000 + b"\n" + json.dumps({
            "id": THREAD, "thread_name": "Recent title",
        }).encode() + b"\n")
        with patch("task_metadata.MAX_INDEX_BYTES", 256):
            self.assertEqual(lookup_task_title(EVENT, self.home), "Recent title")

    def test_old_record_outside_tail_is_not_used(self):
        self.write([{"id": THREAD, "thread_name": "Old title"}])
        with self.index.open("ab") as stream:
            stream.write(b"x" * 3000 + b"\n")
        with patch("task_metadata.MAX_INDEX_BYTES", 256):
            self.assertIsNone(lookup_task_title(EVENT, self.home))

    def test_custom_codex_home(self):
        self.write([{"id": THREAD, "thread_name": "Custom home title"}])
        with patch.dict("os.environ", {"CODEX_HOME": str(self.home)}):
            self.assertEqual(lookup_task_title(EVENT), "Custom home title")


class NotificationTitleTests(OfflineTest):
    def setUp(self):
        super().setUp()
        with patch("notify.read_env", return_value={}):
            self.config = notify.load_config(dict(VALUES, CODEX_NOTIFY_TASK_TITLE="1"))

    def test_named_task_and_project_without_reply_content(self):
        with patch("notify.lookup_task_title", return_value="修复登录问题") as lookup:
            result = notify.build_notification(EVENT, self.config)
        lookup.assert_called_once_with(EVENT)
        self.assertEqual(result.title, "demo · 修复登录问题 · 本轮已结束")
        self.assertIn("设备：测试电脑", result.message)
        self.assertIn("任务：修复登录问题", result.message)
        self.assertIn("状态：本轮已结束", result.message)
        self.assertNotIn("回复预览：", result.message)
        self.assertNotIn(THREAD, result.title + result.message)

    def test_disabled_title_never_reads_local_index(self):
        with patch("notify.lookup_task_title", side_effect=AssertionError("disabled")):
            result = notify.build_notification(EVENT, replace(self.config, task_title=False))
        self.assertEqual(result.title, "demo · 本轮已结束")

    def test_missing_task_never_falls_back_to_user_prompt(self):
        event = dict(EVENT, **{"input-messages": ["PRIVATE PROMPT"],
                              "last-assistant-message": "PRIVATE ANSWER"})
        with patch("notify.lookup_task_title", return_value=None):
            result = notify.build_notification(event, self.config)
        self.assertEqual(result.title, "demo · 本轮已结束")
        self.assertNotIn("PRIVATE", result.title + result.message)
        self.assertNotIn("任务：", result.message)

    def test_projectless_task_and_fully_generic_fallback(self):
        with patch("notify.project_name", return_value="unknown-project"), \
                patch("notify.lookup_task_title", return_value="整理通知设置"):
            self.assertEqual(notify.build_notification(EVENT, self.config).title, "整理通知设置 · 本轮已结束")
        with patch("notify.project_name", return_value="unknown-project"), \
                patch("notify.lookup_task_title", return_value=None):
            self.assertEqual(notify.build_notification(EVENT, self.config).title, "Codex · 本轮已结束")

    def test_title_privacy_filter_runs_before_truncation(self):
        for title in ("token=fixture", "/Users/private/secret", "Code `secret()`", VALUES["NTFY_TOPIC"],
                      "x" * 100 + " password=private", "https://private.invalid", "\x00\u202e"):
            with patch("notify.lookup_task_title", return_value=title):
                result = notify.build_notification(EVENT, self.config)
            self.assertEqual(result.title, "demo · 本轮已结束")
            self.assertNotIn("任务：", result.message)

    def test_long_title_unicode_normalization_and_size(self):
        config = replace(self.config, device="😀" * 200)
        event = dict(EVENT, cwd="/work/" + "😀" * 200,
                     **{"last-assistant-message": "😀" * 1000})
        with patch("notify.lookup_task_title", return_value=" \n " + "😀" * 200):
            result = notify.build_notification(event, config)
        self.assertIn("任务：" + "😀" * 79 + "…", result.message)
        self.assertLessEqual(len(ntfy.build_request(config.ntfy, result).data), 4096)
