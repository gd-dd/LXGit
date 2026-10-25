"""LXGit isolated worker. One JSON request on stdin, one JSON result on stdout."""
import base64
import json
import os
from pathlib import Path
import re
import shutil
import shlex
import tempfile
import uuid
import subprocess
import sys
import urllib.request
import urllib.error
import urllib.parse

class GitError(Exception):
    pass


def find_git(explicit=''):
    if explicit:
        if Path(explicit).is_file(): return str(Path(explicit).resolve())
        raise GitError('指定的 Git 程序不存在。请在设置中重新选择 git.exe。')
    found=shutil.which('git')
    if found: return found
    candidates=[Path(os.environ.get('ProgramFiles','C:/Program Files'))/'Git/cmd/git.exe',
                Path(os.environ.get('LOCALAPPDATA',''))/'Programs/Git/cmd/git.exe']
    desktop=Path(os.environ.get('LOCALAPPDATA',''))/'GitHubDesktop'
    candidates.extend(sorted(desktop.glob('app-*/resources/app/git/cmd/git.exe'),reverse=True))
    for p in candidates:
        if p.is_file(): return str(p)
    raise GitError('未找到 Git。请安装 Git for Windows，或在设置中选择 git.exe。')


def clean_error(value):
    value=re.sub(r'(https?://)[^/\s@]+@',r'\1[已隐藏凭据]@',str(value))
    value=re.sub(r'(gh[pousr]_[A-Za-z0-9_]+|github_pat_[A-Za-z0-9_]+)','[已隐藏凭据]',value)
    translations=[('Authentication failed','身份验证失败，请重新登录 GitHub'),('Permission denied','访问被拒绝'),
      ('not a git repository','此目录不是 Git 仓库'),('nothing to commit','没有可提交的改动'),
      ('Please tell me who you are','请先设置提交者姓名和邮箱'),('non-fast-forward','远端有新提交，请先拉取'),
      ('Could not resolve host','无法解析服务器地址，请检查网络'),('CONFLICT','合并冲突'),
      ('would be overwritten','操作将覆盖本地改动，请先提交或储藏'),('unable to access','无法访问远程地址')]
    for a,b in translations: value=value.replace(a,b)
    return value[-12000:]

