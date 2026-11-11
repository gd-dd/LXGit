"""Offscreen LXGit geometry checks inside the real LXTools native host.

All profiles and outputs are isolated on E:. No worker or user repository is run.
The helpers also provide the reproducible before/after layout QA matrix.
"""
import hashlib
import base64
import json
import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import Mock, patch

ROOT = Path(__file__).resolve().parents[1]
QA = ROOT / 'runtime/ui-layout-20261112'
HOST = ROOT.parent / 'LXTools/src'
for key, folder in {'TEMP': 'temp', 'TMP': 'temp', 'HOME': 'home',
                    'USERPROFILE': 'home', 'APPDATA': 'roaming',
                    'LOCALAPPDATA': 'local', 'XDG_CACHE_HOME': 'cache'}.items():
    location = QA / 'environment' / folder
    location.mkdir(parents=True, exist_ok=True)
    os.environ[key] = str(location)
os.environ['QT_QPA_PLATFORM'] = 'offscreen'
os.environ['PYTHONDONTWRITEBYTECODE'] = '1'
sys.dont_write_bytecode = True
sys.path.insert(0, str(HOST))
from PySide6.QtCore import QPoint, QRect, Qt, QProcess, QTimer, QCoreApplication, QEvent, QBuffer, QByteArray, QIODevice
from PySide6.QtGui import QFont, QFontDatabase, QPixmap, QColor
from PySide6.QtWidgets import QApplication, QWidget, QSplitter, QLabel, QDialog, QLayout, QPushButton, QAbstractScrollArea, QMenu, QHBoxLayout, QVBoxLayout, QComboBox, QStackedWidget
from core import ToolStore
from desktop import Window, STYLE
from native_panels import PanelContext
from repository_panels import RepositoryWorkspace

PROJECT = '星海冒险_大学生游戏开发团队_课程设计与毕业作品_第七组_超长项目名称'
BRANCH = 'feature/' + '角色移动与多人联机同步功能开发_' * 6
FILE = 'Assets/游戏资源/角色/主角/' + '超长中文模型贴图与场景文件名_' * 7 + '.prefab'
ERROR = ('Git 下载连接中断，无法连接服务器。请检查网络后点击“准备 / 重试”。' * 5
         + 'https://github.com/git-for-windows/git/releases/download/PortableGit.7z.exe')


def app_instance():
    app = QApplication.instance() or QApplication([])
    if app.platformName() != 'offscreen':
        raise RuntimeError('Layout QA refuses a visible platform')
    if not getattr(app, '_lx_layout_fonts', False):
        # Offscreen Windows has no native font discovery; read installed fonts only.
        for name in ('msyh.ttc', 'msyhbd.ttc', 'segoeui.ttf', 'consola.ttf'):
            path = Path(os.environ.get('SystemRoot', 'C:/Windows')) / 'Fonts' / name
            if path.is_file(): QFontDatabase.addApplicationFont(str(path))
        app.setFont(QFont('Microsoft YaHei', 9))
        app._lx_layout_fonts = True
    app.setStyle('Fusion')
    app.setStyleSheet(STYLE)
    return app


def drain(app):
    for _ in range(8):
        app.processEvents()
        QCoreApplication.sendPostedEvents(None, QEvent.DeferredDelete)


class ResultProcess:
    def readAllStandardOutput(self): return b''
    def deleteLater(self): pass


