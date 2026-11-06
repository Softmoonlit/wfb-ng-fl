import concurrent.futures,datetime,json,pathlib,subprocess
root=pathlib.Path(__file__).resolve().parent/('jtop-audit-'+datetime.datetime.now(datetime.timezone.utc).strftime('%Y%m%dT%H%M%SZ'));root.mkdir()
probe=r'''
import json,pathlib,socket,subprocess
r={'host':socket.gethostname(),'boot_id':pathlib.Path('/proc/sys/kernel/random/boot_id').read_text().strip(),'uptime':pathlib.Path('/proc/uptime').read_text(),'config':str(pathlib.Path('/etc/nvpmodel.conf').resolve())}
commands={'jtop_enabled':['systemctl','is-enabled','jtop.service'],'jtop_active':['systemctl','is-active','jtop.service'],'power_service':['systemctl','show','nvpmodel.service','-p','Result','-p','ExecMainStatus'],'power':['sudo','-n','/usr/sbin/nvpmodel','-q','--verbose'],'boot_log':['sudo','-n','journalctl','-b','-u','nvpmodel.service','-u','jtop.service','--no-pager','-n','30']}
for k,args in commands.items():
 p=subprocess.run(args,capture_output=True,text=True,timeout=10);r[k]={'exit':p.returncode,'stdout':p.stdout,'stderr':p.stderr}
p=subprocess.run(['sudo','-n','python3','-c',"import pathlib,json; p=pathlib.Path('/usr/local/jtop/config.json'); print(json.dumps(json.loads(p.read_text()).get('jetson_clocks',{})) if p.exists() else 'missing')"],capture_output=True,text=True,timeout=10)
r['jtop_clocks_config']={'exit':p.returncode,'stdout':p.stdout,'stderr':p.stderr}
p=subprocess.run(['sudo','-n','python3','-c',"import pathlib,json; p=pathlib.Path('/sys/kernel/debug/bpmp/debug/clk/emc'); print(json.dumps({f.name:f.read_text().strip() for f in p.iterdir() if f.name in ('rate','mrq_rate_locked','max_rate','min_rate')}))"],capture_output=True,text=True,timeout=10)
r['emc_debug']={'exit':p.returncode,'stdout':p.stdout,'stderr':p.stderr}
print(json.dumps(r))
'''
def check(host):
 cmd=['python3','-'] if host=='fl00' else ['ssh','-o','BatchMode=yes','-o','ConnectTimeout=6',host,'python3','-']
 try:
  p=subprocess.run(cmd,input=probe,capture_output=True,text=True,timeout=45);(root/(host+'.json')).write_text(p.stdout);(root/(host+'.err')).write_text(p.stderr)
  d=json.loads(p.stdout)
  print(json.dumps({'host':host,'uptime_s':d['uptime'].split()[0],'jtop_enabled':d['jtop_enabled']['stdout'].strip(),'jtop_active':d['jtop_active']['stdout'].strip(),'jtop_clocks':d['jtop_clocks_config']['stdout'].strip(),'power_service':d['power_service']['stdout'].strip(),'limits':[s for s in d['power']['stdout'].splitlines() if 'MAX_FREQ' in s and any(t in s for t in ['CPU_A78_0:','PARAM GPU:','PARAM EMC:'])],'emc_debug':d['emc_debug']['stdout'].strip()},ensure_ascii=False),flush=True)
 except Exception as e: print(host,repr(e))
with concurrent.futures.ThreadPoolExecutor(max_workers=10) as ex:list(ex.map(check,['fl00','fl01','fl02','fl04','fl05','fl06','fl07','fl08','fl09','fl10']))
print('EVIDENCE',root)