class Git:
    def __init__(self,repo='',executable=''):
        self.exe=find_git(executable)
        self.repo=str(Path(repo).resolve()) if repo else None
    def run(self,*args,input=None,check=True,timeout=300,raw=False,extra_env=None):
        env=dict(os.environ,GIT_TERMINAL_PROMPT='0',GCM_INTERACTIVE='never',GIT_EDITOR='true',GIT_SEQUENCE_EDITOR='true',LC_ALL='C.UTF-8')
        # Literal pathspecs ensure files named *, [, or :(...) are never interpreted as patterns.
        env.pop('GIT_LITERAL_PATHSPECS',None)
        if args and args[0] in ('add','restore','rm','diff','ls-files','checkout'):
            env['GIT_LITERAL_PATHSPECS']='1'
        if extra_env:env.update(extra_env)
        try:
            p=subprocess.run([self.exe,'-c','core.quotepath=false',*map(str,args)],cwd=self.repo,
                input=input.encode('utf-8') if isinstance(input,str) else input,stdout=subprocess.PIPE,stderr=subprocess.PIPE,
                env=env,timeout=timeout,creationflags=0x08000000 if os.name=='nt' else 0)
        except subprocess.TimeoutExpired: raise GitError('操作超时。请检查网络或仓库状态后重试。')
        if check and p.returncode: raise GitError(clean_error(p.stderr.decode('utf-8','replace') or p.stdout.decode('utf-8','replace')))
        return p.stdout if raw else p.stdout.decode('utf-8','replace').strip()
    def ref(self,value):
        if not value or value.startswith('-') or '\x00' in value: raise GitError('分支或提交引用无效。')
        return self.run('rev-parse','--verify',value+'^{commit}')
    def path(self,value):
        p=Path(self.repo)/value
        if not value or Path(value).is_absolute() or '..' in Path(value).parts or '.git' in Path(value).parts:
            raise GitError('文件路径无效。')
        if not p.resolve().is_relative_to(Path(self.repo)): raise GitError('文件位于仓库外。')
        return value
    def clean(self):
        if self.run('status','--porcelain'): raise GitError('请先提交或储藏全部改动，再执行此操作。')
    def branch_name(self,name):
        if name.startswith('-'): raise GitError('分支名不能以减号开头。')
        self.run('check-ref-format','--branch',name)
        return name
    def snapshot(self):
        root=self.run('rev-parse','--show-toplevel')
        raw=self.run('status','--porcelain=v1','--untracked-files=all','-z',raw=True).decode('utf-8','replace').split('\0')
        files=[];i=0
        while i<len(raw):
            line=raw[i];i+=1
            if not line: continue
            status=line[:2];path=line[3:];old=''
            if 'R' in status or 'C' in status:
                old=raw[i];i+=1
            files.append({'status':status,'path':path,'old':old,'conflict':status in ('DD','AU','UD','UA','DU','AA','UU')})
        branch=self.run('symbolic-ref','--short','HEAD',check=False) or '分离头指针'
        upstream=self.run('rev-parse','--abbrev-ref','--symbolic-full-name','@{upstream}',check=False)
        counts=self.run('rev-list','--left-right','--count','HEAD...@{upstream}',check=False).split()
        branches=self.run('for-each-ref','--format=%(refname:short)','refs/heads','refs/remotes').splitlines()
        operations=[]
        for name,path in [('merge','MERGE_HEAD'),('rebase','rebase-merge'),('rebase','rebase-apply'),('cherry-pick','CHERRY_PICK_HEAD'),('revert','REVERT_HEAD')]:
            q=Path(self.run('rev-parse','--git-path',path))
            if not q.is_absolute(): q=Path(self.repo)/q
            if q.exists() and name not in operations:operations.append(name)
        return dict(root=root,branch=branch,files=files,branches=branches,upstream=upstream,
            ahead=int(counts[0]) if counts else 0,behind=int(counts[1]) if counts else 0,
            operations=operations,remotes=self.run('remote','-v'),tags=self.run('tag').splitlines(),
            stashes=self.run('stash','list','--format=%gd%x09%gs').splitlines())


def github(g,endpoint,method='GET',body=None):
    # Read only Git Credential Manager's GitHub entry; never serialize the token.
    host=getattr(g,'host','github.com')
    value=g.run('credential','fill',input='protocol=https\nhost='+host+'\n\n',check=False)
    credential=dict(line.split('=',1) for line in value.splitlines() if '=' in line)
    headers={'User-Agent':'LXGit/0.0.1','Accept':'application/vnd.github+json','X-GitHub-Api-Version':'2022-11-28'}
    if credential.get('password'): headers['Authorization']='Bearer '+credential['password']
    elif endpoint=='/user' or method!='GET': raise GitError('尚未登录。请先通过“GitHub 登录”授权 Git Credential Manager。')
    req=urllib.request.Request(('https://api.github.com' if host=='github.com' else 'https://'+host+'/api/v3')+endpoint,headers=headers,method=method,
        data=json.dumps(body).encode() if body is not None else None)
    try:
        with urllib.request.urlopen(req,timeout=30) as r:return json.load(r)
    except urllib.error.HTTPError as e:
        raise GitError({401:'GitHub 登录已失效，请重新登录。',403:'GitHub 拒绝访问：请检查权限或 API 限额。',404:'GitHub 仓库不存在，或当前账号无权访问。',422:'GitHub 未接受此请求，请检查名称、分支和重复的拉取请求。'}.get(e.code,'GitHub 请求失败：'+str(e.code)))


def remote_repo(g):
    url=g.run('remote','get-url','origin')
    host=re.escape(getattr(g,'host','github.com'))
    m=re.fullmatch(r'(?:https://'+host+r'/|git@'+host+r':)([\w.-]+/[\w.-]+?)(?:\.git)?/?',url)
    if not m:raise GitError('origin 与当前 GitHub 服务地址不匹配。请在远程设置中检查地址。')
    return m[1]


