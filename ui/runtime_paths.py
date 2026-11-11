"""Strict LXGit runtime location, shared by panel and worker."""
import json
import os
from pathlib import Path

def no_links(path):
    for p in [path, *path.parents]:
        if p.is_symlink() or (p.exists() and getattr(p.lstat(), 'st_file_attributes', 0) & 0x400):
            raise ValueError('运行时目录不能经过符号链接或重解析点。')
    return path

def runtime_root(package):
    p=Path(os.path.abspath(package));no_links(p)
    m=json.loads((p/'.lxtool/manifest.json').read_text('utf-8'))
    if m.get('kind')!='lx-tool' or m.get('panel')!='.lxtool/panel.json':raise ValueError('LXGit 包标识无效。')
    if (p/'tool.json').exists():
        r=json.loads((p/'tool.json').read_text('utf-8'))
        if r.get('id')!='repo-1432888469' or r.get('repository')!='gd-dd/LXGit' or r.get('version')!=p.name or m.get('version')!=p.name or p.parent.name!='versions' or not p.parent.parent.name.endswith('--1432888469'):
            raise ValueError('不能确认 LXGit 安装容器，拒绝向父目录写入。')
        base=p.parent.parent
    elif (p/'backend/worker.py').is_file() and (p/'scripts/prepare_package.py').is_file() and m.get('name')=='LXGit 游戏协作':base=p
    else:raise ValueError('未识别的 LXGit 目录；请通过工具架安装。')
    return no_links(base/'runtime/git')
