import pathlib,sys,json,concurrent.futures
E=pathlib.Path(__file__).parent;sys.path.insert(0,str(E));from run import execute
code='''import pathlib,subprocess,json,hashlib
r={}
for n,a in {'power':['nvpmodel','-q','--verbose'],'power_service':['systemctl','show','nvpmodel.service','-p','Result','-p','ExecMainStatus'],'jtop':['systemctl','show','jtop.service','-p','ActiveState','-p','UnitFileState'],'iw':['iw','dev'],'tun':['ip','-j','addr'],'uftp_version':['uftp','-V'],'uftpd_version':['uftpd','-V']}.items():
 p=subprocess.run(a,text=True,capture_output=True,timeout=15);r[n]={'exit':p.returncode,'stdout':p.stdout,'stderr':p.stderr}
r['boot']=pathlib.Path('/proc/sys/kernel/random/boot_id').read_text();r['uptime']=pathlib.Path('/proc/uptime').read_text();r['profile']=str(pathlib.Path('/etc/nvpmodel.conf').resolve())
r['limits']={str(p):p.read_text().strip() for s in ['/sys/devices/system/cpu/cpufreq/policy*/scaling_max_freq','/sys/devices/platform/17000000.gpu/devfreq_dev/max_freq','/sys/kernel/nvpmodel_clk_cap/emc','/sys/kernel/debug/bpmp/debug/clk/emc/mrq_rate_locked'] for p in pathlib.Path('/').glob(s[1:])}
r['binaries']={n:hashlib.sha256(pathlib.Path('/usr/bin',n).read_bytes()).hexdigest() for n in ['uftp','uftpd','wfb_v6_uplink']}
print(json.dumps(r))
'''
if __name__=='__main__':
 with concurrent.futures.ThreadPoolExecutor() as pool:
  for h,r in zip(['fl03','fl04'],pool.map(lambda h:execute(h,'final-power',code),['fl03','fl04'])):
   print(h,r['exit'],r['stdout'],r['stderr'])