def dispatch(req):
    op=req['op'];a=req.get('args',{});g=Git(req.get('repo',''),req.get('git',''))
    host=req.get('host','github.com').lower().strip()
    if not re.fullmatch(r'[a-z0-9](?:[a-z0-9.-]*[a-z0-9])?',host):raise GitError('GitHub 服务地址应填写域名，不包含协议或路径。')
    g.host=host
    if op=='environment':return {'git':g.exe,'version':g.run('--version'),'lfs':g.run('lfs','version',check=False)}
    if op=='init':
        p=Path(a['path']).resolve();p.mkdir(parents=True,exist_ok=True)
        if (p/'.git').exists():raise GitError('该目录已经是 Git 仓库。')
        return g.run('init','--initial-branch=main',str(p))
    if op=='clone':
        url=a['url'].strip()
        if url.startswith('-') or not url:raise GitError('仓库地址无效。')
        return g.run('clone','--progress','--',url,str(Path(a['path']).resolve()),timeout=1800)
    if op=='login':
        # Interactive browser/device authorization is an explicit user action.
        env=dict(os.environ,GCM_INTERACTIVE='always',GIT_TERMINAL_PROMPT='0')
        p=subprocess.run([g.exe,'credential-manager','github','login','--url','https://'+g.host,'--browser'],env=env,capture_output=True,timeout=300,creationflags=0x08000000 if os.name=='nt' else 0)
        if p.returncode:raise GitError('GitHub 登录失败。请安装含 Git Credential Manager 的 Git for Windows。\n'+clean_error(p.stderr.decode('utf-8','replace')))
        return 'GitHub 授权完成'
    if op=='github_user':return github(g,'/user')
    if op=='github_repos':
        result=[]
        for page in range(1,21):
            batch=github(g,f'/user/repos?sort=updated&per_page=100&page={page}');result.extend(batch)
            if len(batch)<100:break
        return [{'name':x['full_name'],'url':x['clone_url'],'private':x['private']} for x in result]
    if op=='github_create':
        name=a['name'].strip()
        if not re.fullmatch(r'[\w.-]+',name):raise GitError('仓库名只能包含字母、数字、下划线、点和连字符。')
        owner=a.get('owner','').strip()
        if owner and not re.fullmatch(r'[\w.-]+',owner):raise GitError('组织名称无效。')
        return github(g,'/orgs/'+owner+'/repos' if owner else '/user/repos','POST',{'name':name,'private':a.get('private',True),'description':a.get('description',''),'auto_init':False})
    if op=='snapshot':return g.snapshot()
    if op=='history':
        if not g.run('rev-parse','--verify','HEAD',check=False):return []
        data=g.run('log','-200','--date=iso-strict','--format=%H%x00%h%x00%an%x00%ad%x00%s%x00%d%x1e')
        return [dict(zip(['sha','short','author','date','subject','refs'],r.strip().split('\0'))) for r in data.split('\x1e') if r.strip()]
    if op=='image_diff':
        path=g.path(a['path']);working=Path(g.repo)/path
        before=g.run('show','HEAD:'+path,check=False,raw=True)
        after=working.read_bytes() if working.is_file() else b''
        if len(before)>20*1024*1024 or len(after)>20*1024*1024:raise GitError('图片超过 20 MB，请使用外部编辑器查看。')
        return {'before':base64.b64encode(before).decode(),'after':base64.b64encode(after).decode()}
    if op=='rewrite_history':
        g.clean();count=int(a['count'])
        if not 2<=count<=50:raise GitError('每次可整理 2 至 50 条提交。')
        base=g.ref('HEAD~'+str(count))
        if g.run('rev-list','--merges',base+'..HEAD'):raise GitError('所选历史包含合并提交，请先选择连续的普通提交。')
        commits=g.run('rev-list','--reverse',base+'..HEAD').splitlines()
        if len(commits)!=count:raise GitError('提交数量不匹配。')
        if a.get('mode')=='reorder':
            order=[g.ref(x.strip()) for x in a['order']]
            if len(order)!=len(commits) or set(order)!=set(commits):raise GitError('必须且只能列出原来的每条提交一次。')
            todo=''.join('pick '+sha+'\n' for sha in order)
        elif a.get('mode')=='squash':
            todo='pick '+commits[0]+'\n'+''.join('squash '+sha+'\n' for sha in commits[1:])
        else:raise GitError('历史整理模式无效。')
        backup='lx-backup-'+uuid.uuid4().hex[:10];g.run('branch',backup,'HEAD')
        with tempfile.TemporaryDirectory(prefix='lxgit-rebase-') as directory:
            plan=Path(directory)/'todo';plan.write_text(todo,'utf-8')
            command=[Path(sys.executable).as_posix()]
            if not getattr(sys,'frozen',False):command.append(Path(__file__).as_posix())
            command.extend(['--sequence-editor',plan.as_posix()])
            editor=' '.join(shlex.quote(x) for x in command)
            output=g.run('rebase','-i',base,extra_env={'GIT_SEQUENCE_EDITOR':editor})
        return output+'\n已创建恢复分支：'+backup
    if op=='show':return g.run('show','--format=fuller','--stat','--patch','--no-ext-diff','--no-textconv',g.ref(a['ref']))[:2000000]
    if op=='diff':
        path=g.path(a['path']);flags=['--cached'] if a.get('staged') else []
        if a.get('untracked'):
            p=Path(g.repo)/path
            if not p.is_file():return '该项目不是普通文件。'
            with p.open('rb') as stream:data=stream.read(2000000)
            if b'\0' in data:return '二进制文件：请使用“打开文件”预览。'
            return data.decode('utf-8','replace')
        return g.run('diff',*flags,'--no-ext-diff','--no-textconv','--',path,raw=True).decode('utf-8','replace')[:2000000]
    if op in ('stage','unstage'):
        paths=[g.path(p) for p in a['paths']]
        if not paths:raise GitError('请先选择文件。')
        if op=='stage':return g.run('add','--',*paths)
        if g.run('rev-parse','--verify','HEAD',check=False):return g.run('restore','--staged','--',*paths)
        return g.run('rm','--cached','--',*paths)
    if op=='apply_patch':return g.run('apply','--cached','--recount','--whitespace=nowarn','-',input=a['patch'])
    if op=='commit':
        if not a['message'].strip():raise GitError('请填写提交说明。')
        return g.run('commit',*(['--amend'] if a.get('amend') else []),'-m',a['message'])
    if op=='identity':
        if not a['name'].strip() or '@' not in a['email']:raise GitError('请填写姓名和有效邮箱。')
        g.run('config','--local','user.name',a['name']);return g.run('config','--local','user.email',a['email'])
    if op=='switch':
        name=g.branch_name(a['branch'])
        if name.startswith('origin/'):
            local=name[len('origin/'):]
            return g.run('switch',local) if local in g.run('for-each-ref','--format=%(refname:short)','refs/heads').splitlines() else g.run('switch','--track',name)
        return g.run('switch',name)
    if op=='branch_create':return g.run('switch','-c',g.branch_name(a['name']),g.ref(a.get('start','HEAD')))
    if op=='branch_delete':return g.run('branch','-d',g.branch_name(a['name']))
    if op=='branch_rename':return g.run('branch','-m',g.branch_name(a['name']))
    if op in ('merge','rebase','cherry-pick','revert'):
        g.clean();ref=g.ref(a['ref'])
        return g.run(op,*(['--no-edit'] if op in ('merge','revert') else []),ref)
    if op=='squash_merge':
        g.clean();return g.run('merge','--squash',g.ref(a['ref']))
    if op in ('continue','abort'):
        state=g.snapshot()['operations']
        if not state:raise GitError('没有进行中的合并、变基或拣选。')
        name=state[0]
        if name=='merge' and op=='continue':return g.run('commit','--no-edit')
        return g.run(name,'--'+op)
    if op=='undo':
        g.clean();g.ref('HEAD^');return g.run('reset','--soft','HEAD^')
    if op=='reset':
        g.clean();return g.run('reset','--mixed',g.ref(a['ref']))
    if op=='resolve':
        path=g.path(a['path']);side=a['side']
        if side not in ('ours','theirs'):raise GitError('无效冲突版本。')
        g.run('checkout','--'+side,'--',path);return g.run('add','--',path)
    if op=='discard':
        path=g.path(a['path'])
        # Tracked files only. Untracked deletion is never implicit.
        g.run('ls-files','--error-unmatch','--',path)
        return g.run('restore','--worktree','--',path)
    if op=='stash_save':return g.run('stash','push','--include-untracked','-m',a.get('message','LX 版本控制储藏'))
    if op in ('stash_apply','stash_pop','stash_drop'):
        ref=a['ref']
        if not re.fullmatch(r'stash@\{\d+\}',ref):raise GitError('储藏引用无效。')
        return g.run('stash',op.split('_')[1],ref)
    if op=='tag_create':
        name=g.branch_name(a['name']);return g.run('tag','-a',name,'-m',a.get('message') or name,g.ref(a.get('ref','HEAD')))
    if op=='tag_delete':return g.run('tag','-d',g.branch_name(a['name']))
    if op=='fetch':return g.run('fetch','--all','--prune',timeout=600)
    if op=='pull':return g.run('pull',*(['--rebase'] if a.get('rebase') else ['--ff-only']),timeout=600)
    if op=='push':
        branch=g.run('symbolic-ref','--short','HEAD')
        flags=['--force-with-lease'] if a.get('force') else []
        return g.run('push',*flags,'--set-upstream','origin',branch,timeout=600)
    if op=='push_tags':return g.run('push','origin','--tags',timeout=600)
    if op=='remote':
        name=a.get('name','origin');url=a['url'].strip()
        if not re.fullmatch(r'[\w.-]+',name) or name.startswith('-') or url.startswith('-') or not url:raise GitError('远程名称或地址无效。')
        if re.search(r'https?://[^/]+@',url):raise GitError('不要把令牌写入远程地址。请使用凭据管理器登录。')
        return g.run('remote','set-url' if name in g.run('remote').splitlines() else 'add',name,url)
    if op=='ignore':
        pattern=a['pattern'].strip()
        if not pattern or '\n' in pattern:raise GitError('请输入一行忽略规则。')
        with (Path(g.repo)/'.gitignore').open('a',encoding='utf-8') as f:f.write('\n'+pattern+'\n')
        return '已添加忽略规则，提交 .gitignore 后对团队生效。'
    if op=='lfs_install':return g.run('lfs','install','--local')
    if op=='lfs_track':return g.run('lfs','track','--',a['pattern'])
    if op=='lfs_pull':return g.run('lfs','pull',timeout=1800)
    if op=='submodules':return g.run('submodule','update','--init','--recursive',timeout=1800)
    if op=='github_fork':return github(g,'/repos/'+remote_repo(g)+'/forks','POST',{})
    if op=='github_pr_details':
        repo=remote_repo(g);pr=github(g,'/repos/'+repo+'/pulls/'+str(int(a['number'])));sha=pr['head']['sha']
        checks=github(g,'/repos/'+repo+'/commits/'+sha+'/check-runs')
        status=github(g,'/repos/'+repo+'/commits/'+sha+'/status')
        return {'title':pr['title'],'url':pr['html_url'],'body':pr.get('body') or '', 'mergeable':pr.get('mergeable'),'draft':pr.get('draft'), 'checks':[{'name':c['name'],'status':c['status'],'conclusion':c.get('conclusion')} for c in checks.get('check_runs',[])], 'status':status.get('state')}
    if op=='github_prs':return github(g,'/repos/'+remote_repo(g)+'/pulls?state=open&per_page=100')
    if op=='github_pr_create':return github(g,'/repos/'+remote_repo(g)+'/pulls','POST',{'title':a['title'],'body':a.get('body',''),'head':g.run('symbolic-ref','--short','HEAD'),'base':a['base'],'draft':a.get('draft',False)})
    if op=='github_pr_checkout':
        number=int(a['number']);g.clean();name='pr-'+str(number)
        g.run('fetch','origin',f'pull/{number}/head');return g.run('switch','-c',name,'FETCH_HEAD')
    if op=='github_url':return 'https://'+g.host+'/'+remote_repo(g)
    if op=='compare':
        base=g.ref(a['ref']);return g.run('log','--oneline',base+'..HEAD')+'\n\n'+g.run('diff','--stat','--patch','--no-ext-diff','--no-textconv',base+'...HEAD')[:2000000]
    if op=='file_history':return g.run('log','-100','--follow','--format=%h %ad %an %s','--date=short','--',g.path(a['path']))
    if op=='signing':
        enabled=bool(a.get('enabled'));key=a.get('key','').strip()
        if enabled and not key:raise GitError('请填写 GPG 签名密钥 ID。')
        if enabled:g.run('config','--local','user.signingkey',key)
        return g.run('config','--local','commit.gpgsign','true' if enabled else 'false')
    if op=='reflog':return g.run('reflog','-100','--date=local')
    raise GitError('不支持的操作：'+op)


