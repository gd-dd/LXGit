import json
import hashlib
from pathlib import Path
from PySide6.QtGui import QImage
root=Path(__file__).resolve().parents[1]
source=Path('E:/Ruanjian/LXTool/纳西妲抱github desktop icon.png')
if not (root/'.lxtool/icon.png').exists():
    image=QImage(str(source));assert not image.isNull()
    assert image.scaled(512,512).save(str(root/'.lxtool/icon.png'))
(root/'.lxtool/panel.json').write_text(json.dumps({'type':'native','apiVersion':1,'entry':'ui/panel.py'}),'utf-8')
files=['.lxtool/icon.png','.lxtool/README.md','.lxtool/panel.json','ui/panel.py','ui/runtime_paths.py','backend/git_runtime.py','bin/LXGit-worker.exe']
m={'schemaVersion':2,'kind':'lx-tool','name':'LXGit 游戏协作','description':'给中文游戏开发团队的版本管理工具：下载团队项目、查看修改、保存版本、同步给队友。','version':'0.0.7','category':'开发工具','icon':'.lxtool/icon.png','readme':'.lxtool/README.md','panel':'.lxtool/panel.json','applications':{},'actions':{},'files':{f:hashlib.sha256((root/f).read_bytes()).hexdigest() for f in files},'releaseNotes':'重新整理日常操作：项目、分支、同步与更多分组收纳，扩大文件和差异区，改善小窗口与高缩放显示。'}
(root/'.lxtool/manifest.json').write_text(json.dumps(m,ensure_ascii=False,indent=2)+'\n','utf-8')
