import json,os,subprocess,sys,tempfile,time,ctypes
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
from backend.worker import Git

def alive(pid):
 k=ctypes.WinDLL('kernel32');k.OpenProcess.restype=ctypes.c_void_p;k.OpenProcess.argtypes=[ctypes.c_ulong,ctypes.c_int,ctypes.c_ulong];k.WaitForSingleObject.argtypes=[ctypes.c_void_p,ctypes.c_ulong];k.CloseHandle.argtypes=[ctypes.c_void_p]
 handle=k.OpenProcess(0x100000,False,pid)
 if not handle:return False
 running=k.WaitForSingleObject(handle,0)==258;k.CloseHandle(handle);return running
with tempfile.TemporaryDirectory() as temp:
 root=Path(temp);repo=root/'repo';repo.mkdir();g=Git(str(repo));g.run('init','-b','main');g.run('config','user.name','Test');g.run('config','user.email','test@example.invalid');(repo/'file').write_text('data');g.run('add','.')
 child=root/'sleeper.py';pidfile=root/'pid';child.write_text('import os,time\nfrom pathlib import Path\nPath('+repr(str(pidfile))+').write_text(str(os.getpid()))\ntime.sleep(60)\n')
 hook=repo/'.git/hooks/pre-commit';hook.write_text('#!/bin/sh\n"'+Path(sys.executable).as_posix()+'" "'+child.as_posix()+'"\n')
 p=subprocess.Popen([str(ROOT/'bin/LXGit-worker.exe')],stdin=subprocess.PIPE,stdout=subprocess.PIPE,stderr=subprocess.PIPE)
 p.stdin.write(json.dumps({'op':'commit','repo':str(repo),'args':{'message':'cancel test'}}).encode());p.stdin.close()
 deadline=time.monotonic()+15
 while not pidfile.exists() and time.monotonic()<deadline:time.sleep(.1)
 assert pidfile.exists(),'hook did not start';pid=int(pidfile.read_text());assert alive(pid)
 p.kill();p.wait(timeout=5)
 deadline=time.monotonic()+8
 while alive(pid) and time.monotonic()<deadline:time.sleep(.1)
 assert not alive(pid),'orphaned Git hook process'
 print('PASS: killing packaged worker launcher also stops owned Git and hook descendants')