def minimal_native_host(base):
    """QA host chrome only, with standard Qt constraints and no size override.

    Unlike production Window's tall sidebar, this empty 280px sidebar lets the
    native panel's own first-layout minimum be tested independently.
    """
    window = QWidget()
    root = QHBoxLayout(window); root.setContentsMargins(0, 0, 0, 0); root.setSpacing(0)
    sidebar = QWidget(); sidebar.setObjectName('sidebar'); sidebar.setFixedWidth(280)
    root.addWidget(sidebar)
    body = QWidget(); layout = QVBoxLayout(body); layout.setContentsMargins(28, 24, 28, 16)
    window.pages = QStackedWidget(); layout.addWidget(window.pages, 1)
    window.tool_panel = QWidget(); tool = QVBoxLayout(window.tool_panel); tool.setContentsMargins(0, 0, 0, 0)
    header = QHBoxLayout(); header.addWidget(QPushButton('‹ 返回我的工具'))
    window.panel_title = QLabel(); window.panel_title.setObjectName('title'); window.panel_title.setWordWrap(True)
    header.addWidget(window.panel_title, 1); header.addWidget(QLabel('收纳到'))
    window.move_combo = QComboBox(); window.move_combo.setMinimumWidth(140); header.addWidget(window.move_combo)
    tool.addLayout(header)
    window.workspace = RepositoryWorkspace(base / 'tools', base / 'profile', managed=True)
    tool.addWidget(window.workspace, 1); window.pages.addWidget(window.tool_panel)
    status = QLabel('已载入作者目录。公开工具无需登录即可安装。'); status.setObjectName('subtitle'); status.setWordWrap(True)
    layout.addWidget(status); root.addWidget(body, 1)
    return window


