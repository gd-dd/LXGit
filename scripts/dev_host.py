"""Development entry using the real LXTools Window and native loader; no install receipt."""
import argparse
import json
import os
import sys
from pathlib import Path
ROOT=Path(sys.executable).parent if getattr(sys,'frozen',False) else Path(__file__).resolve().parents[1]
HOST=ROOT.parent/'LXTools'
if not getattr(sys,'frozen',False):sys.path.insert(0,str(HOST/'src'))
from PySide6.QtWidgets import QApplication,QPushButton
from PySide6.QtCore import QTimer
from desktop import Window
from core import ToolStore
from theme import STYLE
from lx_protocol import validate_manifest

def main():
    parser=argparse.ArgumentParser();parser.add_argument('--package',default=str(ROOT));parser.add_argument('--config');parser.add_argument('--smoke',action='store_true');parser.add_argument('--screenshot');args=parser.parse_args()
    package=Path(args.package).resolve();config=Path(args.config) if args.config else package/'runtime/development-profile'
    app=QApplication(sys.argv);app.setStyle('Fusion');app.setStyleSheet(STYLE)
    manifest=validate_manifest(json.loads((package/'.lxtool/manifest.json').read_text('utf-8')))
    tool=dict(manifest,id='repo-0',repository='local/development-only',root=package,panelData=json.loads((package/'.lxtool/panel.json').read_text('utf-8')))
    window=Window(ToolStore(config),config/'no-installed-tools',autoload=False);window.resize(1500,950);window.setWindowTitle('LXTools · LX 版本控制开发验收（未发布）')
    def open_panel():
        window.workspace.display(tool);window.panel_title.setText('LX 版本控制 · 0.0.1 开发验收');window.pages.setCurrentWidget(window.tool_panel)
    button=QPushButton('打开版本控制开发包',window);window.status.parentWidget().layout().addWidget(button);button.clicked.connect(open_panel)
    open_panel();window.show()
    if args.smoke:
        def finish():
            handle=getattr(window.workspace,'native_panel',None)
            if not handle or not handle.widget:
                app.exit(2);return
            if args.screenshot:window.grab().save(args.screenshot)
            window.navigate('store');open_panel();window.close();app.quit()
        panel=window.workspace.native_panel.widget
        panel.warn=lambda message:app.exit(3)
        QTimer.singleShot(15000,lambda:app.exit(4))
        panel.run('environment',callback=lambda result:QTimer.singleShot(300,finish),refresh=False)
    return app.exec()
if __name__=='__main__':sys.exit(main())
