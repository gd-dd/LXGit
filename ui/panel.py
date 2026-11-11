"""Tool-owned native QWidget, host API 1. No host-global state changes."""
import json
import base64
from .runtime_paths import runtime_root, no_links
from pathlib import Path
from PySide6.QtCore import Qt,QProcess,QTimer,QUrl,QProcessEnvironment,QSize
from PySide6.QtGui import QDesktopServices,QPixmap,QFont,QSyntaxHighlighter,QTextCharFormat,QColor
from PySide6.QtWidgets import (QWidget,QVBoxLayout,QHBoxLayout,QLabel,QPushButton,QComboBox,QLineEdit,
    QTextEdit,QPlainTextEdit,QListWidget,QListWidgetItem,QSplitter,QTabWidget,QFileDialog,QInputDialog,
    QMessageBox,QMenu,QCheckBox,QDialog,QDialogButtonBox,QFormLayout,QAbstractItemView,QProgressBar,
    QSizePolicy,QScrollArea,QGridLayout)

STYLE='''
QWidget#LXGit { background:#f7faf3; color:#243227; font-family:"Microsoft YaHei UI"; font-size:13px; }
QWidget#LXGit QWidget {background:#f7faf3; color:#243227; font-family:"Microsoft YaHei UI"; font-size:13px;}
QWidget#LXGit QLabel {color:#344a35;}
QWidget#LXGit QPushButton {background:#ffffff; color:#2d432d; border:1px solid #c6d9b1; border-radius:6px; padding:6px 10px;}
QWidget#LXGit QPushButton:hover {background:#eaf4dc; border-color:#8dbb43;}
QWidget#LXGit QPushButton:disabled {color:#83907c; background:#edf1e8;}
QWidget#LXGit QPushButton#primary {background:#91bd45; color:#172810; font-weight:bold;}
QWidget#LXGit QLineEdit, QWidget#LXGit QPlainTextEdit, QWidget#LXGit QTextEdit, QWidget#LXGit QListWidget, QWidget#LXGit QComboBox {background:#ffffff; color:#243227; border:1px solid #d5e1ca; border-radius:5px; padding:6px; selection-background-color:#d8ebbb; selection-color:#243227;}
QWidget#LXGit QTabWidget::pane {border:1px solid #d5e1ca;}
QWidget#LXGit QTabBar::tab {background:#edf3e6; color:#53644a; padding:7px 16px;}
QWidget#LXGit QTabBar::tab:selected {color:#365c19; background:#deedc8;}
QWidget#LXGit QSplitter::handle {background:#d5e1ca; width:6px;}
QWidget#LXGit QMenu {background:#ffffff; color:#243227; border:1px solid #c6d9b1;}
QWidget#LXGit QMenu::item:selected {background:#deedc8; color:#243227;}
QWidget#LXGit QAbstractItemView::item:selected {background:#deedc8; color:#243227;}
QWidget#LXGit QLineEdit:focus, QWidget#LXGit QPlainTextEdit:focus {border-color:#8dbb43;}
'''
STATUS={'??':'新文件',' M':'已修改','M ':'已暂存','MM':'暂存后又修改','A ':'新增已暂存',' D':'已删除','D ':'删除已暂存','R ':'已重命名','UU':'内容冲突','AA':'双方新增','DU':'一方删除','UD':'一方删除'}
class ElidedLabel(QLabel):
    """Long project paths/diagnostics never set the workspace minimum width."""
    def __init__(self,text='',parent=None):
        super().__init__(parent);self.full_text='';self.setMinimumWidth(0);self.setTextFormat(Qt.PlainText)
        self.setSizePolicy(QSizePolicy.Ignored,QSizePolicy.Preferred);self.setText(text)
    def setText(self,text):
        self.full_text=str(text);self.setToolTip(self.full_text);self.setAccessibleName(self.full_text)
        super().setText(self.fontMetrics().elidedText(self.full_text,Qt.ElideMiddle,max(20,self.width())))
    def text(self):return self.full_text
    def clear(self):self.setText('')
    def resizeEvent(self,event):
        super().resizeEvent(event);self.setText(self.full_text)

class ImagePreview(QLabel):
    def __init__(self,pixmap,parent=None):
        super().__init__(parent);self.original=pixmap;self.setMinimumSize(60,60)
        self.setSizePolicy(QSizePolicy.Ignored,QSizePolicy.Ignored);self.setAlignment(Qt.AlignCenter)
    def resizeEvent(self,event):
        super().resizeEvent(event)
        self.setPixmap(self.original.scaled(self.contentsRect().size(),Qt.KeepAspectRatio,Qt.SmoothTransformation))

class DiffColors(QSyntaxHighlighter):
    def highlightBlock(self,text):
        color='#28733a' if text.startswith('+') else '#b83239' if text.startswith('-') else '#2866a6' if text.startswith('@@') else None
        if color:
            fmt=QTextCharFormat();fmt.setForeground(QColor(color));self.setFormat(0,len(text),fmt)

