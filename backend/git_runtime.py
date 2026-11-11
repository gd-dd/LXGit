"""Pinned, private PortableGit bootstrap. Only this worker writes runtime data."""
import hashlib
import json
import os
from pathlib import Path
import shutil
import stat
import errno
import re
import subprocess
import time
import urllib.request
import urllib.error
import http.client
import urllib.parse
import uuid
from contextlib import contextmanager

SPEC = dict(version='2.56.1.windows.1', size=60125512,
    sha256='9dd04a608ceb5f6ab32a73709e0ecb159d211da71b697c45ff81f6dd0c6b49c7',
    url='https://github.com/git-for-windows/git/releases/download/v2.56.1.windows.1/PortableGit-2.56.1-64-bit.7z.exe')
FLAGS = 0x08000000 if os.name == 'nt' else 0

class Cancelled(Exception): pass

def check_cancel(cancel):
    if cancel.is_set(): raise Cancelled('已取消 Git 准备，可稍后重试。')

from ui.runtime_paths import no_links, runtime_root

def digest(path):
    h=hashlib.sha256()
    with Path(path).open('rb') as f:
        for b in iter(lambda:f.read(1024*1024),b''):h.update(b)
    return h.hexdigest()

def git_env(root):
    e=dict(os.environ)
    e['PATH']=os.pathsep.join(str(root/p) for p in ['cmd','ucrt64/bin','mingw64/bin','usr/bin'])+os.pathsep+e.get('PATH','')
    return e

def probe(root):
    git=root/'cmd/git.exe'
    commands=[[str(git),'--version'],[str(git),'lfs','version'],[str(git),'credential-manager','--version'],[str(root/'usr/bin/ssh.exe'),'-V']]
    versions=[]
    for args in commands:
        r=subprocess.run(args,capture_output=True,timeout=30,env=git_env(root),creationflags=FLAGS)
        if r.returncode:raise ValueError('Git 组件验证失败：'+Path(args[0]).name)
        versions.append((r.stdout+r.stderr).decode('utf-8','replace').strip())
    if versions[0]!='git version '+SPEC['version']:raise ValueError('Git 版本验证失败。')
    return versions

def ready(root,verify=True):
    target=root/SPEC['version'];no_links(target)
    try:
        marker=no_links(target/'lxgit-ready.json')
        if marker.stat().st_size>16384:return None
        m=json.loads(marker.read_text('utf-8'))
        if not isinstance(m,dict) or not isinstance(m.get('files'),dict):return None
        if m.get('archiveSha256')!=SPEC['sha256'] or m.get('version')!=SPEC['version']:return None
        required=['cmd/git.exe','ucrt64/bin/git-lfs.exe','ucrt64/bin/git-credential-manager.exe','usr/bin/ssh.exe']
        if set(m['files'])!=set(required):return None
        if any(not isinstance(h,str) or not re.fullmatch('[a-f0-9]{64}',h) for h in m['files'].values()):return None
        for rel in required:
            p=no_links(target/rel)
            if digest(p)!=m['files'][rel]:return None
        if verify:probe(target)
        return str(target/'cmd/git.exe')
    except (OSError,ValueError,subprocess.SubprocessError):return None

@contextmanager
def locked(root):
    import msvcrt
    no_links(root);root.mkdir(parents=True,exist_ok=True)
    p=no_links(root/'prepare.lock')
    f=p.open('a+b');f.seek(0);f.write(b'0');f.flush();f.seek(0)
    try:msvcrt.locking(f.fileno(),msvcrt.LK_NBLCK,1)
    except OSError:
        f.close();raise ValueError('另一个 LXGit 正在准备 Git，请稍后重试。')
    try:yield
    finally:
        f.seek(0);msvcrt.locking(f.fileno(),msvcrt.LK_UNLCK,1);f.close()

def remove_staging(root,path):
    no_links(path)
    if path.parent!=root or not path.name.startswith('.stage-'):raise ValueError('临时目录越界。')
    if path.exists():
        for p in path.rglob('*'):no_links(p)
        shutil.rmtree(path)

class SecureRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self,req,fp,code,msg,headers,newurl):
        validate_url(newurl)
        return super().redirect_request(req,fp,code,msg,headers,newurl)

def validate_url(url):
    u=urllib.parse.urlsplit(url)
    if u.scheme!='https' or u.hostname not in ('github.com','release-assets.githubusercontent.com','objects.githubusercontent.com') or u.username or u.password:
        raise ValueError('Git 下载跳转来源不受信任。')

