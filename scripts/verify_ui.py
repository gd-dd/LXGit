"""Exercise the LXTool native loader and packaged LXGit worker using disposable repositories."""
import json
import os
from pathlib import Path
import sys
import tempfile
import time

ROOT = Path(__file__).resolve().parents[1]
HOST = next(parent / 'LXTools/src' for parent in ROOT.parents
            if (parent / 'LXTools/src/native_panels.py').is_file())
OUTPUT = ROOT / 'runtime/workspace-ui'
OUTPUT.mkdir(parents=True, exist_ok=True)
for name, folder in {
    'TEMP': 'temp', 'TMP': 'temp', 'USERPROFILE': 'profile', 'APPDATA': 'roaming',
    'LOCALAPPDATA': 'local', 'XDG_CONFIG_HOME': 'config', 'XDG_CACHE_HOME': 'cache',
}.items():
    location = OUTPUT / folder
    location.mkdir(exist_ok=True)
    os.environ[name] = str(location)
os.environ['GIT_CONFIG_GLOBAL'] = str(OUTPUT / 'gitconfig')
os.environ['GIT_CONFIG_NOSYSTEM'] = '1'
os.environ['QT_QPA_PLATFORM'] = 'offscreen'
sys.dont_write_bytecode = True
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(HOST))

from PySide6.QtCore import Qt, QTimer
from PySide6.QtGui import QFont, QFontDatabase
from PySide6.QtWidgets import QApplication, QListWidget, QMenu, QPlainTextEdit, QWidget
from backend.worker import Git
from native_panels import NativePanelHandle, PanelContext