class Panel(QWidget):
    def __init__(self,context,parent):
        super().__init__(parent);self.context=context;self.settings=context.load_settings();self.repo='';self.description='';self.state={};self.proc=None;self.closed=False;self.after=None
        self.setObjectName('LXGit');self.setAttribute(Qt.WA_StyledBackground,True);self.setStyleSheet(STYLE)
        root=QVBoxLayout(self);root.setContentsMargins(10,8,10,8);root.setSpacing(6)
        self.header_bar=QWidget();bar=QHBoxLayout(self.header_bar);bar.setContentsMargins(0,0,0,0);bar.setSpacing(6)
        self.repositories=QComboBox();self.repositories.setMinimumWidth(80);self.repositories.setMinimumContentsLength(4)
        self.repositories.setSizePolicy(QSizePolicy.Expanding,QSizePolicy.Fixed);self.repositories.setSizeAdjustPolicy(QComboBox.AdjustToMinimumContentsLengthWithIcon)
        self.repositories.setToolTip('选择当前游戏项目；添加和下载项目请打开右侧“项目”')
        for p in self.settings.get('repositories',[]):
            self.repositories.addItem(Path(p).name,p);self.repositories.setItemData(self.repositories.count()-1,p,Qt.ToolTipRole)
        bar.addWidget(self.repositories,1)
        self.project_button=self.button(bar,'项目',self.project_panel)
        self.branch_button=self.button(bar,'分支',self.branch_panel)
        self.sync_button=self.button(bar,'同步',self.sync_panel)
        self.tools_button=self.button(bar,'更多',self.tools_panel);root.addWidget(self.header_bar)
        self.branches=QComboBox(self);self.branches.setMinimumContentsLength(8);self.branches.setSizeAdjustPolicy(QComboBox.AdjustToMinimumContentsLengthWithIcon);self.branches.hide()
        self.summary=ElidedLabel('添加已有游戏项目，或从队友提供的地址下载项目。');root.addWidget(self.summary)
        self.git_ready=bool(self.settings.get('git'));self.git_pending=None;self.runtime_buffer=b'';self.runtime_cancelled=False
        self.runtime_box=QWidget();runtimebar=QHBoxLayout(self.runtime_box);runtimebar.setContentsMargins(0,0,0,0);runtimebar.setSpacing(6)
        self.runtime_label=ElidedLabel('Git：正在检查工具运行环境…');runtimebar.addWidget(self.runtime_label,1)
        self.runtime_progress=QProgressBar();self.runtime_progress.setTextVisible(False);self.runtime_progress.setAccessibleName('Git 准备进度');self.runtime_progress.setMinimumWidth(60);self.runtime_progress.setMaximumWidth(150);runtimebar.addWidget(self.runtime_progress)
        self.runtime_retry=self.button(runtimebar,'重试',self.prepare_git);self.runtime_cancel=self.button(runtimebar,'取消',self.cancel_git);self.runtime_cancel.setEnabled(False)
        self.button(runtimebar,'详情',lambda:self.show_text(self.runtime_label.text()));root.addWidget(self.runtime_box)
        self.operation=ElidedLabel();self.operation.hide();root.addWidget(self.operation)
        self.tabs=QTabWidget();root.addWidget(self.tabs,1)
        changes=QWidget();cl=QVBoxLayout(changes);cl.setContentsMargins(6,6,6,6);cl.setSpacing(6)
        self.workspace_split=QSplitter();split=self.workspace_split;split.setChildrenCollapsible(False);split.setHandleWidth(6);cl.addWidget(split,1)
        left=QWidget();left.setMinimumWidth(170);ll=QVBoxLayout(left);ll.setContentsMargins(0,0,0,0);ll.setSpacing(5)
        filebar=QHBoxLayout();filebar.setSpacing(4);self.filter=QLineEdit();self.filter.setMinimumWidth(0);self.filter.setPlaceholderText('搜索改动…');filebar.addWidget(self.filter,1);self.button(filebar,'文件…',self.changes_menu);ll.addLayout(filebar)
        self.files=QListWidget();self.files.setMinimumSize(0,100);self.files.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff);self.files.setTextElideMode(Qt.ElideMiddle);self.files.setSelectionMode(QAbstractItemView.ExtendedSelection);ll.addWidget(self.files,1)
        self.files_tools=QWidget();controls=QHBoxLayout(self.files_tools);controls.setContentsMargins(0,0,0,0);controls.setSpacing(4)
        self.button(controls,'选入',lambda:self.stage(False)).setToolTip('将选中文件选入本次版本（暂存）')
        self.button(controls,'移出',lambda:self.stage(True)).setToolTip('将选中文件移出本次版本（取消暂存）')
        self.files.setContextMenuPolicy(Qt.CustomContextMenu);self.files.customContextMenuRequested.connect(lambda _:self.changes_menu());ll.addWidget(self.files_tools);split.addWidget(left)
        right=QWidget();right.setMinimumWidth(210);rl=QVBoxLayout(right);rl.setContentsMargins(0,0,0,0);rl.setSpacing(5)
        diffbar=QHBoxLayout();self.file_label=ElidedLabel('选择文件查看差异');diffbar.addWidget(self.file_label,1)
        self.staged=QCheckBox('只看选入本次版本的改动',self);self.staged.hide()
        self.button(diffbar,'差异…',self.diff_menu);rl.addLayout(diffbar)
        self.diff=QPlainTextEdit();self.diff.setMinimumSize(0,100);self.diff.setReadOnly(True);self.diff.setFont(QFont('Consolas',11));self.diff.setStyleSheet('font-family:Consolas,"Microsoft YaHei UI";font-size:14px;');self.colors=DiffColors(self.diff.document());rl.addWidget(self.diff,1)
        split.addWidget(right);split.setStretchFactor(0,3);split.setStretchFactor(1,7);split.setSizes([280,650])
        commitbar=QHBoxLayout();commitbar.setSpacing(6);self.commit_actions=commitbar
        self.message=QLineEdit();self.message.setMinimumWidth(60);self.message.setPlaceholderText('这次做了什么？');self.message.setToolTip('填写这次修改的说明，例如：修复角色跳跃、完成第一关场景');commitbar.addWidget(self.message,1)
        self.amend=QCheckBox('修订最近提交',self);self.amend.hide()
        self.commit_options=self.button(commitbar,'选项…',self.commit_menu)
        self.commit_button=self.button(commitbar,'保存本地版本',self.commit);self.commit_button.setObjectName('primary');self.commit_button.setToolTip('仅保存到本机；需要分享时再打开“同步”上传给队友');cl.addLayout(commitbar)
        self.amend.toggled.connect(self.update_commit_ui);self.update_commit_ui()
        self.changes_page=changes;self.tabs.addTab(changes,'我修改的内容')
        history=QWidget();hl=QVBoxLayout(history);hl.setContentsMargins(6,6,6,6);hl.setSpacing(6)
        self.history_search=QLineEdit();self.history_search.setPlaceholderText('搜索已加载历史（最近 200 条）');hl.addWidget(self.history_search)
        self.history_split=QSplitter();hs=self.history_split;hs.setChildrenCollapsible(False);hs.setHandleWidth(6)
        self.history=QListWidget();self.history.setMinimumSize(150,70);self.history.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff);self.history.setTextElideMode(Qt.ElideMiddle);hs.addWidget(self.history)
        self.commit_diff=QPlainTextEdit();self.commit_diff.setMinimumSize(200,70);self.commit_diff.setReadOnly(True);self.commit_diff.setFont(QFont('Consolas',10));self.commit_diff.setStyleSheet('font-family:Consolas,"Microsoft YaHei UI";font-size:14px;');hs.addWidget(self.commit_diff)
        hs.setStretchFactor(0,3);hs.setStretchFactor(1,7);hs.setSizes([280,650]);hl.addWidget(hs,1)
        self.button(hl,'所选提交操作…',self.history_menu);self.history_page=history;self.tabs.addTab(history,'版本记录')
        stashpage=QWidget(self);sl=QVBoxLayout(stashpage);self.stashes=QListWidget();sl.addWidget(self.stashes,1);sb=QVBoxLayout();self.button(sb,'储藏当前改动',self.stash_save)
        for label,op in [('应用并保留','stash_apply'),('恢复并移除','stash_pop'),('删除储藏','stash_drop')]:self.button(sb,label,lambda checked=False,o=op:self.stash_action(o))
        sl.addLayout(sb);stashpage.hide()
        self.log=QPlainTextEdit(self);self.log.setReadOnly(True);self.log.hide();self.aux_pages={'储藏改动':stashpage,'操作记录':self.log}
        footer=QHBoxLayout();self.status=ElidedLabel('就绪');footer.addWidget(self.status,1)
        self.environment_status=ElidedLabel('Git 正在检查');self.environment_status.setSizePolicy(QSizePolicy.Fixed,QSizePolicy.Preferred);self.environment_status.setFixedWidth(104);footer.addWidget(self.environment_status);root.addLayout(footer)
        self.repositories.currentIndexChanged.connect(self.select_repo);self.files.currentItemChanged.connect(self.show_diff);self.staged.toggled.connect(self.show_diff)
        self.filter.textChanged.connect(self.filter_files);self.history.currentItemChanged.connect(self.show_commit);self.history_search.textChanged.connect(self.filter_history)
        self.tabs.currentChanged.connect(self.tab_changed)
        self.timer=QTimer(self);self.timer.setInterval(15000);self.timer.timeout.connect(self.auto_refresh);self.timer.start()
        if self.repositories.count():
            index=self.repositories.findData(self.settings.get('last_repo'));self.repositories.setCurrentIndex(max(index,0));self.repo=self.repositories.currentData();QTimer.singleShot(0,self.refresh)
        QTimer.singleShot(0,self.prepare_git)
    def resizeEvent(self,event):
        super().resizeEvent(event)
        if not hasattr(self,'changes_page'):return
        compact=self.height()<430
        self.summary.setVisible(not compact);self.files_tools.setVisible(not compact)
        self.layout().setContentsMargins(8,4 if compact else 8,8,4 if compact else 8)
        margin=4 if compact else 6;self.changes_page.layout().setContentsMargins(margin,margin,margin,margin)
    def minimumSizeHint(self):
        hint=super().minimumSizeHint()
        if not hasattr(self,'history_page'):return hint
        # Advertise the supported compact layout before the first resize. Otherwise
        # Qt enforces the spacious layout minimum and never reaches compact mode.
        split=self.workspace_split;left=split.widget(0);right=split.widget(1)
        left_height=left.minimumSizeHint().height()
        if not self.files_tools.isHidden():left_height-=self.files_tools.minimumSizeHint().height()+left.layout().spacing()
        split_height=max(left_height,right.minimumSizeHint().height())
        changes_height=self.changes_page.minimumSizeHint().height()-(split.minimumSizeHint().height()-split_height)
        margins=self.changes_page.layout().contentsMargins();changes_height-=max(0,margins.top()+margins.bottom()-8)
        pages_height=max(changes_height,self.history_page.minimumSizeHint().height())
        normal_pages=max(self.changes_page.minimumSizeHint().height(),self.history_page.minimumSizeHint().height())
        height=hint.height()-max(0,normal_pages-pages_height)
        if not self.summary.isHidden():height-=self.summary.minimumSizeHint().height()+self.layout().spacing()
        margins=self.layout().contentsMargins();height-=max(0,margins.top()+margins.bottom()-8)
        return QSize(hint.width(),height)
    def worker_environment(self,p):
        root=self.context.data_root/'worker-temp' if self.settings.get('git') else runtime_root(self.context.package_root)/'tmp'
        temp=no_links(root);temp.mkdir(parents=True,exist_ok=True)
        env=QProcessEnvironment.systemEnvironment();env.insert('TEMP',str(temp));env.insert('TMP',str(temp));p.setProcessEnvironment(env)
    def runtime_view(self,ready=False):
        self.runtime_box.setVisible(not ready)
        self.environment_status.setText('Git 已就绪' if ready else 'Git 尚未就绪')
        self.environment_status.setToolTip(self.runtime_label.text())
    def prepare_git(self):
        if self.closed or self.proc:return
        if self.settings.get('git'):
            self.git_ready=True;self.runtime_label.setText('使用手动选择的 Git');self.runtime_progress.hide();self.runtime_view(True);return
        try:runtime_root(self.context.package_root)
        except Exception as error:
            self.runtime_label.setText(str(error));self.runtime_view();return
        self.git_ready=False;self.runtime_cancelled=False;self.runtime_buffer=b''
        self.runtime_view()
        self.runtime_label.setText('正在准备工具私有 Git…');self.runtime_progress.show();self.runtime_progress.setRange(0,0)
        self.runtime_retry.setEnabled(False);self.runtime_cancel.setEnabled(True)
        self.proc=QProcess(self);p=self.proc;self.after=('prepare_git',None,False)
        try:
            self.worker_environment(p)
            p.setProgram(str(self.context.resource('bin/LXGit-worker.exe')))
        except Exception as error:
            self.start_error(p,error);return
        p.readyReadStandardOutput.connect(self.runtime_output);p.finished.connect(self.runtime_finished);p.errorOccurred.connect(self.runtime_failed)
        request={'op':'prepare_git','package':str(self.context.package_root)}
        p.started.connect(lambda:p.write((json.dumps(request,ensure_ascii=False)+'\n').encode('utf-8')))
        p.start()
    def runtime_output(self):
        if not self.proc:return
        self.runtime_buffer+=bytes(self.proc.readAllStandardOutput())
        while b'\n' in self.runtime_buffer:
            line,self.runtime_buffer=self.runtime_buffer.split(b'\n',1)
            try:row=json.loads(line)
            except ValueError:continue
            if 'progress' in row:
                self.runtime_label.setText(row['progress']);total=row.get('total',0);done=row.get('done',0)
                self.runtime_progress.setRange(0,100 if total else 0)
                if total:self.runtime_progress.setValue(int(done*100/total))
    def start_error(self,p,error):
        self.proc=None;self.after=None;self.git_pending=None;p.deleteLater()
        self.runtime_retry.setEnabled(True);self.runtime_cancel.setEnabled(False);self.runtime_progress.setRange(0,100);self.runtime_progress.setValue(0)
        message='无法准备后台运行目录或启动资源：'+str(error)+'。请检查工具目录写入权限和磁盘空间后重试。'
        self.runtime_label.setText(message);self.status.setText('后台未启动');self.log.appendPlainText(message)
        self.runtime_view()
    def runtime_failed(self,error):
        if error==QProcess.FailedToStart:self.runtime_finished(-1,None)
    def runtime_finished(self,code,status):
        if self.closed or not self.proc:return
        self.runtime_output();p=self.proc;self.proc=None;self.after=None;p.deleteLater()
        self.runtime_retry.setEnabled(True);self.runtime_cancel.setEnabled(False);self.runtime_progress.setRange(0,100)
        try:result=json.loads(self.runtime_buffer)
        except ValueError:result={'ok':False,'error':'Git 准备中断，请重试。'}
        if result.get('ok') and code==0 and status==QProcess.NormalExit and not self.runtime_cancelled:
            self.git_ready=True;self.runtime_progress.setValue(100);self.runtime_label.setText('工具私有 Git '+result['data']['version']+' 已就绪（离线可用）')
            self.runtime_view(True)
            pending=self.git_pending;self.git_pending=None
            if pending:self.run(*pending)
        else:
            self.git_pending=None;self.runtime_progress.setValue(0);self.runtime_label.setText('已取消，可重试' if self.runtime_cancelled else result.get('error','Git 后台异常退出，请重试。'))
            self.log.appendPlainText(self.runtime_label.text())
            self.runtime_view()
    def cancel_git(self):
        if self.proc and self.after and self.after[0]=='prepare_git':
            self.runtime_cancelled=True;self.git_pending=None;self.proc.write(b'c');self.runtime_label.setText('正在取消并清理临时文件…');self.runtime_cancel.setEnabled(False)
    def use_managed_git(self):
        if self.proc:return
        self.settings.pop('git',None);self.save();self.git_ready=False;self.prepare_git()
    def button(self,layout,text,callback):
        b=QPushButton(text);b.clicked.connect(callback);layout.addWidget(b);return b
    def dialog(self,title,width=560,height=500):
        d=QDialog(self);d.setWindowTitle(title)
        screen=self.screen().availableGeometry();d.resize(min(width,screen.width()-40),min(height,screen.height()-60));return d
    def actions_panel(self,title,groups,intro=''):
        d=self.dialog(title);layout=QVBoxLayout(d)
        if intro:
            note=ElidedLabel(intro);layout.addWidget(note)
        scroll=QScrollArea();scroll.setWidgetResizable(True);scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        body=QWidget();items=QVBoxLayout(body);items.setSpacing(8)
        for heading,actions in groups:
            label=QLabel(heading);label.setStyleSheet('font-weight:600;color:#416b24');items.addWidget(label)
            for text,callback in actions:
                def invoke(checked=False,fn=callback):d.accept();fn()
                self.button(items,text,invoke)
        items.addStretch();scroll.setWidget(body);layout.addWidget(scroll,1)
        self.button(layout,'返回工作区',d.accept)
        try:d.exec()
        finally:d.deleteLater()
    def project_panel(self):
        self.actions_panel('项目管理',[
            ('加入或创建项目',[('下载团队项目…',self.clone_repo),('添加已有项目…',lambda:self.add_repo(QFileDialog.getExistingDirectory(self,'选择已有游戏项目'))),('为本地项目建立版本管理…',self.create_repo)]),
            ('当前项目',[('打开项目文件夹',lambda:QDesktopServices.openUrl(QUrl.fromLocalFile(self.repo))),('刷新本地改动',self.refresh),('项目配置与高级操作…',self.repo_menu)])],self.repo or '先加入团队项目，再开始保存版本。')
    def branch_panel(self):
        d=self.dialog('分支管理',540,340);layout=QVBoxLayout(d)
        label=ElidedLabel('当前分支：'+self.state.get('branch','尚未选择项目'));layout.addWidget(label)
        layout.addWidget(QLabel('选择要切换到的分支'));layout.addWidget(self.branches);self.branches.show()
        def switch():d.accept();self.switch()
        self.button(layout,'切换到所选分支',switch)
        self.button(layout,'新建分支…',lambda:(d.accept(),self.name_op('branch_create','新分支名称')))
        self.button(layout,'合并、变基和标签等高级操作…',lambda:(d.accept(),self.branch_menu()))
        layout.addStretch();self.button(layout,'返回工作区',d.accept)
        try:d.exec()
        finally:self.branches.hide();self.branches.setParent(self);d.deleteLater()
    def sync_panel(self):
        self.actions_panel('与队友同步',[
            ('1 · 开始工作前',[('检查队友更新',lambda:self.run('fetch')),('下载更新到当前分支',lambda:self.run('pull'))]),
            ('2 · 保存本地版本后',[('上传已保存的版本给队友',self.push)]),
            ('高级同步',[('变基拉取 / 安全强制推送…',self.advanced_sync_menu)])],
            '检查只获取信息；下载合入队友版本；上传分享已保存的本地版本。')
    def tools_panel(self):
        self.actions_panel('更多功能',[
            ('GitHub 协作',[('账号、远程仓库与拉取请求…',self.github_menu)]),
            ('工作与恢复',[('储藏改动',lambda:self.show_auxiliary('储藏改动')),('操作记录',lambda:self.show_auxiliary('操作记录'))]),
            ('运行环境',[('Git、服务地址、编辑器与签名设置…',self.settings_menu)]),
            ('使用帮助',[('新手指南',self.beginner_guide),('关于 LXGit',self.help)])])
    def show_auxiliary(self,title):
        d=self.dialog(title,760,520);layout=QVBoxLayout(d);page=self.aux_pages[title];layout.addWidget(page);page.show()
        self.button(layout,'返回工作区',d.accept)
        try:d.exec()
        finally:page.hide();page.setParent(self);d.deleteLater()
    def changes_menu(self):
        self.menu([('选入全部改动',self.stage_all),('选入所选文件',lambda:self.stage(False)),('移出所选文件',lambda:self.stage(True)),('所选文件的打开、历史与冲突操作…',self.file_menu)])
    def diff_menu(self):
        m=QMenu(self);a=m.addAction('只看选入本次版本的改动');a.setCheckable(True);a.setChecked(self.staged.isChecked());a.toggled.connect(self.staged.setChecked)
        m.addSeparator();m.addAction('部分暂存：勾选修改行…',self.partial_stage)
        try:m.exec(self.cursor().pos())
        finally:m.deleteLater()
    def commit_menu(self):
        m=QMenu(self);m.addAction('编辑详细说明…',self.edit_description)
        a=m.addAction('修订最近提交');a.setCheckable(True);a.setChecked(self.amend.isChecked());a.toggled.connect(self.amend.setChecked)
        try:m.exec(self.cursor().pos())
        finally:m.deleteLater();self.update_commit_ui()
    def update_commit_ui(self):
        self.commit_options.setToolTip(('已启用：修订最近提交\n' if self.amend.isChecked() else '')+('已填写详细说明' if self.description else '编辑详细说明或修订最近提交'))
        self.commit_button.setText('修订本地版本' if self.amend.isChecked() else '保存本地版本')
    def advanced_sync_menu(self):
        self.menu([('变基拉取',lambda:self.confirm_run('pull','拉取远程并重放本地提交？',{'rebase':True})),('安全强制推送…',lambda:self.confirm_run('push','使用 force-with-lease 推送改写后的历史。可能替换远端已有提交，确认继续？',{'force':True}))])
    def ask(self,title,label,value=''):
        d=QInputDialog(self);d.setWindowTitle(title);d.setLabelText(label);d.setTextValue(value);d.setOkButtonText('确定');d.setCancelButtonText('取消')
        value=d.textValue().strip() if d.exec() else None;d.deleteLater();return value
    def pick(self,title,label,items):
        d=QInputDialog(self);d.setWindowTitle(title);d.setLabelText(label);d.setComboBoxItems(items);d.setComboBoxEditable(False);d.setOkButtonText('确定');d.setCancelButtonText('取消');ok=d.exec();value=d.textValue();d.deleteLater();return value,bool(ok)
    def number(self,title,label,value,minimum,maximum):
        d=QInputDialog(self);d.setWindowTitle(title);d.setLabelText(label);d.setInputMode(QInputDialog.IntInput);d.setIntRange(minimum,maximum);d.setIntValue(value);d.setOkButtonText('确定');d.setCancelButtonText('取消');ok=d.exec();result=d.intValue();d.deleteLater();return result,bool(ok)
    def edit_description(self):
        d=self.dialog('提交详细说明',600,350);l=QVBoxLayout(d);edit=QPlainTextEdit(self.description);l.addWidget(edit);b=QPushButton('保存说明');b.clicked.connect(d.accept);l.addWidget(b)
        if d.exec():self.description=edit.toPlainText().strip();self.update_commit_ui()
        d.deleteLater()
    def confirm(self,text):
        box=QMessageBox(self);box.setWindowTitle('确认操作');box.setText(text);yes=box.addButton('确认执行',QMessageBox.AcceptRole);box.addButton('取消',QMessageBox.RejectRole);box.exec();accepted=box.clickedButton() is yes;box.deleteLater();return accepted
    def warn(self,text):
        box=QMessageBox(self);box.setWindowTitle('操作提示');box.setText(text);box.addButton('知道了',QMessageBox.AcceptRole);box.exec();box.deleteLater()
    def menu(self,entries):
        m=QMenu(self)
        for label,fn in entries:
            if fn is None:m.addSection(label)
            else:m.addAction(label,fn)
        try:m.exec(self.cursor().pos())
        finally:m.deleteLater()
    def save(self):
        self.settings['last_repo']=self.repo;self.context.save_settings(self.settings)
    def run(self,op,args=None,callback=None,refresh=True):
        if self.closed:return
        if not self.git_ready:
            self.git_pending=(op,args,callback,refresh);self.prepare_git();self.status.setText('准备 Git 后继续操作…');return
        if self.proc:
            self.status.setText('上一项操作还在进行，请稍候。');return
        if not self.repo and op not in ('environment','init','clone','login','github_user','github_repos','github_create'):
            self.warn('请先添加、创建或克隆一个仓库。');return
        request={'op':op,'args':args or {},'repo':self.repo,'git':self.settings.get('git',''),'host':self.settings.get('github_host','github.com'),'package':str(self.context.package_root)}
        self.proc=QProcess(self);p=self.proc;self.status.setText('正在处理，请稍候…');self.after=(op,callback,refresh)
        try:
            self.worker_environment(p)
            p.setProgram(str(self.context.resource('bin/LXGit-worker.exe')))
        except Exception as error:
            self.start_error(p,error);return
        p.finished.connect(self.finished);p.errorOccurred.connect(self.failed)
        p.started.connect(lambda:(p.write(json.dumps(request,ensure_ascii=False).encode('utf-8')),p.closeWriteChannel()))
        p.start()
    def failed(self,error):
        if self.closed:return
        if error==QProcess.FailedToStart:
            p=self.proc;self.proc=None;self.after=None
            if p:p.deleteLater()
            self.warn('后台程序无法启动，请检查工具包是否完整。');self.status.setText('启动失败')
    def finished(self,code,status):
        if self.closed:return
        p=self.proc
        if not p:return
        meta=self.after;self.after=None;self.proc=None
        data=bytes(p.readAllStandardOutput());err=bytes(p.readAllStandardError()).decode('utf-8','replace');p.deleteLater()
        op,callback,refresh=meta
        try:result=json.loads(data.decode('utf-8'))
        except Exception:result={'ok':False,'error':'后台异常退出。'+err[-1500:]}
        if not result.get('ok'):
            self.status.setText('操作未完成，详情见操作记录');self.log.appendPlainText('操作失败：'+result.get('error','未知错误'));self.warn(result.get('error','未知错误'))
            if op not in ('snapshot','history','diff','show'):QTimer.singleShot(0,self.refresh)
            return
        value=result.get('data');self.status.setText('就绪')
        if op not in ('snapshot','history','diff','show','github_user','github_repos','github_prs','environment'):
            self.log.appendPlainText('操作完成\n'+(value if isinstance(value,str) else '请求已完成'))
        if callback:callback(value)
        elif refresh:QTimer.singleShot(0,self.refresh)
    def auto_refresh(self):
        if self.repo and not self.proc and self.isVisible() and not self.window().isModal():
            from PySide6.QtWidgets import QApplication
            if QApplication.activeModalWidget() is None:self.refresh()
    def refresh(self):
        if self.repo:self.run('snapshot',callback=self.render,refresh=False)
    def render(self,state):
        self.state=state;self.repo=str(Path(state['root']).resolve());self.summary.setText(f"{state['branch']}  ·  待推送 {state['ahead']}  /  待拉取 {state['behind']}  ·  {len(state['files'])} 个改动")
        self.summary.setToolTip(self.repo+'\n'+self.summary.text()+'\n'+state['remotes']);self.repositories.setToolTip(self.repo);self.branch_button.setToolTip('当前分支：'+state['branch']);self.branches.clear();self.branches.addItems(state['branches'])
        self.branches.setCurrentText(state['branch']);self.operation.setText('当前有未完成的'+ ' / '.join({'merge':'合并','rebase':'变基','cherry-pick':'拣选','revert':'反向提交'}.get(x,x) for x in state['operations'])+'。请解决冲突后在“分支”菜单继续或中止。' if state['operations'] else '')
        self.operation.setVisible(bool(state['operations']))
        old=self.files.currentItem();oldpath=old.data(Qt.UserRole)['path'] if old else None
        self.files.blockSignals(True);self.files.clear()
        for f in state['files']:
            item=QListWidgetItem(('⚠ ' if f['conflict'] else '')+STATUS.get(f['status'],'状态变更 '+f['status'])+'  '+f['path']);item.setData(Qt.UserRole,f);item.setToolTip(f['path']);self.files.addItem(item)
            if f['path']==oldpath:self.files.setCurrentItem(item)
        self.files.blockSignals(False);self.filter_files();self.stashes.clear();self.stashes.addItems(state['stashes'])
        if self.tabs.currentWidget() is self.history_page:self.load_history()
    def select_repo(self):
        if self.proc:
            self.repositories.blockSignals(True);self.repositories.setCurrentIndex(self.repositories.findData(self.repo));self.repositories.blockSignals(False);return
        self.repo=self.repositories.currentData() or '';self.diff.clear();self.history.clear();self.commit_diff.clear();self.save()
        if not self.repo:
            self.state={};self.files.clear();self.branches.clear();self.stashes.clear();self.operation.clear();self.operation.hide();self.summary.setText('从队友的 GitHub 地址下载游戏项目，或添加电脑上已有的项目。')
        else:self.refresh()
    def add_repo(self,path):
        if path:
            path=str(Path(path).resolve());repos=self.settings.setdefault('repositories',[])
            if path not in repos:repos.append(path);self.repositories.addItem(Path(path).name,path);self.repositories.setItemData(self.repositories.count()-1,path,Qt.ToolTipRole)
            self.repositories.setCurrentIndex(self.repositories.findData(path));self.repo=path;self.save();self.refresh()
    def repo_menu(self):
        self.menu([('项目信息',None),('提交者姓名和邮箱…',self.identity),('远程地址…',self.remote),('添加忽略规则…',self.ignore),
        ('开发工具',None),('比较当前分支…',self.compare),('在外部编辑器打开',self.open_editor),
        ('大文件与子模块',None),('初始化 Git LFS',lambda:self.run('lfs_install')),('跟踪大文件类型…',self.lfs_track),('下载 LFS 文件',lambda:self.run('lfs_pull')),('更新所有子模块',lambda:self.run('submodules')),
        ('项目列表',None),('从列表移除（保留文件）',self.remove_repo)])
    def create_repo(self):
        path=QFileDialog.getExistingDirectory(self,'选择新仓库目录')
        if path:self.run('init',{'path':path},lambda _:self.add_repo(path))
    def clone_repo(self,url=None):
        url=url or self.ask('克隆仓库','HTTPS / SSH 仓库地址')
        if not url:return
        parent=QFileDialog.getExistingDirectory(self,'选择保存仓库的父目录')
        if not parent:return
        name=self.ask('目录名称','新目录名称',url.rstrip('/').split('/')[-1].removesuffix('.git'))
        if not name or Path(name).name!=name or name in ('.','..'):return
        target=str(Path(parent)/name);self.run('clone',{'url':url,'path':target},lambda _:self.add_repo(target))
    def remove_repo(self):
        if self.proc:return
        repos=self.settings.get('repositories',[])
        if self.repo in repos:repos.remove(self.repo)
        self.repositories.removeItem(self.repositories.currentIndex());self.save()
    def selected(self):return [i.data(Qt.UserRole) for i in self.files.selectedItems()]
    def stage(self,unstage=False):
        paths=[f['path'] for f in self.selected()]
        if paths:self.run('unstage' if unstage else 'stage',{'paths':paths})
    def stage_all(self):
        paths=[f['path'] for f in self.state.get('files',[])]
        if paths:self.run('stage',{'paths':paths})
    def show_diff(self,*_):
        item=self.files.currentItem()
        if not item or self.proc:return
        f=item.data(Qt.UserRole);self.file_label.setText(f['path']);self.run('diff',{'path':f['path'],'staged':self.staged.isChecked(),'untracked':f['status']=='??'},self.diff.setPlainText,False)
    def filter_files(self):
        for i in range(self.files.count()):self.files.item(i).setHidden(self.filter.text().lower() not in self.files.item(i).text().lower())
    def partial_stage(self):
        item=self.files.currentItem()
        if not item or item.data(Qt.UserRole)['status']=='??' or 'D' in item.data(Qt.UserRole)['status']:self.warn('请先选择已跟踪的文本文件。新增和删除文件请使用整文件暂存。');return
        self.run('diff',{'path':item.data(Qt.UserRole)['path']},self.patch_dialog,False)
    def patch_dialog(self,patch):
        if not patch or 'Binary files' in patch:self.warn('没有可逐行暂存的文本差异。');return
        d=self.dialog('部分暂存 · 选择修改行',950,650);l=QVBoxLayout(d);note=QLabel('勾选要暂存的新增或删除行；未勾选的修改留在工作区。');note.setWordWrap(True);l.addWidget(note)
        rows=QListWidget();rows.setFont(QFont('Consolas',10));l.addWidget(rows,1);lines=patch.splitlines(keepends=True);choices={};in_hunk=False
        for index,line in enumerate(lines):
            if line.startswith('@@'):in_hunk=True
            item=QListWidgetItem(line.rstrip('\r\n'));rows.addItem(item)
            if in_hunk and line.startswith(('+','-')):
                item.setFlags(item.flags()|Qt.ItemIsUserCheckable);item.setCheckState(Qt.Checked);choices[index]=item;item.setForeground(QColor('#28733a' if line.startswith('+') else '#b83239'))
        b=QPushButton('暂存勾选的修改');l.addWidget(b);b.clicked.connect(d.accept)
        accepted=d.exec();d.deleteLater()
        if accepted:
            if not any(item.checkState()==Qt.Checked for item in choices.values()):return
            output=[];dropped=False
            for index,line in enumerate(lines):
                if index in choices and choices[index].checkState()!=Qt.Checked:
                    dropped=line.startswith('+')
                    if line.startswith('-'):output.append(' '+line[1:])
                elif line.startswith('\\ No newline') and dropped:continue
                else:output.append(line);dropped=False
            self.run('apply_patch',{'patch':''.join(output)})
    def commit(self):
        if not self.message.text().strip():self.warn('请填写提交说明。');return
        if self.amend.isChecked() and not self.confirm('修订将替换最近一次提交。若已推送，后续需要谨慎强制推送。继续吗？'):return
        self.run('commit',{'message':self.message.text()+('\n\n'+self.description if self.description else ''),'amend':self.amend.isChecked()},self.committed)
    def committed(self,_):self.description='';self.message.clear();self.amend.setChecked(False);self.update_commit_ui();self.refresh()
    def file_menu(self):
        item=self.files.currentItem()
        if not item:return
        f=item.data(Qt.UserRole);path=f['path']
        entries=[('打开文件',lambda:QDesktopServices.openUrl(QUrl.fromLocalFile(str(Path(self.repo)/path)))),('查看此文件历史',lambda:self.run('file_history',{'path':path},self.show_text,False)),('图片前后对比',lambda:self.run('image_diff',{'path':path},self.image_dialog,False)),('丢弃未暂存修改…',lambda:self.discard(path))]
        if f['conflict']:
            entries.extend([('解决冲突：采用当前一方…',lambda:self.resolve(path,'ours')),('解决冲突：采用传入一方…',lambda:self.resolve(path,'theirs')),('已手动解决，标记完成',lambda:self.run('stage',{'paths':[path]}))])
        self.menu(entries)
    def discard(self,path):
        if self.confirm('将丢弃此文件的未暂存修改，恢复到暂存区版本：\n'+path+'\n此操作无法撤销。'):
            self.run('discard',{'path':path})
    def resolve(self,path,side):
        if self.confirm('采用所选一方内容并暂存 '+path+'？\n变基过程中，“当前一方”是目标分支，“传入一方”是正在重放的提交。'):
            self.run('resolve',{'path':path,'side':side})
    def switch(self):
        branch=self.branches.currentText()
        if branch and branch!=self.state.get('branch'):self.run('switch',{'branch':branch})
    def branch_menu(self):
        self.menu([('分支管理',None),('新建分支…',lambda:self.name_op('branch_create','新分支名称')),('重命名当前分支…',lambda:self.name_op('branch_rename','新分支名称')),('删除已合并分支…',lambda:self.name_op('branch_delete','要删除的分支名称')),
        ('合并与恢复',None),('合并分支…',lambda:self.ref_op('merge','合并来源分支')),('变基到分支…',lambda:self.ref_op('rebase','目标分支')),('压缩合并分支…',lambda:self.ref_op('squash_merge','合并来源分支')),
        ('继续当前操作',lambda:self.run('continue')),('中止当前操作…',lambda:self.confirm_run('abort','中止当前合并、变基或拣选，恢复操作前状态？')),
        ('标签',None),('新建标签…',lambda:self.name_op('tag_create','标签名称')),('删除本地标签…',lambda:self.name_op('tag_delete','标签名称')),('推送全部标签…',lambda:self.confirm_run('push_tags','将全部本地标签推送到 origin？'))])
    def name_op(self,op,prompt):
        name=self.ask('分支与标签',prompt)
        if name:self.run(op,{'name':name})
    def ref_op(self,op,prompt):
        value=self.ask('分支操作',prompt,'main')
        if value and self.confirm('将对当前分支执行此操作，目标：'+value+'。继续吗？'):self.run(op,{'ref':value})
    def confirm_run(self,op,text,args=None):
        if self.confirm(text):self.run(op,args)
    def push(self):self.confirm_run('push','把当前分支中保存好的版本上传给队友？未保存为版本的修改不会上传。首次上传会建立远程跟踪关系。')
    def load_history(self):self.run('history',callback=self.render_history,refresh=False)
    def render_history(self,rows):
        self.history.blockSignals(True);self.history.clear()
        for r in rows:
            item=QListWidgetItem(r['short']+'  '+r['subject']+'\n'+r['author']+' · '+r['date'][:10]+' '+r['refs']);item.setData(Qt.UserRole,r);item.setToolTip(item.text());self.history.addItem(item)
        self.history.blockSignals(False);self.filter_history()
    def filter_history(self):
        for i in range(self.history.count()):self.history.item(i).setHidden(self.history_search.text().lower() not in self.history.item(i).text().lower())
    def show_commit(self,*_):
        item=self.history.currentItem()
        if item and not self.proc:self.run('show',{'ref':item.data(Qt.UserRole)['sha']},self.commit_diff.setPlainText,False)
    def history_menu(self):
        item=self.history.currentItem();ref=item.data(Qt.UserRole)['sha'] if item else None
        entries=[('压缩最近几条提交…',lambda:self.rewrite_history('squash')),('重新排列最近提交…',lambda:self.rewrite_history('reorder')),('撤销最近提交（保留暂存内容）…',lambda:self.confirm_run('undo','撤销最近提交并保留其改动到暂存区？已发布提交建议使用反向提交。')),('查看操作恢复日志',lambda:self.run('reflog',callback=self.show_text,refresh=False))]
        if ref:
            entries.extend([('反向提交所选改动…',lambda:self.confirm_run('revert','创建反向提交撤销所选提交？',{'ref':ref})),('拣选到当前分支…',lambda:self.confirm_run('cherry-pick','将所选提交复制到当前分支？',{'ref':ref})),('重置到此提交（保留工作文件）…',lambda:self.confirm_run('reset','移动当前分支到所选提交，保留工作目录差异。继续吗？',{'ref':ref}))])
        self.menu(entries)
    def image_dialog(self,data):
        d=self.dialog('图片差异 · 最近提交与工作区',1000,650);layout=QVBoxLayout(d);l=QHBoxLayout();layout.addLayout(l,1)
        for key,title in [('before','最近提交'),('after','工作区')]:
            col=QVBoxLayout();col.addWidget(QLabel(title));image=QPixmap();image.loadFromData(base64.b64decode(data[key]));label=QLabel('无图片或格式不支持') if image.isNull() else ImagePreview(image)
            label.setAlignment(Qt.AlignCenter)
            col.addWidget(label,1);l.addLayout(col)
        self.button(layout,'返回工作区',d.accept)
        try:d.exec()
        finally:d.deleteLater()
    def rewrite_history(self,mode):
        count,ok=self.number('整理提交历史','最近几条提交（会创建恢复分支）',2,2,50)
        if not ok:return
        if mode=='squash':
            if self.confirm('把最近 '+str(count)+' 条普通提交压缩为一条？已推送的历史需要带租约的强制推送。'):
                self.run('rewrite_history',{'mode':mode,'count':count})
        else:
            def got(rows):
                if len(rows)<=count:self.warn('需要保留至少一条基础提交。');return
                d=self.dialog('重排提交 · 从早到晚',650,450);l=QVBoxLayout(d);note=QLabel('每行一个完整提交编号，调整行顺序。每个编号必须且只能出现一次。');note.setWordWrap(True);l.addWidget(note)
                e=QPlainTextEdit('\n'.join(r['sha'] for r in reversed(rows[:count])));l.addWidget(e);b=QPushButton('确认重排并创建恢复分支');b.clicked.connect(d.accept);l.addWidget(b)
                if d.exec():self.run('rewrite_history',{'mode':mode,'count':count,'order':e.toPlainText().splitlines()})
                d.deleteLater()
            self.run('history',callback=got,refresh=False)
    def tab_changed(self,index):
        if self.tabs.currentWidget() is self.history_page and self.repo and not self.proc:self.load_history()
    def stash_save(self):
        name=self.ask('储藏改动','储藏说明（包含未跟踪文件）','临时保存工作')
        if name is not None:self.run('stash_save',{'message':name})
    def stash_action(self,op):
        item=self.stashes.currentItem()
        if item and (op!='stash_drop' or self.confirm('删除所选储藏？未恢复的内容可能丢失。')):self.run(op,{'ref':item.text().split('\t')[0]})
    def remote(self):
        name=self.ask('远程仓库','远程名称','origin')
        if not name:return
        url=self.ask('远程仓库','HTTPS / SSH 地址（不要填写令牌）')
        if url:self.run('remote',{'name':name,'url':url})
    def identity(self):
        name=self.ask('提交者','姓名')
        if not name:return
        email=self.ask('提交者','邮箱（可用 GitHub 隐私邮箱）')
        if email:self.run('identity',{'name':name,'email':email})
    def ignore(self):
        value=self.ask('忽略规则','例如 Library/、Temp/ 或 *.tmp')
        if value:self.run('ignore',{'pattern':value})
    def lfs_track(self):
        value=self.ask('Git LFS','大文件匹配规则，例如 *.psd 或 *.fbx')
        if value:self.run('lfs_track',{'pattern':value})
    def settings_menu(self):
        self.menu([('使用工具管理的 Git / 修复检查',self.use_managed_git),('选择 Git 程序…',self.choose_git),('GitHub / Enterprise 服务地址…',self.host_setting),('选择外部编辑器…',self.choose_editor),('配置 GPG 提交签名…',self.signing),('检查运行环境',lambda:self.run('environment',callback=lambda r:self.show_text('Git：'+r['git']+'\n'+r['version']+'\n'+r['lfs']),refresh=False))])
    def choose_git(self):
        if self.proc:
            self.status.setText("请先取消 Git 准备或等待当前操作完成。");return
        path,_=QFileDialog.getOpenFileName(self,'选择 git.exe','','Git 程序 (git.exe)')
        if path:self.settings['git']=path;self.git_ready=True;self.runtime_label.setText('使用手动选择的 Git');self.runtime_view(True);self.save();self.run('environment',callback=lambda r:self.show_text('已配置 '+r['version']),refresh=False)
    def github_menu(self):
        self.menu([('账号与项目',None),('GitHub 登录（浏览器授权）',lambda:self.run('login',refresh=False)),('查看当前账号',lambda:self.run('github_user',callback=lambda r:self.show_text('已登录：'+r['login']),refresh=False)),('从我的 GitHub 仓库克隆…',lambda:self.run('github_repos',callback=self.pick_github_repo,refresh=False)),('创建 GitHub 仓库…',self.create_github_repo),('派生当前仓库…',lambda:self.confirm_run('github_fork','在当前 GitHub 账号下派生此仓库？')),('在浏览器打开仓库',lambda:self.run('github_url',callback=lambda url:QDesktopServices.openUrl(QUrl(url)),refresh=False)),('拉取请求',None),('查看并检出拉取请求…',lambda:self.run('github_prs',callback=self.pick_pr,refresh=False)),('创建拉取请求…',self.create_pr)])
    def pick_github_repo(self,rows):
        names=[r['name']+('（私有）' if r['private'] else '') for r in rows]
        if not names:self.warn('当前账号没有可访问的仓库。');return
        name,ok=self.pick('GitHub 仓库','选择要克隆的仓库',names)
        if ok:self.clone_repo(rows[names.index(name)]['url'])
    def create_github_repo(self):
        name=self.ask('创建 GitHub 仓库','仓库名称')
        if not name:return
        owner=self.ask('仓库归属','组织名称（留空表示个人账号）','')
        if owner is None:return
        choice,ok=self.pick('可见性','仓库可见性',['私有','公开'])
        if ok and self.confirm('将在当前 GitHub 账号下创建'+choice+'仓库 '+name+'，确认吗？'):
            self.run('github_create',{'name':name,'private':choice=='私有','owner':owner},lambda r:self.show_text('创建成功：'+r['html_url']+'\n远程地址：'+r['clone_url']+'\n请在仓库菜单设置 origin，再推送。'),False)
    def pick_pr(self,rows):
        if not rows:self.warn('没有打开的拉取请求。');return
        names=['#'+str(r['number'])+' '+r['title'] for r in rows];name,ok=self.pick('拉取请求','选择拉取请求',names)
        if not ok:return
        item=rows[names.index(name)]
        action,ok=self.pick('拉取请求操作','选择操作',['查看说明与检查状态','检出到本地分支','在浏览器打开'])
        if not ok:return
        if action=='检出到本地分支':self.run('github_pr_checkout',{'number':item['number']})
        elif action=='在浏览器打开':QDesktopServices.openUrl(QUrl(item['html_url']))
        else:self.run('github_pr_details',{'number':item['number']},self.pr_details,False)
    def pr_details(self,data):
        labels={'success':'成功','failure':'失败','pending':'等待中','completed':'已结束','in_progress':'进行中','queued':'排队中','neutral':'中性','skipped':'已跳过','cancelled':'已取消','timed_out':'超时',None:'尚无结果'}
        checks='\n'.join(c['name']+'：'+labels.get(c['conclusion'],labels.get(c['status'],c['status'])) for c in data['checks'])
        self.show_text(data['title']+'\n'+data['url']+'\n\n'+data['body']+'\n\n检查状态：'+labels.get(data['status'],str(data['status']))+'\n'+checks)
    def create_pr(self):
        title=self.ask('创建拉取请求','标题')
        if not title:return
        base=self.ask('创建拉取请求','目标分支','main')
        if not base:return
        mode,ok=self.pick('拉取请求类型','选择类型',['正式拉取请求','草稿拉取请求'])
        if ok and self.confirm('为当前已推送分支创建拉取请求，目标 '+base+'？'):self.run('github_pr_create',{'title':title,'base':base,'draft':mode=='草稿拉取请求'},lambda r:self.show_text('拉取请求已创建：'+r['html_url']),False)
    def compare(self):
        ref=self.ask('比较分支','比较基准分支','main')
        if ref:self.run('compare',{'ref':ref},self.show_text,False)
    def host_setting(self):
        host=self.ask('GitHub 服务','域名（GitHub.com 或自建 Enterprise 域名）',self.settings.get('github_host','github.com'))
        if host:self.settings['github_host']=host;self.save()
    def choose_editor(self):
        path,_=QFileDialog.getOpenFileName(self,'选择编辑器程序','','程序 (*.exe)')
        if path:self.settings['editor']=path;self.save()
    def open_editor(self):
        if not self.repo:return
        editor=self.settings.get('editor')
        if not editor:self.choose_editor();editor=self.settings.get('editor')
        if editor:QProcess.startDetached(editor,[self.repo],self.repo)
    def signing(self):
        mode,ok=self.pick('提交签名','仅修改当前仓库设置',['启用 GPG 签名','关闭 GPG 签名'])
        if not ok:return
        key=self.ask('GPG 签名','已安装的 GPG 密钥 ID') if mode=='启用 GPG 签名' else ''
        if key is not None:self.run('signing',{'enabled':mode=='启用 GPG 签名','key':key})
    def show_text(self,text):
        d=self.dialog('详细信息',800,520);l=QVBoxLayout(d);view=QPlainTextEdit(text);view.setReadOnly(True);l.addWidget(view);b=QPushButton('关闭');b.clicked.connect(d.accept);l.addWidget(b);d.exec();d.deleteLater()
    def beginner_guide(self):
        d=self.dialog('第一次和队友一起开发游戏',780,620);layout=QVBoxLayout(d)
        text=QPlainTextEdit('LXGit 帮你保存游戏项目的版本，并与队友交换修改。\n\n第一次加入团队\n① 向队友要 GitHub 项目地址，选择“下载团队项目”。\n② 在 GitHub 菜单登录自己的账号。私有项目需要队友邀请你加入。\n③ 在项目菜单设置你的姓名和邮箱，让大家知道谁做了修改。\n\n每天的工作顺序\n① 开始制作前：检查队友更新 → 下载更新。\n② 正常在 Unity、3ds Max 或编辑器中制作并保存文件。\n③ 回到“我修改的内容”，检查文件，选入本次版本。\n④ 写清这次做了什么，点击“保存本地版本”。此时只保存在你的电脑。\n⑤ 点击“上传给队友”，队友下载更新后才能拿到你的修改。\n\nUnity 项目\nAssets、Packages、ProjectSettings 和资源对应的 .meta 文件应由团队共同管理。Library、Temp、Logs 等生成目录通常应忽略；请由项目负责人维护 .gitignore，不要见到很多文件就全部上传。\n\n模型、贴图和音频\n.max、.fbx、.psd、音频等大文件可由负责人配置 Git LFS；配置会生成 .gitattributes，也要分享给队友。大文件使用 LFS 后仍可能冲突，修改同一场景或模型前先和队友沟通。\n\n遇到冲突\n不要随手选择“采用一方”。先找修改过该文件的队友确认，保留双方需要的工作，再标记解决。高级历史操作不属于日常必做步骤。')
        text.setReadOnly(True);layout.addWidget(text,1);row=QHBoxLayout();layout.addLayout(row)
        def download():d.accept();self.clone_repo()
        def existing():d.accept();self.add_repo(QFileDialog.getExistingDirectory(self,'选择已有游戏项目'))
        self.button(row,'下载团队项目',download);self.button(row,'添加已有项目',existing);self.button(row,'明白了',d.accept);d.exec();d.deleteLater()
    def help(self):
        self.show_text('LXGit 游戏协作 0.0.7\n\n添加仓库 → 设置提交者 → 选择改动 → 暂存 → 填写说明 → 提交 → 推送。\nGitHub 登录通过 Git Credential Manager，工具不保存令牌。\n冲突文件可手动编辑后暂存，或在文件菜单选择一方；随后在分支菜单继续。\n部分暂存支持逐行勾选文本修改。\n所有用户操作和错误记录在本次面板的操作记录中。\n\n首次打开会自动准备工具私有 Git，可取消并重试；也可在设置中手动选择 Git。完整功能与验证范围见工具目录 README.md。\n历史整理支持压缩和编号重排；暂不支持拖动重排。Enterprise、组织和签名功能需要对应环境验收。')
    def shutdown(self):
        if self.closed:return
        self.closed=True;self.timer.stop()
        if self.proc:
            self.proc.blockSignals(True)
            if self.after and self.after[0]=='prepare_git':self.proc.write(b'c');self.proc.waitForFinished(2000)
            self.proc.kill();self.proc.waitForFinished(1500);self.proc.deleteLater();self.proc=None

def create_panel(context,parent):return Panel(context,parent)
