"""Manager -> worker JSON -> source offscreen panel cleanup diagnostics.

The worker entry point is called with inert process/thread/input mocks. No EXE,
Git, shell, SFX, network, desktop or user installation is started or modified.
"""
import io
import json
import os
from pathlib import Path
import sys
import tempfile
import threading
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT)); sys.path.insert(0, str(ROOT / 'tests'))
os.environ['QT_QPA_PLATFORM'] = 'offscreen'
from PySide6.QtCore import QProcess
from PySide6.QtWidgets import QApplication, QWidget, QPushButton
from ui import panel as panel_module
from backend import worker, git_runtime
from test_panel_bootstrap import Context, InertProcess

AREA = ROOT / 'runtime/bootstrap-fix-20261112/diagnostics-tests'
WARNING = ('临时目录清理失败，已保留 E:/fixture/runtime/git/.stage-owned：'
           '[WinError 5] 拒绝访问；https://fixture-user:fixture-secret@example.invalid/')


class BootstrapDiagnosticsTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        AREA.mkdir(parents=True, exist_ok=True)
        values = {'QT_QPA_PLATFORM': 'offscreen', 'PYTHONDONTWRITEBYTECODE': '1'}
        for key, name in {'TEMP': 'temp', 'TMP': 'temp', 'APPDATA': 'roaming',
                          'LOCALAPPDATA': 'local', 'HOME': 'home',
                          'USERPROFILE': 'home', 'XDG_CACHE_HOME': 'cache'}.items():
            path = AREA / name; path.mkdir(exist_ok=True); values[key] = str(path)
        cls.environment = patch.dict(os.environ, values); cls.environment.start()
        cls.app = QApplication.instance() or QApplication([])
        if cls.app.platformName() != 'offscreen':
            cls.environment.stop(); raise RuntimeError('Diagnostics tests refuse a visible Qt platform')

    @classmethod
    def tearDownClass(cls):
        cls.environment.stop()

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='diagnostics-', dir=AREA)
        self.base = Path(self.temp.name)
        self.parent = QWidget(); self.parent.resize(1300, 900)
        self.process_patch = patch.object(panel_module, 'QProcess', InertProcess)
        self.process_patch.start(); InertProcess.instances.clear()
        self.panel = panel_module.Panel(Context(self.base / 'data', {
            'git': str(self.base / 'explicit/never-run.exe')}), self.parent)
        self.panel.resize(1300, 900); self.parent.show()
        for _ in range(5): self.app.processEvents()
        self.assertTrue(self.panel.git_ready)
        self.panel.warn = Mock(side_effect=AssertionError('Cleanup diagnostics must stay inline'))
        self.panel.show_text = Mock()

    def tearDown(self):
        self.panel.shutdown(); self.parent.close()
        for _ in range(4): self.app.processEvents()
        self.assertFalse(any(p.starts for p in InertProcess.instances), 'No QProcess may start in this fixture')
        self.process_patch.stop(); self.temp.cleanup()

    def worker_wire(self, outcome):
        """Execute real worker.main serialization with a fake manager and no thread."""
        request = (json.dumps({'op': 'prepare_git', 'package': 'E:/fixture/package'}) + '\n').encode()
        reads = [request[i:i+1] for i in range(len(request))]
        output = io.BytesIO(); before = threading.active_count()
        def prepare(package, cancel, progress):
            self.assertEqual(package, 'E:/fixture/package')
            progress('诊断测试：组件已验证', 1, 1)
            if isinstance(outcome, Exception): raise outcome
            return outcome
        with patch.object(worker, 'own_process_tree', return_value=object()) as own_tree, \
             patch.object(worker, 'dispatch') as dispatch, \
             patch.object(worker.os, 'read', side_effect=reads) as read, \
             patch.object(worker.sys, 'stdin', SimpleNamespace(fileno=lambda: 43210)), \
             patch.object(worker.sys, 'stdout', SimpleNamespace(buffer=output)), \
             patch.object(worker.sys, 'argv', ['inert-worker']), \
             patch.object(worker.git_runtime, 'prepare', side_effect=prepare) as manager, \
             patch.object(threading, 'Thread') as thread:
            worker.main()
        own_tree.assert_called_once(); dispatch.assert_not_called(); manager.assert_called_once()
        thread.assert_called_once(); thread.return_value.start.assert_called_once()
        thread.return_value.join.assert_called_once_with(timeout=1)
        self.assertEqual(read.call_count, len(request))
        self.assertEqual(threading.active_count(), before)
        wire = output.getvalue(); result = json.loads(wire.splitlines()[-1])
        self.assertNotIn('fixture-secret', wire.decode('utf-8'))
        return wire, result

    def deliver(self, wire, cancelled=False, pending=None):
        p = self.panel; p.settings.pop('git', None); p.git_ready = False
        p.runtime_cancelled = cancelled; p.git_pending = pending; p.runtime_buffer = b''
        p.proc = InertProcess(p); p.proc.output = wire; p.after = ('prepare_git', None, False)
        p.runtime_view(False)
        p.runtime_finished(0, QProcess.NormalExit)
        for _ in range(3): self.app.processEvents()
        return p

    def assert_warning_visible(self, panel, warning, detail_button=True):
        self.assertIn(warning, panel.log.toPlainText())
        self.assertIn(warning, panel.runtime_label.text())
        self.assertIn(warning, panel.runtime_label.toolTip())
        self.assertIn(warning, panel.environment_status.toolTip())
        self.assertNotIn('fixture-secret', panel.log.toPlainText())
        if detail_button:
            button = next(b for b in panel.runtime_box.findChildren(QPushButton) if b.text() == '详情')
            self.assertTrue(button.isVisible()); button.click()
            self.panel.show_text.assert_called_with(panel.runtime_label.text())

    def test_primary_cleanup_attribute_survives_worker_json_and_panel_details(self):
        error = ValueError('Git 已解压并验证，但无法切换运行目录。')
        error.cleanup_warning = WARNING
        wire, result = self.worker_wire(error)
        self.assertFalse(result['ok']); self.assertIn('无法切换', result['error'])
        warning = result['cleanupWarning']; self.assertIn('临时目录清理失败', warning)
        self.assertIn('[已隐藏凭据]', warning)
        with patch.object(self.panel, 'run') as operation:
            p = self.deliver(wire, pending=('environment', {}, None, False))
        self.assertFalse(p.git_ready); self.assertIsNone(p.proc); self.assertIsNone(p.git_pending)
        operation.assert_not_called(); self.assertIn('无法切换', p.runtime_label.text())
        self.assertTrue(p.runtime_retry.isVisible()); self.assertTrue(p.runtime_retry.isEnabled())
        self.assert_warning_visible(p, warning)

    def test_cancelled_failure_keeps_cancellation_and_cleanup_warning_visible(self):
        error = git_runtime.Cancelled('已取消 Git 准备，可稍后重试。'); error.cleanup_warning = WARNING
        wire, result = self.worker_wire(error)
        self.assertFalse(result['ok']); self.assertIn('取消', result['error'])
        with patch.object(self.panel, 'run') as operation:
            p = self.deliver(wire, cancelled=True, pending=('environment', {}, None, False))
        operation.assert_not_called(); self.assertFalse(p.git_ready)
        self.assertIn('取消', p.runtime_label.text()); self.assert_warning_visible(p, result['cleanupWarning'])

    def test_success_cleanup_warning_keeps_ready_and_exposes_log_and_tooltip(self):
        # Success data originates in the manager; its warning does not contain a URL.
        warning = 'Git 可继续使用；临时目录尚未清理：E:/fixture/.stage-owned；[WinError 5] 拒绝访问'
        wire, result = self.worker_wire({'version': 'fixture', 'git': 'E:/fixture/cmd/git.exe',
                                        'reused': False, 'cleanupWarning': warning})
        self.assertTrue(result['ok']); self.assertEqual(result['data']['cleanupWarning'], warning)
        p = self.deliver(wire)
        self.assertTrue(p.git_ready); self.assertEqual(p.runtime_progress.value(), 100)
        self.assertTrue(p.runtime_box.isHidden(), 'Warning must not add a permanent ready-state row')
        self.assertIn('清理提示', p.status.text()); self.assertIn('更多', p.status.text())
        self.assert_warning_visible(p, warning, detail_button=False)

    def test_success_warning_does_not_replace_resumed_pending_operation_state(self):
        warning = '临时文件尚未清理：E:/fixture/.stage-owned'
        wire, _ = self.worker_wire({'version': 'fixture', 'git': 'E:/fixture/cmd/git.exe',
                                    'cleanupWarning': warning})
        def resume(*args):
            self.panel.proc = InertProcess(self.panel)
            self.panel.after = ('environment', None, False)
            self.panel.status.setText('正在继续排队操作')
        with patch.object(self.panel, 'run', side_effect=resume) as operation:
            p = self.deliver(wire, pending=('environment', {}, None, False))
        operation.assert_called_once_with('environment', {}, None, False)
        self.assertTrue(p.git_ready); self.assertIsNotNone(p.proc)
        self.assertEqual(p.status.text(), '正在继续排队操作')
        self.assert_warning_visible(p, warning, detail_button=False)


if __name__ == '__main__': unittest.main()
