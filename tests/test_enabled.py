import contextlib
import io
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
from unittest.mock import patch

import notify
from providers import ProviderError
from scripts import smoke_test
from tests.test_notify import OfflineTest, ROOT, VALUES


class EnabledTests(OfflineTest):
    def setUp(self):
        super().setUp()
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.path = Path(temporary.name) / '.env'
        patch('notify.ENV_PATH', self.path).start()

    def invoke(self, values, *, smoke=False, raw=None):
        output, error = io.StringIO(), io.StringIO()
        event = json.dumps({'type': 'agent-turn-complete', 'cwd': '/work/demo'})
        with patch.dict(os.environ, values, clear=True), \
                patch.object(sys, 'argv', ['notify.py', event if raw is None else raw]), \
                patch('notify.ntfy.send_notification') as send, \
                contextlib.redirect_stdout(output), contextlib.redirect_stderr(error):
            result = smoke_test.main() if smoke else notify.main()
        return result, output.getvalue(), error.getvalue(), send

    def test_absent_and_explicit_enabled_preserve_delivery(self):
        for extra in ({}, {'CODEX_NOTIFY_ENABLED': '1'}, {'CODEX_NOTIFY_ENABLED': ' 1 '}):
            with self.subTest(extra=extra):
                result, output, error, send = self.invoke(dict(VALUES, **extra))
                self.assertEqual((result, output, error), (0, '', ''))
                send.assert_called_once()

    def test_disabled_skips_all_delivery_settings_and_metadata(self):
        for topic in (None, '', 'replace-with-a-long-random-private-topic'):
            values = {'CODEX_NOTIFY_ENABLED': '0', 'CODEX_NOTIFY_PROVIDER': 'invalid',
                      'CODEX_NOTIFY_TASK_TITLE': 'invalid', 'CODEX_NOTIFY_TIMEOUT': 'invalid',
                      'CODEX_NOTIFY_PROJECT_ROOTS': 'invalid', 'NTFY_SERVER': 'invalid'}
            if topic is not None:
                values['NTFY_TOPIC'] = topic
            with self.subTest(topic=topic), \
                    patch('notify.ntfy.load_config', side_effect=AssertionError('no provider config')), \
                    patch('notify.socket.gethostname', side_effect=AssertionError('no hostname')), \
                    patch('notify.build_notification', side_effect=AssertionError('no metadata')):
                result, output, error, send = self.invoke(values)
            self.assertEqual((result, output, error), (0, '', ''))
            send.assert_not_called()

    def test_switch_environment_precedence_in_both_directions(self):
        for file_value, env_value, count in (('0', None, 0), ('1', None, 1),
                                              ('0', '1', 1), ('1', '0', 0)):
            self.path.write_text(f'CODEX_NOTIFY_ENABLED={file_value}\n', encoding='utf-8')
            values = dict(VALUES)
            if env_value is not None:
                values['CODEX_NOTIFY_ENABLED'] = env_value
            with self.subTest(file=file_value, env=env_value):
                result, output, error, send = self.invoke(values)
                self.assertEqual((result, output, error), (0, '', ''))
                self.assertEqual(send.call_count, count)

    def test_file_changes_apply_next_invocation_without_replay(self):
        for enabled, count in (('0', 0), ('1', 1), ('0', 0)):
            self.path.write_text(f'CODEX_NOTIFY_ENABLED={enabled}\n', encoding='utf-8')
            result, _, error, send = self.invoke(VALUES)
            self.assertEqual((result, error, send.call_count), (0, '', count))

    def test_invalid_switch_is_safe_error_including_empty_override(self):
        self.path.write_text('CODEX_NOTIFY_ENABLED=0\n', encoding='utf-8')
        for value in ('', 'true', 'false', '2', 'private-fixture'):
            with self.subTest(value=value):
                result, output, error, send = self.invoke(dict(VALUES, CODEX_NOTIFY_ENABLED=value))
                self.assertEqual((result, output), (2, ''))
                self.assertIn('CODEX_NOTIFY_ENABLED must be 0 or 1', error)
                self.assertNotIn('private-fixture', error)
                send.assert_not_called()

    def test_disabled_still_requires_valid_dotenv_syntax(self):
        self.path.write_text('private broken config\n', encoding='utf-8')
        result, _, error, send = self.invoke({'CODEX_NOTIFY_ENABLED': '0'})
        self.assertEqual(result, 2)
        self.assertIn('invalid .env syntax', error)
        self.assertNotIn('private', error)
        send.assert_not_called()

    def test_input_validation_and_unsupported_event_order_unchanged(self):
        result, _, error, send = self.invoke({'CODEX_NOTIFY_ENABLED': '0'}, raw='private invalid json')
        self.assertEqual(result, 2)
        self.assertNotIn('private', error)
        send.assert_not_called()
        with patch('notify.load_config', side_effect=AssertionError('unsupported must skip')):
            result, output, error, send = self.invoke({'CODEX_NOTIFY_ENABLED': 'invalid'}, raw='{"type":"other"}')
        self.assertEqual((result, output, error), (0, '', ''))
        send.assert_not_called()

    def test_smoke_disabled_reports_no_send_without_topic(self):
        with patch('notify.lookup_task_title', side_effect=AssertionError('no index read')):
            result, output, error, send = self.invoke({'CODEX_NOTIFY_ENABLED': '0'}, smoke=True)
        self.assertEqual((result, error), (0, ''))
        self.assertIn('disabled', output)
        self.assertIn('nothing was sent', output)
        send.assert_not_called()

    def test_smoke_enabled_uses_normal_sender_once(self):
        with patch.object(smoke_test, 'ROOT', ROOT.parent / 'demo'):
            result, output, error, send = self.invoke(VALUES, smoke=True)
        self.assertEqual((result, error), (0, ''))
        self.assertNotIn('nothing was sent', output)
        send.assert_called_once()
        self.assertIn('项目：demo', send.call_args.args[1].message)

    def test_smoke_scope_skip_is_explicit(self):
        values = dict(VALUES, CODEX_NOTIFY_PROJECT_ROOTS='["/scope/fixture-only"]')
        result, output, error, send = self.invoke(values, smoke=True)
        self.assertEqual((result, error), (0, ''))
        self.assertIn('outside the configured project scope', output)
        self.assertIn('nothing was sent', output)
        send.assert_not_called()

    def test_smoke_preserves_config_and_provider_failure_behavior(self):
        result, _, error, send = self.invoke({}, smoke=True)
        self.assertEqual(result, 2)
        self.assertIn('NTFY_TOPIC is required', error)
        send.assert_not_called()
        with patch('notify.send_notification', side_effect=ProviderError('ntfy delivery timed out')):
            result, _, error, _ = self.invoke(VALUES, smoke=True)
        self.assertEqual(result, 0)
        self.assertIn('timed out', error)

    def test_smoke_script_from_other_directory_with_no_topic(self):
        with tempfile.TemporaryDirectory(prefix='notify smoke 中文 ') as directory:
            clone = Path(directory)
            for name in ('notify.py', 'task_metadata.py'):
                shutil.copyfile(ROOT / name, clone / name)
            shutil.copytree(ROOT / 'providers', clone / 'providers', ignore=shutil.ignore_patterns('__pycache__'))
            (clone / 'scripts').mkdir()
            shutil.copyfile(ROOT / 'scripts/smoke_test.py', clone / 'scripts/smoke_test.py')
            env = dict(os.environ, CODEX_NOTIFY_ENABLED='0', PYTHONIOENCODING='utf-8')
            env.pop('NTFY_TOPIC', None)
            run = subprocess.run([sys.executable, str(clone / 'scripts/smoke_test.py')],
                                 cwd=ROOT.parent, env=env, capture_output=True, encoding='utf-8', timeout=10)
        self.assertEqual((run.returncode, run.stderr), (0, ''))
        self.assertIn('disabled', run.stdout)
        self.assertIn('nothing was sent', run.stdout)
