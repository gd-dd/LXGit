import sys
import os
import tempfile
import unittest
from unittest.mock import patch
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from backend.worker import Git,GitError,dispatch,clean_error

class GitIntegration(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.root=Path(self.temp.name);self.repo=self.root/'中文 项目';self.repo.mkdir();self.g=Git(str(self.repo));self.g.run('init','-b','main');self.g.run('config','user.name','测试作者');self.g.run('config','user.email','test@example.invalid')
        self.write('初始.txt','base\n');self.g.run('add','.');self.g.run('commit','-m','初始提交')
    def tearDown(self):self.temp.cleanup()
    def write(self,name,content):
        p=self.repo/name;p.parent.mkdir(parents=True,exist_ok=True);p.write_text(content,'utf-8')
    def call(self,op,**args):return dispatch({'op':op,'repo':str(self.repo),'args':args})
    def test_status_diff_stage_commit_and_history(self):
        self.write('中文 空格.txt','第一行\n');state=self.call('snapshot');self.assertEqual(state['files'][0]['path'],'中文 空格.txt');self.assertEqual(state['files'][0]['status'],'??')
        self.assertIn('第一行',self.call('diff',path='中文 空格.txt',untracked=True));self.call('stage',paths=['中文 空格.txt']);self.call('commit',message='中文提交');self.assertEqual(self.call('snapshot')['files'],[])
        history=self.call('history');self.assertEqual(history[0]['subject'],'中文提交');self.assertIn('中文 空格.txt',self.call('show',ref=history[0]['sha']))
    def test_literal_path_and_injection_guards(self):
        self.write('[abc].txt','literal');self.write('a.txt','other');self.call('stage',paths=['[abc].txt']);self.assertEqual(self.g.run('diff','--cached','--name-only'),'[abc].txt')
        for path in ('../outside','.git/config',str(self.root/'outside')):
            with self.assertRaises(GitError):self.call('stage',paths=[path])
        with self.assertRaises(GitError):self.call('merge',ref='--upload-pack=bad')
        with self.assertRaises(GitError):self.call('remote',url='https://secret@github.com/u/repo')
        self.assertNotIn('secret',clean_error('https://secret@github.com/u'))
    def test_stage_unstage_and_discard(self):
        self.write('初始.txt','changed\n');self.call('stage',paths=['初始.txt']);self.call('unstage',paths=['初始.txt']);self.assertEqual(self.g.run('diff','--cached'),'');self.call('discard',path='初始.txt');self.assertEqual((self.repo/'初始.txt').read_text('utf-8'),'base\n')
        self.write('新文件.txt','keep')
        with self.assertRaises(GitError):self.call('discard',path='新文件.txt')
        self.assertTrue((self.repo/'新文件.txt').exists())
    def test_unborn_repo_and_unstage(self):
        empty=self.root/'empty';empty.mkdir();dispatch({'op':'init','args':{'path':str(empty)}});(empty/'new').write_text('new');g=Git(str(empty));g.run('add','new');dispatch({'op':'unstage','repo':str(empty),'args':{'paths':['new']}});self.assertTrue((empty/'new').exists());self.assertEqual(g.snapshot()['branch'],'main')
    def test_branch_merge_rename_tag(self):
        self.call('branch_create',name='功能');self.write('feature','feature');self.call('stage',paths=['feature']);self.call('commit',message='功能');self.call('switch',branch='main');self.call('merge',ref='功能');self.call('branch_delete',name='功能');self.call('branch_rename',name='主线');self.call('tag_create',name='v0.0.1');self.assertIn('v0.0.1',self.call('snapshot')['tags']);self.call('tag_delete',name='v0.0.1')
    def make_conflict(self):
        self.call('branch_create',name='feature');self.write('初始.txt','feature\n');self.call('stage',paths=['初始.txt']);self.call('commit',message='功能');self.call('switch',branch='main');self.write('初始.txt','main\n');self.call('stage',paths=['初始.txt']);self.call('commit',message='主线')
        with self.assertRaises(GitError):self.call('merge',ref='feature')
    def test_conflict_resolution_continue(self):
        self.make_conflict();s=self.call('snapshot');self.assertEqual(s['operations'],['merge']);self.assertTrue(s['files'][0]['conflict']);self.call('resolve',path='初始.txt',side='theirs');self.call('continue');self.assertEqual(self.call('snapshot')['operations'],[]);self.assertEqual((self.repo/'初始.txt').read_text('utf-8'),'feature\n')
    def test_conflict_abort(self):
        self.make_conflict();self.call('abort');self.assertEqual((self.repo/'初始.txt').read_text('utf-8'),'main\n');self.assertFalse(self.call('snapshot')['operations'])
    def test_stash_preserves_untracked(self):
        self.write('未跟踪.txt','untracked');self.write('初始.txt','dirty');self.call('stash_save',message='保存');self.assertFalse((self.repo/'未跟踪.txt').exists());self.call('stash_apply',ref='stash@{0}');self.assertEqual((self.repo/'未跟踪.txt').read_text(),'untracked');self.call('stash_drop',ref='stash@{0}');self.assertEqual(self.call('snapshot')['stashes'],[])
    def test_revert_cherry_pick_undo_reset(self):
        self.call('branch_create',name='feature');self.write('feature','f');self.call('stage',paths=['feature']);self.call('commit',message='new');sha=self.g.ref('HEAD');self.call('switch',branch='main');self.call('cherry-pick',ref=sha);self.assertTrue((self.repo/'feature').exists());self.call('revert',ref='HEAD');self.assertFalse((self.repo/'feature').exists());self.call('undo');self.assertTrue(self.g.run('diff','--cached'));self.g.run('commit','-m','revert again');self.call('reset',ref='HEAD^');self.assertTrue(self.call('snapshot')['files'])
    def test_rebase_and_squash(self):
        self.call('branch_create',name='feature');self.write('feature','f');self.call('stage',paths=['feature']);self.call('commit',message='feature');self.call('switch',branch='main');self.write('main','m');self.call('stage',paths=['main']);self.call('commit',message='main');self.call('switch',branch='feature');self.call('rebase',ref='main');self.call('switch',branch='main');self.call('squash_merge',ref='feature');self.assertIn('feature',self.g.run('diff','--cached','--name-only'))
    def test_sync_with_real_bare_remote(self):
        bare=self.root/'remote.git';Git().run('init','--bare',str(bare));self.call('remote',url=str(bare));self.call('push');self.assertEqual(self.call('snapshot')['upstream'],'origin/main')
        clone=self.root/'clone';dispatch({'op':'clone','args':{'url':str(bare),'path':str(clone)}});other=Git(str(clone));other.run('switch','main');other.run('config','user.name','other');other.run('config','user.email','other@example.invalid');(clone/'remote.txt').write_text('remote');other.run('add','.');other.run('commit','-m','remote');other.run('push');self.call('fetch');self.assertEqual(self.call('snapshot')['behind'],1);self.call('pull');self.assertTrue((self.repo/'remote.txt').exists())
    def test_history_squash_and_reorder(self):
        for name in ('one','two','three'):
            self.write(name,name);self.call('stage',paths=[name]);self.call('commit',message=name)
        rows=self.call('history');self.call('rewrite_history',mode='reorder',count=3,order=[rows[1]['sha'],rows[2]['sha'],rows[0]['sha']])
        self.assertEqual([x['subject'] for x in self.call('history')][:3],['three','one','two'])
        self.call('rewrite_history',mode='squash',count=3);self.assertEqual(len(self.call('history')),2)
        self.assertTrue(all((self.repo/n).exists() for n in ('one','two','three')))
        self.assertTrue(any(b.startswith('lx-backup-') for b in self.call('snapshot')['branches']))
    def test_nested_untracked_files_and_rename(self):
        self.write('folder/中文.txt','new');self.assertIn('folder/中文.txt',[f['path'] for f in self.call('snapshot')['files']])
        self.g.run('mv','初始.txt','改名.txt');entry=next(f for f in self.call('snapshot')['files'] if f['path']=='改名.txt');self.assertEqual(entry['old'],'初始.txt')

    def test_compare_file_history_and_signing_settings(self):
        self.call('branch_create',name='feature');self.write('feature','f');self.call('stage',paths=['feature']);self.call('commit',message='feature')
        self.assertIn('feature',self.call('compare',ref='main'));self.assertIn('feature',self.call('file_history',path='feature'))
        self.call('signing',enabled=True,key='TEST-ONLY');self.assertEqual(self.g.run('config','commit.gpgsign'),'true');self.call('signing',enabled=False);self.assertEqual(self.g.run('config','commit.gpgsign'),'false')
    def test_github_api_routes_without_remote_mutation(self):
        self.call('remote',url='https://github.com/example/repository.git')
        with patch('backend.worker.github',return_value={'ok':True}) as api:
            self.call('github_create',name='new',owner='organization',private=True)
            self.assertEqual(api.call_args.args[1],'/orgs/organization/repos');self.assertTrue(api.call_args.args[3]['private'])
            self.call('github_pr_create',title='PR',base='main',draft=True)
            self.assertEqual(api.call_args.args[1],'/repos/example/repository/pulls');self.assertTrue(api.call_args.args[3]['draft'])
            self.call('github_fork');self.assertEqual(api.call_args.args[1],'/repos/example/repository/forks')
        with self.assertRaises(GitError):dispatch({'op':'github_user','host':'github.com/evil'})
    def test_enterprise_remote_matching(self):
        self.call('remote',url='https://git.example.invalid/team/project.git')
        result=dispatch({'op':'github_url','host':'git.example.invalid','repo':str(self.repo)})
        self.assertEqual(result,'https://git.example.invalid/team/project')
        with self.assertRaises(GitError):self.call('github_url')

    def test_lfs_local_initialization_and_tracking(self):
        self.call('lfs_install');self.call('lfs_track',pattern='*.fbx')
        self.assertIn('*.fbx filter=lfs',(self.repo/'.gitattributes').read_text())
        self.assertTrue((self.repo/'.git/hooks/pre-push').exists())
    def test_submodule_initialization(self):
        module=self.root/'module';module.mkdir();other=Git(str(module));other.run('init','-b','main');other.run('config','user.name','Module');other.run('config','user.email','module@example.invalid');(module/'content').write_text('submodule');other.run('add','.');other.run('commit','-m','module')
        self.g.run('-c','protocol.file.allow=always','submodule','add',str(module),'vendor/module');self.g.run('commit','-m','add module');self.g.run('submodule','deinit','--force','--all')
        with patch.dict(os.environ,{'GIT_ALLOW_PROTOCOL':'file'}):self.call('submodules')
        self.assertEqual((self.repo/'vendor/module/content').read_text(),'submodule')

    def test_partial_patch(self):
        self.write('初始.txt','base\naddition\n');patch=self.call('diff',path='初始.txt');self.call('apply_patch',patch=patch);self.assertIn('+addition',self.g.run('diff','--cached'))
    def test_partial_line_selection_and_trailing_spaces(self):
        self.write('初始.txt','base\nold\n');self.call('stage',paths=['初始.txt']);self.call('commit',message='baseline')
        self.write('初始.txt','base\nnew\ntrailing  \n');patch=self.call('diff',path='初始.txt');self.assertTrue(patch.endswith('trailing  \n'))
        lines=[]
        for line in patch.splitlines(keepends=True):
            if line=='-old\n':lines.append(' old\n')
            elif line=='+new\n':continue
            else:lines.append(line)
        self.call('apply_patch',patch=''.join(lines));self.assertEqual(self.g.run('show',':初始.txt',raw=True).decode(),'base\nold\ntrailing  \n')

    def test_dirty_merge_protected(self):
        self.write('初始.txt','dirty')
        with self.assertRaisesRegex(GitError,'储藏'):self.call('rebase',ref='main')
    def test_ignore_and_identity(self):
        self.call('ignore',pattern='*.tmp');self.write('ignored.tmp','ignored');self.assertNotIn('ignored.tmp',[f['path'] for f in self.call('snapshot')['files']]);self.call('identity',name='新作者',email='new@example.invalid');self.assertEqual(self.g.run('config','user.name'),'新作者')

if __name__=='__main__':unittest.main()
