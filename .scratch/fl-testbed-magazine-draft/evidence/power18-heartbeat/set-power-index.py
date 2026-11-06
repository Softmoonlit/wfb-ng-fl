import json
import os
import pathlib
import subprocess
import sys

index=int(sys.argv[1]);assert 1<=index<=63
host=__import__('socket').gethostname();assert os.geteuid()==0
interfaces=[]
for p in pathlib.Path('/sys/class/net').iterdir():
    d=p/'device/driver'
    if d.exists() and pathlib.Path(os.path.realpath(d)).name=='rtl88xxau_wfb':interfaces.append(p.name)
assert len(interfaces)==1,interfaces
iface=interfaces[0]
def run(args):
 p=subprocess.run(args,capture_output=True,text=True,timeout=10);p.check_returncode();return p.stdout
before=run(['iw','dev',iface,'info'])
run(['iw','dev',iface,'set','txpower','fixed',str(-index*100)])
after=run(['iw','dev',iface,'info'])
expected=f'txpower -{index}.00 dBm';assert expected in after,after
print(json.dumps({'host':host,'interface':iface,'index':index,'before':before,'after':after},ensure_ascii=False))