def own_process_tree():
    """Windows closes this job on worker exit, stopping owned Git descendants."""
    if os.name!='nt':return None
    import ctypes
    from ctypes import wintypes as w
    class Basic(ctypes.Structure):
        _fields_=[('process_time',ctypes.c_int64),('job_time',ctypes.c_int64),('flags',w.DWORD),('min_ws',ctypes.c_size_t),('max_ws',ctypes.c_size_t),('active',w.DWORD),('affinity',ctypes.c_size_t),('priority',w.DWORD),('scheduling',w.DWORD)]
    class IO(ctypes.Structure):
        _fields_=[(n,ctypes.c_uint64) for n in ('read_ops','write_ops','other_ops','read_bytes','write_bytes','other_bytes')]
    class Extended(ctypes.Structure):
        _fields_=[('basic',Basic),('io',IO),('process_memory',ctypes.c_size_t),('job_memory',ctypes.c_size_t),('peak_process',ctypes.c_size_t),('peak_job',ctypes.c_size_t)]
    k=ctypes.WinDLL('kernel32',use_last_error=True)
    k.CreateJobObjectW.argtypes=[ctypes.c_void_p,w.LPCWSTR];k.CreateJobObjectW.restype=w.HANDLE
    k.SetInformationJobObject.argtypes=[w.HANDLE,ctypes.c_int,ctypes.c_void_p,w.DWORD];k.SetInformationJobObject.restype=w.BOOL
    k.GetCurrentProcess.restype=w.HANDLE
    k.AssignProcessToJobObject.argtypes=[w.HANDLE,w.HANDLE];k.AssignProcessToJobObject.restype=w.BOOL
    handle=k.CreateJobObjectW(None,None);limits=Extended();limits.basic.flags=0x2000
    if not handle or not k.SetInformationJobObject(handle,9,ctypes.byref(limits),ctypes.sizeof(limits)) or not k.AssignProcessToJobObject(handle,k.GetCurrentProcess()):
        raise GitError('无法建立后台进程生命周期，请重新打开工具架后重试。')
    # A one-file bootloader is the QProcess child; monitor it so terminating
    # the launcher cannot leave the extracted worker and Git running.
    import threading
    k.OpenProcess.argtypes=[w.DWORD,w.BOOL,w.DWORD];k.OpenProcess.restype=w.HANDLE
    k.WaitForSingleObject.argtypes=[w.HANDLE,w.DWORD];k.WaitForSingleObject.restype=w.DWORD
    k.CloseHandle.argtypes=[w.HANDLE]
    parent=k.OpenProcess(0x00100000,False,os.getppid())
    if parent:
        def watch_parent():
            k.WaitForSingleObject(parent,0xFFFFFFFF);k.CloseHandle(parent);os._exit(90)
        threading.Thread(target=watch_parent,daemon=True).start()
    return handle


def main():
    if len(sys.argv)>=4 and sys.argv[1]=='--sequence-editor':
        shutil.copyfile(sys.argv[2],sys.argv[3]);return
    try:
        job=own_process_tree()
        request=json.loads(sys.stdin.buffer.read().decode('utf-8'))
        result={'ok':True,'data':dispatch(request)}
    except Exception as e: result={'ok':False,'error':clean_error(e)}
    sys.stdout.buffer.write(json.dumps(result,ensure_ascii=False).encode('utf-8'));sys.stdout.buffer.flush()

if __name__=='__main__':main()