def main():
    app = QApplication([])
    font = Path(os.environ.get('SystemRoot', 'C:/Windows')) / 'Fonts/msyh.ttc'
    if font.is_file():
        QFontDatabase.addApplicationFont(str(font))
        app.setFont(QFont('Microsoft YaHei', 9))
    manifest = json.loads((ROOT / '.lxtool/manifest.json').read_text('utf-8'))
    tool = dict(manifest, id='repo-0', root=ROOT, panelData={'type': 'native', 'apiVersion': 1, 'entry': 'ui/panel.py'})
    with tempfile.TemporaryDirectory(dir=OUTPUT) as temp:
        base = Path(temp)
        repo = base / '中文界面验收'
        repo.mkdir()
        remote = base / '远端-仓库.git'
        g = Git(str(repo))
        g.run('init', '--bare', '-b', 'main', remote)
        g.run('init', '-b', 'main')
        g.run('config', 'user.name', 'UI Test')
        g.run('config', 'user.email', 'ui@example.invalid')
        (repo / '说明.txt').write_text('原来的内容\n', 'utf-8', newline='\n')
        g.run('add', '.')
        g.run('commit', '-m', '初始提交')
        g.run('remote', 'add', 'origin', remote)
        g.run('push', '-u', 'origin', 'main')
        (repo / '说明.txt').write_text('新的内容\n', 'utf-8', newline='\n')
        (repo / '本地草稿.txt').write_text('尚未提交', 'utf-8')

        parent = QWidget()
        parent.resize(1300, 850)
        config = base / 'config'
        PanelContext(tool, config).save_settings({'git': g.exe})
        handle = NativePanelHandle(tool, config, parent)
        panel = handle.widget
        panel.resize(parent.size())
        parent.show()
        errors = []
        panel.warn = errors.append

        def wait():
            deadline = time.monotonic() + 25
            idle_samples = 0
            while time.monotonic() < deadline:
                app.processEvents()
                time.sleep(0.02)
                idle_samples = idle_samples + 1 if panel.proc is None else 0
                if idle_samples >= 6:
                    break
            assert idle_samples >= 6, 'Packaged Git worker did not become idle'
            assert not errors, errors

        def change(path):
            return next(panel.files.item(row) for row in range(panel.files.count())
                        if (panel.files.item(row).data(Qt.UserRole) or {}).get('path') == path)

        def invoke_action(view, show_menu, item, title):
            seen = []
            def trigger():
                menu = app.activePopupWidget()
                if isinstance(menu, QMenu):
                    action = next((action for action in menu.actions()
                                   if title in action.text() and action.isEnabled()), None)
                    menu.close()
                    if action is not None:
                        seen.append(action.text())
                        action.trigger()
            QTimer.singleShot(0, trigger)
            show_menu(view.visualItemRect(item).center())
            assert seen, f'Action {title!r} was unavailable'

        def find(tree, text):
            return tree.findItems(text, Qt.MatchFlag.MatchExactly | Qt.MatchFlag.MatchRecursive)

        panel.add_repo(str(repo))
        wait()
        assert {row['path'] for row in panel.state['files']} == {'说明.txt', '本地草稿.txt'}
        assert find(panel.remote_tree, '说明.txt')
        assert find(panel.local_tree, '说明.txt')
        assert find(panel.local_tree, '本地草稿.txt')
        assert not find(panel.remote_tree, '本地草稿.txt')
        selected = change('说明.txt')
        panel.files.setCurrentItem(selected)
        wait()
        assert not any(view.isVisibleTo(panel.changes_page) for view in panel.changes_page.findChildren(QPlainTextEdit))

        def invoke_change_action(item, title):
            invoke_action(panel.files, panel.changes_menu, item, title)

        def diff_text(open_diff):
            contents = []
            deadline = time.monotonic() + 25

            def capture():
                dialog = app.activeModalWidget()
                if dialog:
                    view = dialog.findChild(QPlainTextEdit)
                    if view:
                        contents.append(view.toPlainText())
                        dialog.accept()
                        return
                if time.monotonic() < deadline:
                    QTimer.singleShot(40, capture)

            QTimer.singleShot(40, capture)
            open_diff()
            wait()
            assert contents, 'The requested diff did not open in a dialog'
            return contents[0]

        assert '+新的内容' in diff_text(lambda: invoke_change_action(selected, '查看未加入的差异'))

        invoke_change_action(change('说明.txt'), '加入本次提交')
        wait()
        assert next(row['status'] for row in panel.state['files'] if row['path'] == '说明.txt') == 'M '
        invoke_change_action(change('说明.txt'), '移出本次提交')
        wait()
        assert (repo / '说明.txt').read_text('utf-8') == '新的内容\n'
        assert next(row['status'] for row in panel.state['files'] if row['path'] == '说明.txt') == ' M'
        invoke_change_action(change('说明.txt'), '加入本次提交')
        wait()
        panel.message.setText('界面提交')
        panel.commit_button.click()
        wait()
        assert [row['path'] for row in panel.state['files']] == ['本地草稿.txt']
        assert g.run('log', '-1', '--format=%s') == '界面提交'

        panel.amend.setChecked(True)
        panel.message.setText('界面提交')
        panel.description = '修订后的详细说明'
        panel.update_commit_ui()
        assert '修订' in panel.commit_button.text()
        panel.confirm = lambda text: True
        panel.commit_button.click()
        wait()
        assert not panel.amend.isChecked() and not panel.description
        assert '修订' not in panel.commit_button.text()
        assert '已填写详细说明' not in panel.commit_options.toolTip()
        panel.tabs.setCurrentIndex(1)
        wait()
        assert panel.history.count() == 2
        panel.history.setCurrentRow(0)
        wait()
        overview = panel.history_info.toPlainText()
        assert '界面提交' in overview and 'diff --git' not in overview
        assert '+新的内容' in diff_text(lambda: invoke_action(
            panel.history, panel.history_menu, panel.history.currentItem(), '查看此提交的差异'))
        assert parent.grab().save(str(OUTPUT / 'history.png'))
        panel.tabs.setCurrentIndex(0)

        (repo / '说明.txt').write_text('替换内容\n尾行\n', 'utf-8', newline='\n')
        panel.refresh()
        wait()
        panel.files.setCurrentItem(change('说明.txt'))
        partial_deadline = time.monotonic() + 25
        def select_partial():
            dialog = app.activeModalWidget()
            if dialog:
                rows = dialog.findChild(QListWidget)
                if rows:
                    for index in range(rows.count()):
                        item = rows.item(index)
                        if item.flags() & Qt.ItemIsUserCheckable:
                            item.setCheckState(Qt.Checked if item.text() == '+尾行' else Qt.Unchecked)
                    dialog.accept()
                    return
            if time.monotonic() < partial_deadline:
                QTimer.singleShot(40, select_partial)
        QTimer.singleShot(40, select_partial)
        invoke_change_action(change('说明.txt'), '逐行加入本次提交')
        wait()
        index_content = g.run('show', ':说明.txt', raw=True).decode('utf-8')
        assert index_content == '新的内容\n尾行\n', repr(index_content)
        assert parent.grab().save(str(OUTPUT / 'workspace.png'))

        handle.shutdown()
        handle.shutdown()
        assert panel.closed and not panel.timer.isActive()
        second = NativePanelHandle(tool, config, parent)
        panel = second.widget
        panel.warn = errors.append
        wait()
        assert repo.resolve() == Path(panel.repo).resolve()
        second.shutdown()
        parent.close()
    print('PASS: native host, packaged worker, remote and local trees, contextual diff, inclusion, removal, commit, history, partial staging and reopen')


if __name__ == '__main__':
    main()
