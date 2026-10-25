"""Native host + packaged worker UI integration. All mutations target temporary repositories."""
import json,os,sys,tempfile,time
from pathlib import Path
os.environ.setdefault('QT_QPA_PLATFORM','offscreen')
ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT));sys.path.insert(0,str(ROOT.parent/'LXTools/src'))
from PySide6.QtWidgets import QApplication,QWidget,QListWidget
from PySide6.QtCore import Qt,QTimer
from native_panels import NativePanelHandle
from backend.worker import Git

def main():
 app=QApplication([]);manifest=json.loads((ROOT/'.lxtool/manifest.json').read_text('utf-8'));tool=dict(manifest,id='repo-0',root=ROOT,panelData={'type':'native','apiVersion':1,'entry':'ui/panel.py'})
 with tempfile.TemporaryDirectory() as temp:
  base=Path(temp);repo=base/'中文界面验收';repo.mkdir();g=Git(str(repo));g.run('init','-b','main');g.run('config','user.name','UI Test');g.run('config','user.email','ui@example.invalid');(repo/'说明.txt').write_text('原来的内容\n','utf-8');g.run('add','.');g.run('commit','-m','初始提交');(repo/'说明.txt').write_text('新的内容\n','utf-8')
  parent=QWidget();parent.resize(1300,850);handle=NativePanelHandle(tool,base/'config',parent);p=handle.widget;p.resize(1300,850);parent.show();errors=[];p.warn=errors.append
  def wait():
   end=time.monotonic()+25
   while time.monotonic()<end:
    app.processEvents();time.sleep(.02)
    if p.proc is None:
     for _ in range(5):app.processEvents();time.sleep(.02)
     if p.proc is None:break
   assert p.proc is None,'QProcess timeout'
   assert not errors,errors
  p.add_repo(str(repo));wait();assert p.files.count()==1
  p.files.setCurrentRow(0);wait();assert '+新的内容' in p.diff.toPlainText()
  p.stage_all();wait();assert p.state['files'][0]['status']=='M '
  p.message.setText('界面提交');p.commit_button.click();wait();assert not p.state['files'];assert g.run('log','-1','--format=%s')=='界面提交'
  p.tabs.setCurrentIndex(1);wait();assert p.history.count()==2;p.history.setCurrentRow(0);wait();assert '界面提交' in p.commit_diff.toPlainText()
  (ROOT/'runtime').mkdir(exist_ok=True);parent.grab().save(str(ROOT/'runtime/ui-history.png'))
  p.tabs.setCurrentIndex(0)
  (repo/'说明.txt').write_text('替换内容\n尾行\n','utf-8');p.refresh();wait();p.files.setCurrentRow(0);wait()
  def select_partial():
   dialog=app.activeModalWidget()
   if dialog:
    rows=dialog.findChild(QListWidget)
    if rows:
     for index in range(rows.count()):
      item=rows.item(index)
      if item.flags()&Qt.ItemIsUserCheckable:item.setCheckState(Qt.Checked if item.text()=='+尾行' else Qt.Unchecked)
     dialog.accept();return
   QTimer.singleShot(50,select_partial)
  QTimer.singleShot(50,select_partial);p.partial_stage();wait()
  assert g.run('show',':说明.txt',raw=True).decode('utf-8')=='新的内容\n尾行\n'
  (repo/'新文件.txt').write_text('创建内容','utf-8');p.refresh();wait();parent.grab().save(str(ROOT/'runtime/ui-changes.png'))
  handle.shutdown();handle.shutdown();assert p.closed;assert not p.timer.isActive()
  second=NativePanelHandle(tool,base/'config',parent);p=second.widget;p.warn=errors.append;wait();assert repo.resolve()==Path(p.repo).resolve();second.shutdown();parent.close()
 print('PASS: native host loader, frozen worker IPC, Chinese filenames, stage/commit/history, settings persistence, repeated shutdown')
if __name__=='__main__':main()
