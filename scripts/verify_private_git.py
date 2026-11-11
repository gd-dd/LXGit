"""Offline real-private-Git regression. Caller supplies an isolated E profile/TEMP."""
import json,os,sys,time,unittest,subprocess
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT))
from backend import worker as w,git_runtime as m
start=time.monotonic();git=m.ready(ROOT/'runtime/git');assert git,'Prepare the real private runtime first'
report={'git':git,'readySeconds':time.monotonic()-start}
os.environ['PATH']='';original=w.find_git;w.find_git=lambda explicit='':original(explicit or git)
suite=unittest.defaultTestLoader.discover(str(ROOT/'tests'),pattern='test_git.py')
with (ROOT/'runtime/private-git-qa/private-regression.log').open('w',encoding='utf-8') as f:result=unittest.TextTestRunner(stream=f,verbosity=2).run(suite)
report.update(tests=result.testsRun,passed=result.wasSuccessful());assert result.wasSuccessful()
for mode in ['normal-open-stdin','immediate-cancel']:
 p=subprocess.Popen([str(ROOT/'bin/LXGit-worker.exe')],stdin=subprocess.PIPE,stdout=subprocess.PIPE,stderr=subprocess.PIPE)
 req=(json.dumps({'op':'prepare_git','package':str(ROOT)})+'\n').encode()
 p.stdin.write(req+(b'c' if mode=='immediate-cancel' else b''));p.stdin.flush()
 # Deliberately keep stdin open until process exits, regression for buffered-reader shutdown crash.
 p.wait(timeout=40);out=p.stdout.read();err=p.stderr.read();p.stdin.close()
 row=json.loads(out.splitlines()[-1]);assert p.returncode==0 and not err,(p.returncode,err)
 assert row['ok']==(mode=='normal-open-stdin'),row
 report[mode]={'exit':p.returncode,'result':row}
(ROOT/'runtime/private-git-qa/private-regression.json').write_text(json.dumps(report,ensure_ascii=False,indent=2),'utf-8')
print(json.dumps(report,ensure_ascii=True))