def _download(path,cancel,progress):
    validate_url(SPEC['url']);op=urllib.request.build_opener(SecureRedirect())
    h=hashlib.sha256();total=0
    with op.open(urllib.request.Request(SPEC['url'],headers={'User-Agent':'LXGit-PrivateGit'}),timeout=15) as response,path.open('wb') as f:
        validate_url(response.url)
        while True:
            check_cancel(cancel);b=response.read(256*1024)
            if not b:break
            total+=len(b)
            if total>SPEC['size']:raise ValueError('Git 下载大小超过预期。')
            h.update(b);f.write(b);progress('下载 Git',total,SPEC['size'])
    if total!=SPEC['size'] or h.hexdigest()!=SPEC['sha256']:raise ValueError('Git 下载不完整或 SHA-256 校验失败，请重试。')

def download(path,cancel,progress):
    try:_download(path,cancel,progress)
    except (OSError, http.client.HTTPException) as error:
        check_cancel(cancel)
        if isinstance(error,OSError) and error.errno in (errno.ENOSPC,errno.EACCES,errno.EPERM):
            raise ValueError('无法写入 Git 临时文件，请检查工具目录权限和磁盘空间后重试。') from error
        raise ValueError('Git 下载连接中断或无法连接服务器，请检查网络后点击“准备 / 重试”。已下载的临时文件不会作为可用 Git。') from error

def signature(path):
    # Literal path is supplied via environment, never interpolated into PowerShell code.
    ps=Path(os.environ.get('SystemRoot','C:/Windows'))/'System32/WindowsPowerShell/v1.0/powershell.exe'
    e=dict(os.environ,LXGIT_VERIFY_FILE=str(path),PSModulePath=str(ps.parent/'Modules'))
    code="$s=Get-AuthenticodeSignature -LiteralPath $env:LXGIT_VERIFY_FILE; if ($s.Status -ne 'Valid') {exit 2}; $s.SignerCertificate.Subject"
    r=subprocess.run([str(ps),'-NoProfile','-NonInteractive','-Command',code],capture_output=True,timeout=60,env=e,creationflags=FLAGS)
    if r.returncode:raise ValueError('官方 Git 数字签名验证失败，请检查系统时间或证书后重试。')
    return r.stdout.decode('utf-8','replace').strip()

def prepare(package,cancel,progress=lambda *a:None):
    root=runtime_root(package)
    with locked(root):
        check_cancel(cancel);progress('检查工具私有 Git',0,0)
        current=ready(root);check_cancel(cancel)
        if current:return {'git':current,'version':SPEC['version'],'reused':True}
        # An interrupted worker can leave only owned staging directories; clean under the lock.
        for old in root.glob('.stage-*'):remove_staging(root,old)
        staging=root/('.stage-'+uuid.uuid4().hex);archive=staging/'PortableGit.7z.exe';payload=staging/'PortableGit'
        if len(str(payload))+86>=250:raise ValueError('工具目录路径过长，无法可靠解压 Git；请将工具架放到较短路径，或手动选择已有 Git。')
        staging.mkdir()
        try:
            download(archive,cancel,progress);check_cancel(cancel)
            progress('校验官方数字签名',0,0);signer=signature(archive);check_cancel(cancel)
            progress('解压 Git（请稍候）',0,0)
            env=dict(os.environ,TEMP=str(staging),TMP=str(staging))
            # Only the pinned signed SFX is executed; its observed default output is staging/PortableGit.
            p=subprocess.Popen([str(archive),'-y','-gm2'],cwd=staging,env=env,stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL,creationflags=FLAGS)
            deadline=time.monotonic()+180
            try:
                while p.poll() is None:
                    check_cancel(cancel)
                    if time.monotonic()>deadline:raise ValueError('Git 解压超时。')
                    time.sleep(.1)
                if p.returncode:raise ValueError('Git 解压失败。')
            finally:
                if p.poll() is None:
                    taskkill=Path(os.environ.get('SystemRoot','C:/Windows'))/'System32/taskkill.exe'
                    subprocess.run([str(taskkill),'/PID',str(p.pid),'/T','/F'],capture_output=True,timeout=15,creationflags=FLAGS)
                    p.wait(timeout=15)
            check_cancel(cancel)
            for f in payload.rglob('*'):no_links(f)
            progress('验证 Git / LFS / 登录组件 / SSH',0,0);versions=probe(payload)
            required=['cmd/git.exe','ucrt64/bin/git-lfs.exe','ucrt64/bin/git-credential-manager.exe','usr/bin/ssh.exe']
            marker=dict(version=SPEC['version'],archiveSha256=SPEC['sha256'],signer=signer,probes=versions,files={f:digest(payload/f) for f in required})
            (payload/'lxgit-ready.json').write_text(json.dumps(marker,ensure_ascii=False,indent=2),'utf-8')
            check_cancel(cancel);target=root/SPEC['version']
            if target.exists():target.rename(root/('invalid-'+SPEC['version']+'-'+uuid.uuid4().hex))
            payload.rename(target)
            progress('Git 已就绪',1,1)
            return {'git':str(target/'cmd/git.exe'),'version':SPEC['version'],'reused':False}
        finally:remove_staging(root,staging)