class LayoutFixture:
    def __init__(self, package=ROOT, width=1280, height=720, force_viewport=False,
                 natural_host=False, minimal_host=False):
        self.app = app_instance()
        self.temp = tempfile.TemporaryDirectory(prefix='host-', dir=QA)
        self.base = Path(self.temp.name)
        self.package = Path(package)
        # This is a local UI fixture, not an installer receipt or release gate.
        files = {'ui/panel.py', 'ui/runtime_paths.py', '.lxtool/icon.png', '.lxtool/panel.json'}
        files.update(p.relative_to(self.package).as_posix() for p in (self.package / 'ui').glob('*.py'))
        tool = {'id': 'repo-1432888469', 'version': '0.0.6', 'name': 'LXGit 游戏协作',
                'root': self.package, 'files': {p: hashlib.sha256((self.package / p).read_bytes()).hexdigest() for p in files},
                'panelData': {'type': 'native', 'apiVersion': 1, 'entry': 'ui/panel.py'}}
        self.minimal_host = minimal_host
        self.natural_host = natural_host
        if minimal_host:
            self.window = minimal_native_host(self.base)
        else:
            with patch('desktop.development.enabled', return_value=False):
                self.window = Window(ToolStore(self.base / 'profile'), self.base / 'tools', autoload=False)
        if natural_host:
            self.window.setMinimumSize(0, 0)
        self.window.findChild(QWidget, 'sidebar').setFixedWidth(280)
        self.window.panel_title.setText('LXGit 游戏协作 · 0.0.6')
        self.window.move_combo.addItem('未分类')
        self.window.pages.setCurrentWidget(self.window.tool_panel)
        # The explicit inert path suppresses scheduled bootstrap without executing anything.
        with patch.object(PanelContext, 'load_settings', return_value={'git': str(self.base / 'never-run/git.exe')}):
            self.window.workspace.display(tool)
        handle = getattr(self.window.workspace, 'native_panel', None)
        if handle is None or handle.widget is None:
            error = '\n'.join(w.text() for w in self.window.workspace.findChildren(QLabel))
            self.close()
            raise AssertionError('Real NativePanelHandle failed to construct fixture: ' + error)
        self.handle = handle
        self.panel = handle.widget
        self.panel.run = Mock(name='no_worker_or_repo_operations')
        self.panel.warn = Mock(side_effect=AssertionError('Unexpected modal in layout QA'))
        self.panel.timer.stop()
        self.force_viewport = force_viewport
        if force_viewport:
            # Stress only: production Window deliberately has a 1040x720 minimum.
            # Release only host ancestor layout constraints, never the tool's own.
            ancestor = self.panel.parentWidget()
            while ancestor is not None:
                layout = QWidget.layout(ancestor)
                if layout: layout.setSizeConstraint(QLayout.SetNoConstraint)
                ancestor.setMinimumSize(0, 0)
                ancestor = ancestor.parentWidget()
            self.window.setMinimumSize(0, 0)
            self.window.setMaximumSize(width, height)
        self.window.resize(width, height)
        self.window.show()
        drain(self.app)
        self.requested = (width, height)
        self.set_runtime(True)

    def set_runtime(self, success, message=ERROR):
        p = self.panel
        p.settings.pop('git', None)
        p.git_ready = False
        p.git_pending = None
        p.runtime_cancelled = False
        p.runtime_buffer = json.dumps({'ok': True, 'data': {'version': '2.56.1.windows.1'}} if success
                                     else {'ok': False, 'error': message}, ensure_ascii=False).encode()
        p.proc = ResultProcess()
        p.after = ('prepare_git', None, False)
        p.runtime_finished(0, QProcess.NormalExit)
        drain(self.app)

    def long_content(self):
        p = self.panel
        path = str(self.base / PROJECT)
        p.repositories.blockSignals(True)
        p.repositories.addItem(PROJECT, path)
        p.repositories.setCurrentIndex(0)
        p.repositories.blockSignals(False)
        p.repo = path
        p.render({'root': path, 'branch': BRANCH, 'branches': [BRANCH, 'main'],
                  'files': [{'status': ' M', 'path': FILE, 'old': '', 'conflict': False}],
                  'ahead': 32, 'behind': 7, 'operations': [], 'remotes': 'origin https://github.com/fixture/game.git',
                  'stashes': [], 'upstream': 'origin/main', 'tags': []})
        p.file_label.setText(FILE)
        p.diff.setPlainText('diff --git a/Player.cs b/Player.cs\n@@ -1,3 +1,4 @@\n-旧的角色移动\n+修复角色移动\n+保留团队成员的场景修改\n' * 22)
        p.message.setText('修复角色跳跃并同步第一关场景')
        drain(self.app)

    def rect(self, widget):
        point = widget.mapTo(self.panel, QPoint())
        return QRect(point, widget.size())

    def visible_rect(self, widget):
        rect = QRect(widget.mapTo(self.window, QPoint()), widget.size())
        ancestor = widget.parentWidget()
        while ancestor is not None:
            rect = rect.intersected(QRect(ancestor.mapTo(self.window, QPoint()), ancestor.size()))
            if ancestor is self.window: break
            ancestor = ancestor.parentWidget()
        return rect

    def snapshot(self, screenshot=None):
        p = self.panel
        widgets = {n: getattr(p, n, None) for n in (
            'repositories', 'branches', 'summary', 'runtime_label', 'runtime_box',
            'header_bar', 'repo_bar', 'sync_bar', 'tabs', 'files', 'diff',
            'message', 'commit_button', 'commit_actions', 'workspace_split', 'history_split',
            'status', 'environment_status')}
        geometry = {name: [self.rect(w).x(), self.rect(w).y(), w.width(), w.height()]
                    for name, w in widgets.items() if isinstance(w, QWidget)}
        dpr = self.window.devicePixelRatioF()
        result = {'requestedLogical': self.requested,
                  'actualLogical': [self.window.width(), self.window.height()], 'dpr': dpr,
                  'physicalPixels': [round(self.window.width() * dpr), round(self.window.height() * dpr)],
                  'qaOnlyForcedViewport': self.force_viewport,
                  'qaMinimalHostChrome': self.minimal_host,
                  'qaRemovedOnlyWindowHardMinimum': self.natural_host,
                  'panel': [p.width(), p.height()], 'panelMinimumHint': [p.minimumSizeHint().width(), p.minimumSizeHint().height()],
                  'geometry': geometry, 'gitReady': p.git_ready,
                  'retryVisible': p.runtime_retry.isVisible(), 'cancelVisible': p.runtime_cancel.isVisible(),
                  'progressVisible': p.runtime_progress.isVisible(),
                  'progressTextVisible': p.runtime_progress.isTextVisible(),
                  'progressContentHeight': p.runtime_progress.contentsRect().height(),
                  'progressFontHeight': p.runtime_progress.fontMetrics().height(),
                  'progressAccessibleName': p.runtime_progress.accessibleName(),
                  'runtimeLabel': p.runtime_label.text(), 'runtimeTooltip': p.runtime_label.toolTip(),
                  'fileTooltip': p.file_label.toolTip(),
                  'branchTooltip': getattr(p, 'branch_button', p.branches).toolTip(),
                  'repoTooltip': p.repositories.toolTip(),
                  'containsCommit': p.rect().contains(self.rect(p.commit_button)),
                  'containsDiff': p.rect().contains(self.rect(p.diff)),
                  'splitters': [{'sizes': s.sizes(), 'childrenCollapsible': s.childrenCollapsible()} for s in p.findChildren(QSplitter)],
                  'sourceSha256': hashlib.sha256((self.package / 'ui/panel.py').read_bytes()).hexdigest()}
        result['visibleViewports'] = {}
        for name in ('files', 'diff'):
            viewport = getattr(p, name).viewport()
            visible = self.visible_rect(viewport)
            result['visibleViewports'][name] = {'widgetSize': [viewport.width(), viewport.height()],
                                               'clippedVisibleSize': [visible.width(), visible.height()]}
        result['commitVisibleSize'] = [self.visible_rect(p.commit_button).width(), self.visible_rect(p.commit_button).height()]
        if hasattr(p, 'environment_status'):
            visible = self.visible_rect(p.environment_status)
            result['environmentVisibleSize'] = [visible.width(), visible.height()]
        if screenshot:
            result['screenshot'] = str(screenshot)
            if not self.window.grab().save(str(screenshot)):
                raise AssertionError('Screenshot could not be saved')
        return result

    def modal_snapshot(self, callback, screenshot=None):
        result = {}
        failure = []
        def capture():
            dialog = self.app.activeModalWidget()
            try:
                if not isinstance(dialog, QDialog):
                    raise AssertionError('Expected a real offscreen modal dialog')
                drain(self.app)
                screen = dialog.screen().availableGeometry()
                result.update(title=dialog.windowTitle(), size=[dialog.width(), dialog.height()],
                              minimumHint=[dialog.minimumSizeHint().width(), dialog.minimumSizeHint().height()],
                              virtualScreen=[screen.width(), screen.height()])
                def visible_rect(widget):
                    rect = QRect(widget.mapTo(dialog, QPoint()), widget.size())
                    ancestor = widget.parentWidget()
                    while ancestor is not None:
                        rect = rect.intersected(QRect(ancestor.mapTo(dialog, QPoint()), ancestor.size()))
                        if ancestor is dialog: break
                        ancestor = ancestor.parentWidget()
                    return rect
                result['buttons'] = []
                for button in dialog.findChildren(QPushButton):
                    if not button.isVisibleTo(dialog): continue
                    visible = visible_rect(button)
                    result['buttons'].append({'text': button.text(), 'size': [button.width(), button.height()],
                                              'visibleSize': [visible.width(), visible.height()],
                                              'terminal': any(s in button.text() for s in ('返回', '关闭', '保存说明', '暂存勾选', '确认重排', '明白了'))})
                result['contents'] = []
                for area in dialog.findChildren(QAbstractScrollArea):
                    if not area.isVisibleTo(dialog): continue
                    visible = visible_rect(area.viewport())
                    result['contents'].append({'type': type(area).__name__, 'visibleSize': [visible.width(), visible.height()]})
                result['images'] = []
                for label in dialog.findChildren(QLabel):
                    if not label.isVisibleTo(dialog): continue
                    pixmap = label.pixmap()
                    if pixmap and not pixmap.isNull():
                        visible = visible_rect(label)
                        result['images'].append({'pixmapSize': [pixmap.width(), pixmap.height()],
                                                 'visibleSize': [visible.width(), visible.height()]})
                if screenshot:
                    result['screenshot'] = str(screenshot)
                    if not dialog.grab().save(str(screenshot)): raise AssertionError('Modal screenshot failed')
            except Exception as error:
                failure.append(error)
            finally:
                if isinstance(dialog, QDialog): dialog.reject()
        QTimer.singleShot(0, capture)
        callback()
        drain(self.app)
        if failure: raise failure[0]
        if not result: raise AssertionError('Dialog was never captured')
        return result

    def legacy_callbacks(self):
        p = self.panel
        image = QPixmap(1600, 900)
        image.fill(QColor('#8dbb43'))
        data = QByteArray()
        buffer = QBuffer(data)
        buffer.open(QIODevice.WriteOnly)
        image.save(buffer, 'PNG')
        encoded = base64.b64encode(bytes(data)).decode('ascii')
        patch_text = ('diff --git a/Player.cs b/Player.cs\n--- a/Player.cs\n+++ b/Player.cs\n'
                      '@@ -1,2 +1,2 @@\n-旧的角色移动\n+修复角色跳跃\n 保留队友改动\n')
        def reorder():
            rows = [{'sha': digit * 40} for digit in ('a', 'b', 'c')]
            def history(op, *args, **kwargs):
                if op != 'history': raise AssertionError('QA must not modify history')
                kwargs['callback'](rows)
            with patch.object(p, 'number', return_value=(2, True)), patch.object(p, 'run', side_effect=history):
                p.rewrite_history('reorder')
        return [('description', p.edit_description), ('help', p.help), ('guide', p.beginner_guide),
                ('partial', lambda: p.patch_dialog(patch_text)),
                ('image', lambda: p.image_dialog({'before': encoded, 'after': encoded})),
                ('reorder', reorder)]

    def open_and_close_menu(self, callback):
        failures = []
        captured = []
        def close_popup():
            menu = self.app.activePopupWidget()
            try:
                if not isinstance(menu, QMenu): raise AssertionError('Expected a real offscreen QMenu')
                captured.append(len(menu.actions()))
            except Exception as error:
                failures.append(error)
            finally:
                if isinstance(menu, QMenu): menu.close()
        QTimer.singleShot(0, close_popup)
        callback()
        drain(self.app)
        if failures: raise failures[0]
        if not captured or not captured[0]: raise AssertionError('Empty or unopened menu')

    def close(self):
        if getattr(self, 'handle', None): self.handle.shutdown()
        self.window.close()
        self.window.deleteLater()
        drain(self.app)
        self.temp.cleanup()


class PanelLayoutTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls): cls.app = app_instance()

    def setUp(self):
        self.fixture = LayoutFixture()
        self.panel = self.fixture.panel

    def tearDown(self): self.fixture.close()

    def test_long_content_does_not_force_host_larger_and_commit_is_reachable(self):
        self.fixture.long_content()
        result = self.fixture.snapshot()
        self.assertEqual(result['actualLogical'], [1280, 720])
        self.assertTrue(result['containsCommit'])
        self.assertTrue(result['containsDiff'])
        self.assertGreaterEqual(self.panel.diff.height(), 160)
        self.assertLessEqual(self.panel.minimumSizeHint().width(), self.panel.width())

    def test_ready_runtime_is_compact_and_failure_remains_actionable(self):
        ready = self.fixture.snapshot()
        self.assertTrue(not ready['progressTextVisible']
                        or ready['progressContentHeight'] >= ready['progressFontHeight'],
                        'Host-styled progress bar must not clip percentage text')
        self.assertTrue(ready['progressAccessibleName'])
        self.assertFalse(ready['progressVisible'])
        self.assertFalse(ready['cancelVisible'])
        self.fixture.set_runtime(False)
        failed = self.fixture.snapshot()
        self.assertEqual(failed['actualLogical'], [1280, 720])
        self.assertTrue(self.panel.runtime_retry.isVisible())
        self.assertTrue(self.panel.runtime_retry.isEnabled())
        self.assertTrue(self.panel.runtime_label.isVisible())
        self.assertTrue(ERROR in self.panel.runtime_label.text() or ERROR in self.panel.runtime_label.toolTip()
                        or ERROR in self.panel.log.toPlainText())
        self.assertTrue(failed['containsCommit'])

    def test_workspace_and_history_splitters_are_user_adjustable(self):
        for index, name in enumerate(('workspace_split', 'history_split')):
            self.panel.tabs.setCurrentIndex(index)
            drain(self.app)
            splitter = getattr(self.panel, name)
            self.assertFalse(splitter.childrenCollapsible())
            before = splitter.sizes()
            splitter.setSizes([sum(before) // 2, sum(before) // 2])
            drain(self.app)
            self.assertTrue(all(value > 0 for value in splitter.sizes()))
            self.assertNotEqual(splitter.sizes(), before)

    def test_long_file_and_branch_remain_available_as_tooltips(self):
        self.fixture.long_content()
        self.assertIn(FILE, self.panel.file_label.toolTip())
        self.assertIn(BRANCH, self.panel.branch_button.toolTip())
        self.assertIn(PROJECT, self.panel.repositories.toolTip())
        dynamic = '<tag>场景与队友&文件名称</tag>' * 12
        for label in (self.panel.file_label, self.panel.summary, self.panel.runtime_label, self.panel.status):
            label.setText(dynamic)
            self.assertEqual(label.textFormat(), Qt.PlainText)
            self.assertEqual(label.accessibleName(), dynamic)
            self.assertEqual(label.toolTip(), dynamic)

    def test_secondary_panels_fit_and_return_reusable_widgets(self):
        self.fixture.long_content()
        p = self.panel
        p.description = '队友仍需继续编辑的提交详细说明'
        p.amend.setChecked(True)
        p.staged.setChecked(True)
        p.filter.setText('超长')
        p.files.setCurrentRow(0)
        p.files.item(0).setSelected(True)
        p.diff.verticalScrollBar().setValue(40)
        p.workspace_split.setSizes([340, 500])
        drain(self.app)
        def draft_state():
            return {'message': p.message.text(), 'description': p.description,
                    'amend': p.amend.isChecked(), 'staged': p.staged.isChecked(),
                    'filter': p.filter.text(), 'selected': [i.text() for i in p.files.selectedItems()],
                    'current': p.files.currentRow(), 'diff': p.diff.toPlainText(),
                    'scroll': p.diff.verticalScrollBar().value(), 'split': p.workspace_split.sizes()}
        before = draft_state()
        callbacks = (self.panel.project_panel, self.panel.branch_panel,
                         self.panel.sync_panel, self.panel.tools_panel,
                         lambda: self.panel.show_auxiliary('储藏改动'),
                         lambda: self.panel.show_auxiliary('操作记录'))
        callbacks += tuple(callback for _, callback in self.fixture.legacy_callbacks())
        for callback in callbacks:
            with self.subTest(callback=callback):
                result = self.fixture.modal_snapshot(callback)
                self.assertLessEqual(result['size'][0], result['virtualScreen'][0])
                self.assertLessEqual(result['size'][1], result['virtualScreen'][1])
                self.assertTrue(result['title'])
                terminal = [b for b in result['buttons'] if b['terminal']]
                self.assertTrue(terminal, 'Dialog needs a reachable return or confirmation action')
                for button in terminal:
                    self.assertEqual(button['visibleSize'], button['size'], button['text'])
                for content in result['contents']:
                    self.assertTrue(all(size > 0 for size in content['visibleSize']))
                for image in result['images']:
                    self.assertTrue(all(size <= available for size, available in zip(image['pixmapSize'], image['visibleSize'])))
                self.assertEqual(draft_state(), before, 'Secondary navigation lost a pending draft or view state')
        self.assertIs(self.panel.branches.parentWidget(), self.panel)
        self.assertTrue(self.panel.branches.isHidden())
        self.assertIs(self.panel.log.parentWidget(), self.panel)
        self.assertTrue(self.panel.log.isHidden())

    def test_qa_forced_small_viewport_keeps_lists_diff_and_commit_visible(self):
        self.fixture.close()
        self.fixture = LayoutFixture(width=853, height=480, force_viewport=True)
        self.panel = self.fixture.panel
        self.fixture.long_content()
        for ready in (True, False):
            self.fixture.set_runtime(ready)
            result = self.fixture.snapshot()
            with self.subTest(ready=ready):
                self.assertEqual(result['actualLogical'], [853, 480])
                for item in result['visibleViewports'].values():
                    self.assertGreaterEqual(item['clippedVisibleSize'][0], 80)
                    self.assertGreaterEqual(item['clippedVisibleSize'][1], 80)
                self.assertEqual(result['commitVisibleSize'],
                                 [self.panel.commit_button.width(), self.panel.commit_button.height()])
                self.assertEqual(result['environmentVisibleSize'],
                                 [self.panel.environment_status.width(), self.panel.environment_status.height()])

    def test_environment_status_is_fully_visible_in_ready_and_error_states(self):
        for ready, text in ((True, 'Git 已就绪'), (False, 'Git 尚未就绪')):
            with self.subTest(ready=ready):
                self.fixture.set_runtime(ready)
                p = self.panel
                result = self.fixture.snapshot()
                self.assertEqual(result['environmentVisibleSize'],
                                 [p.environment_status.width(), p.environment_status.height()])
                self.assertEqual(QLabel.text(p.environment_status), text)

    def test_natural_first_small_host_and_large_to_small_resize(self):
        self.fixture.close()
        for first in ((853, 480), (1280, 720)):
            self.fixture = LayoutFixture(width=first[0], height=first[1], minimal_host=True)
            self.panel = self.fixture.panel
            self.fixture.long_content()
            if first != (853, 480):
                self.fixture.window.resize(853, 480)
                self.fixture.requested = (853, 480)
                drain(self.app)
            for ready in (True, False):
                self.fixture.set_runtime(ready)
                result = self.fixture.snapshot()
                with self.subTest(first=first, ready=ready):
                    self.assertEqual(result['actualLogical'], [853, 480])
                    self.assertGreater(self.fixture.window.maximumWidth(), 853)
                    self.assertGreater(self.fixture.window.maximumHeight(), 480)
                    ancestor = self.panel
                    while ancestor is not None:
                        layout = QWidget.layout(ancestor)
                        if layout: self.assertNotEqual(layout.sizeConstraint(), QLayout.SetNoConstraint)
                        ancestor = ancestor.parentWidget()
                    for item in result['visibleViewports'].values():
                        self.assertGreaterEqual(min(item['clippedVisibleSize']), 80)
                    self.assertEqual(result['commitVisibleSize'], [self.panel.commit_button.width(), self.panel.commit_button.height()])
                    self.assertEqual(result['environmentVisibleSize'], [self.panel.environment_status.width(), self.panel.environment_status.height()])
            if first == (853, 480): self.fixture.close()

    def test_secondary_dialogs_do_not_accumulate_over_six_cycles(self):
        p = self.panel
        before = len(p.findChildren(QDialog))
        menus_before = len(p.findChildren(QMenu))
        callbacks = (p.project_panel, p.branch_panel, p.sync_panel, p.tools_panel,
                     lambda: p.show_auxiliary('储藏改动'), lambda: p.show_auxiliary('操作记录'))
        callbacks += tuple(callback for _, callback in self.fixture.legacy_callbacks())
        for cycle in range(6):
            for callback in callbacks:
                self.fixture.modal_snapshot(callback)
            for callback in (lambda: p.menu([('测试菜单操作', lambda: None)]), p.diff_menu, p.commit_menu):
                self.fixture.open_and_close_menu(callback)
            with self.subTest(cycle=cycle):
                drain(self.app)
                self.assertEqual(len(p.findChildren(QDialog)), before)
                self.assertEqual(len(p.findChildren(QMenu)), menus_before)
                self.assertIs(p.branches.parentWidget(), p)
                self.assertIs(p.log.parentWidget(), p)


if __name__ == '__main__': unittest.main()
