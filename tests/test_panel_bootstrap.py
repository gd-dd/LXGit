"""Offscreen bootstrap error-state tests; no executable or user repository is run."""
import json
import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import Mock, patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
os.environ['QT_QPA_PLATFORM'] = 'offscreen'
from PySide6.QtCore import QByteArray, QProcess
from PySide6.QtWidgets import QApplication, QWidget
from ui import panel as panel_module


class Context:
    package_root = ROOT

    def __init__(self, data_root, settings=None):
        self.data_root = data_root
        self.settings = dict(settings or {})
        self.saved = None

    def load_settings(self):
        return dict(self.settings)

    def save_settings(self, settings):
        self.saved = dict(settings)
        self.settings = dict(settings)

    def resource(self, relative):
        return ROOT / relative


class InertProcess(QProcess):
    """Preserve real Qt signals/env/enums but deliberately never start a process."""
    instances = []

    def __init__(self, parent=None):
        super().__init__(parent)
        self.starts = 0
        self.output = b''
        self.writes = []
        type(self).instances.append(self)

    def start(self, *args, **kwargs):
        self.starts += 1

    def readAllStandardOutput(self):
        result = QByteArray(self.output)
        self.output = b''
        return result

    def readAllStandardError(self):
        return QByteArray()

    def write(self, data):
        self.writes.append(bytes(data))
        return len(data)

    def kill(self):
        pass

    def waitForFinished(self, *args):
        return True


class PanelBootstrapTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.area = ROOT / 'runtime/panel-bootstrap-tests'
        cls.area.mkdir(parents=True, exist_ok=True)
        env = {'TEMP': 'temp', 'TMP': 'temp', 'HOME': 'home', 'USERPROFILE': 'home',
               'LOCALAPPDATA': 'local', 'APPDATA': 'roaming', 'XDG_CACHE_HOME': 'cache'}
        values = {}
        for name, relative in env.items():
            path = cls.area / relative; path.mkdir(parents=True, exist_ok=True)
            values[name] = str(path)
        values['QT_QPA_PLATFORM'] = 'offscreen'
        cls.environment = patch.dict(os.environ, values)
        cls.environment.start()
        cls.app = QApplication.instance() or QApplication([])
        if cls.app.platformName() != 'offscreen':
            cls.environment.stop()
            raise RuntimeError('These tests require QT_QPA_PLATFORM=offscreen; refusing visible UI')

    @classmethod
    def tearDownClass(cls):
        cls.environment.stop()

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(dir=self.area)
        self.base = Path(self.temp.name)
        self.parent = QWidget()
        self.panels = []
        InertProcess.instances.clear()
        self.process_patch = patch.object(panel_module, 'QProcess', InertProcess)
        self.process_patch.start()
        self.excepthook = patch.object(sys, 'excepthook', Mock())
        self.unhandled = self.excepthook.start()

    def tearDown(self):
        for panel in self.panels:
            panel.shutdown()
        self.parent.close()
        self.drain_events()
        self.excepthook.stop()
        self.process_patch.stop()
        self.temp.cleanup()

    def drain_events(self):
        for _ in range(4): self.app.processEvents()

    def new_panel(self, manual=False):
        settings = {'git': str(self.base / 'explicit/git.exe')} if manual else {}
        self.context = Context(self.base / 'data', settings)
        panel = panel_module.Panel(self.context, self.parent)
        panel.warn = Mock(side_effect=AssertionError('Bootstrap must report inline, not open a modal'))
        self.panels.append(panel)
        self.parent.resize(1300, 900); panel.resize(1300, 900)
        self.parent.show()
        return panel

    def assert_recoverable_error(self, panel, text):
        self.assertIsNone(panel.proc)
        self.assertFalse(panel.git_ready)
        self.assertTrue(panel.runtime_retry.isEnabled())
        self.assertFalse(panel.runtime_cancel.isEnabled())
        self.assertEqual(panel.runtime_progress.value(), 0)
        self.assertTrue(panel.runtime_label.isVisible())
        self.assertIn(text, panel.runtime_label.text())
        self.assertIn('磁盘空间', panel.runtime_label.text())
        self.assertIn(text, panel.log.toPlainText())
        self.assertEqual(panel.status.text(), '后台未启动')
        self.assertFalse(any(p.starts for p in InertProcess.instances))
        self.unhandled.assert_not_called()

    def test_first_open_permission_error_is_inline_retryable(self):
        with patch.object(panel_module.Panel, 'worker_environment', side_effect=PermissionError('permission denied')) as env:
            panel = self.new_panel()
            self.drain_events()
            self.assert_recoverable_error(panel, 'permission denied')
            panel.runtime_retry.click()
            self.drain_events()
            self.assert_recoverable_error(panel, 'permission denied')
            self.assertEqual(env.call_count, 2)

    def test_first_open_full_disk_error_is_inline_retryable(self):
        with patch.object(panel_module.Panel, 'worker_environment', side_effect=OSError(28, 'No space left on device')):
            panel = self.new_panel()
            self.drain_events()
            self.assert_recoverable_error(panel, 'No space left on device')

    def test_manual_git_bypasses_managed_container_guard(self):
        with patch.object(panel_module, 'runtime_root', side_effect=ValueError('untrusted managed container')) as guard:
            panel = self.new_panel(manual=True)
            self.drain_events()
            panel.prepare_git()
            self.assertTrue(panel.git_ready)
            self.assertIsNone(panel.proc)
            self.assertIn('手动', panel.runtime_label.text())
            self.assertTrue(panel.runtime_progress.isHidden())
            guard.assert_not_called(); self.unhandled.assert_not_called()

    def test_manual_worker_temp_is_context_data_not_managed_root(self):
        with patch.object(panel_module, 'runtime_root', side_effect=ValueError('read-only package')) as guard:
            panel = self.new_panel(manual=True); self.drain_events()
            process = InertProcess(panel)
            panel.worker_environment(process)
            expected = self.context.data_root / 'worker-temp'
            self.assertTrue(expected.is_dir())
            env = process.processEnvironment()
            self.assertEqual(env.value('TEMP'), str(expected))
            self.assertEqual(env.value('TMP'), str(expected))
            guard.assert_not_called()
            self.assertEqual(process.starts, 0)

    def test_switch_back_to_managed_clears_explicit_choice_and_bootstraps(self):
        panel = self.new_panel(manual=True); self.drain_events()
        managed = self.base / 'managed/git'
        with patch.object(panel_module, 'runtime_root', return_value=managed):
            panel.use_managed_git()
        self.assertNotIn('git', panel.settings)
        self.assertNotIn('git', self.context.saved)
        self.assertFalse(panel.git_ready)
        self.assertEqual(panel.after[0], 'prepare_git')
        self.assertEqual(panel.proc.starts, 1)
        self.assertEqual(panel.proc.processEnvironment().value('TEMP'), str(managed / 'tmp'))
        self.assertFalse(panel.runtime_retry.isEnabled())
        self.assertTrue(panel.runtime_cancel.isEnabled())
        self.assertIn('准备', panel.runtime_label.text())
        self.unhandled.assert_not_called()

    def finish_with_success_json(self, panel, code, exit_status, cancelled=False):
        panel.settings.pop('git', None)
        panel.git_ready = False
        panel.runtime_cancelled = cancelled
        panel.git_pending = ('environment', {}, None, False)
        panel.runtime_buffer = b''
        panel.proc = InertProcess(panel)
        panel.proc.output = json.dumps({'ok': True, 'data': {'version': 'fixture', 'git': 'E:/fixture/git.exe'}}).encode()
        panel.after = ('prepare_git', None, False)
        with patch.object(panel, 'run') as operation:
            panel.runtime_finished(code, exit_status)
            return operation

    def test_success_json_with_nonzero_exit_is_not_ready(self):
        panel = self.new_panel(manual=True); self.drain_events()
        operation = self.finish_with_success_json(panel, 1, QProcess.NormalExit)
        self.assertFalse(panel.git_ready); self.assertIsNone(panel.proc)
        self.assertTrue(panel.runtime_retry.isEnabled()); self.assertFalse(panel.runtime_cancel.isEnabled())
        self.assertEqual(panel.runtime_progress.value(), 0)
        self.assertIn('异常退出', panel.runtime_label.text())
        operation.assert_not_called()

    def test_success_json_with_crash_exit_is_not_ready(self):
        panel = self.new_panel(manual=True); self.drain_events()
        operation = self.finish_with_success_json(panel, 0, QProcess.CrashExit)
        self.assertFalse(panel.git_ready); self.assertIsNone(panel.proc)
        self.assertIn('异常退出', panel.runtime_label.text())
        operation.assert_not_called()

    def test_cancelled_success_json_is_not_ready(self):
        panel = self.new_panel(manual=True); self.drain_events()
        operation = self.finish_with_success_json(panel, 0, QProcess.NormalExit, cancelled=True)
        self.assertFalse(panel.git_ready); self.assertIn('取消', panel.runtime_label.text())
        operation.assert_not_called()

    def test_normal_success_becomes_ready_and_resumes_pending_operation(self):
        panel = self.new_panel(manual=True); self.drain_events()
        operation = self.finish_with_success_json(panel, 0, QProcess.NormalExit)
        self.assertTrue(panel.git_ready); self.assertIsNone(panel.proc)
        self.assertEqual(panel.runtime_progress.value(), 100)
        self.assertIn('离线可用', panel.runtime_label.text())
        operation.assert_called_once_with('environment', {}, None, False)


if __name__ == '__main__': unittest.main()
