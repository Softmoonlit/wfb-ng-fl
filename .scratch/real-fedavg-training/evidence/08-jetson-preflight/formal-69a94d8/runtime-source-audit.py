import os,sys,json,pathlib,subprocess,importlib.util,hashlib,time
host=os.uname().nodename;role='server' if host=='fl00' else 'client';unit='wfb-fl-'+role+'-daemon.service'
def cmd(a):
 try:r=subprocess.run(a,capture_output=True,text=True,errors='backslashreplace',timeout=10);return {'args':a,'rc':r.returncode,'stdout':r.stdout,'stderr':r.stderr}
 except Exception as e:return {'args':a,'error':repr(e)}
def read(p):
 try:return pathlib.Path(p).read_text(errors='backslashreplace')
 except Exception as e:return 'READ_ERROR '+repr(e)
out={'host':host,'utc':time.strftime('%Y-%m-%dT%H:%M:%SZ',time.gmtime()),'unit':unit}
out['systemd_show']=cmd(['systemctl','show',unit,'-p','MainPID','-p','ExecStart','-p','WorkingDirectory','-p','Environment','-p','FragmentPath','-p','DropInPaths'])
pid=out['systemd_show']['stdout'].split('MainPID=',1)[-1].split('\n',1)[0].strip();out['pid']=pid
if pid.isdigit():
 out['proc_cwd']=cmd(['readlink','-e','/proc/'+pid+'/cwd']);out['proc_exe']=cmd(['readlink','-e','/proc/'+pid+'/exe']);out['proc_cmdline']=read('/proc/'+pid+'/cmdline').replace('\x00',' ');out['proc_environ']=read('/proc/'+pid+'/environ').replace('\x00','\n');out['proc_status']=cmd(['grep','-E','^(Name|Pid|PPid|Uid|Gid):','/proc/'+pid+'/status'])
out['shell_cwd']=os.getcwd();out['shell_sys_path']=sys.path
mods=['wfb_ng','wfb_ng.fl.service','wfb_ng.fl.mnist','wfb_ng.fl.mnist_data','wfb_ng.fl.server_daemon','wfb_ng.fl.client_daemon','wfb_ng.fl.console']
out['find_spec']={}
for m in mods:
 try:
  spec=importlib.util.find_spec(m);out['find_spec'][m]=None if spec is None else {'origin':spec.origin,'search_locations':None if spec.submodule_search_locations is None else list(spec.submodule_search_locations)}
 except Exception as e:out['find_spec'][m]={'error':repr(e)}
# Import using the same clean /tmp context, with no current repo path or PYTHONPATH.
out['clean_import']={}
for m in mods[1:]:
 try:
  spec=importlib.util.find_spec(m);out['clean_import'][m]=None if spec is None else {'origin':spec.origin,'sha256':hashlib.sha256(pathlib.Path(spec.origin).read_bytes()).hexdigest() if spec.origin and pathlib.Path(spec.origin).is_file() else None}
 except Exception as e:out['clean_import'][m]={'error':repr(e)}
print(json.dumps(out,ensure_ascii=False,indent=2))
