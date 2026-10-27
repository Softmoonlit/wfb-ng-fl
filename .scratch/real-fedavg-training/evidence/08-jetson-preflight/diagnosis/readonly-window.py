import subprocess,pathlib,os,json,time
host=os.uname().nodename;role='server' if host=='fl00' else 'client'
for args in [['timedatectl','show','-p','NTPSynchronized','-p','Timezone','-p','TimeUSec'],['journalctl','-k','--since','@1793071770','--until','@1793071900','--no-pager','-o','short-unix'],['journalctl','-u','wfb-fl-'+role+'-daemon.service','--since','@1793071770','--until','@1793071900','--no-pager','-o','short-unix']]:
 print('命令',args,flush=True);r=subprocess.run(args,capture_output=True,text=True,timeout=10);print(r.stdout+r.stderr,'退出码',r.returncode,flush=True)
f=pathlib.Path('/srv/wfb-ng-fl')/role/'wfb_uplink.log'
if f.exists():
 print('当前链路日志尾部',f,flush=True);r=subprocess.run(['tail','-n','55',str(f)],capture_output=True,text=True,timeout=10);print(r.stdout,flush=True)
for f in pathlib.Path('/proc/self').glob('nothing'):pass
