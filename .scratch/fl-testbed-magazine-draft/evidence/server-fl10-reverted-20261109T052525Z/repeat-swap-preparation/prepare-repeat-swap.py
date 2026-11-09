import pathlib,json,importlib.util,concurrent.futures,urllib.request,ast,time
BASE=pathlib.Path(__file__).parent;ROOT=BASE.parent;ACTIVE=ROOT/'audit-corrected-continuation'
def save(n,x):(BASE/n).write_text(json.dumps(x,indent=2)+'\n')
final=json.loads((ACTIVE/'final-closeout/result.json').read_text());assert final['exact_cleanup_ok'] and final['server_schedule']=='10/10',final
matrix=json.loads((ACTIVE/'matrix.json').read_text());assert matrix['status'] in ['complete','stopped']
with urllib.request.urlopen('http://127.0.0.1:9090/api/v1/status',timeout=4) as f:status=json.load(f)
online=sorted(int(k) for k,v in status['nodes'].items() if v.get('readiness')!='OFFLINE')
assert status['active_job'] is None and status['server_state']=='IDLE'
# Latest explicit parent instruction permits physical swap preparation with restoration incomplete.
assert json.loads((ACTIVE/'after-fl10-standard-recovery/result.json').read_text())['exact_cleanup_ok']
suspects=[]
for p in pathlib.Path('/proc').glob('[0-9]*/cmdline'):
 try:a=p.read_bytes().decode().split('\0')
 except (OSError,UnicodeError):continue
 if len(a)>1 and any(pathlib.Path(x).name=='runner.py' or pathlib.Path(x).name.startswith('orchestration-') for x in a[1:3]):suspects.append(dict(pid=p.parent.name,argv=a))
assert not suspects,suspects
save('preflight.json',dict(status=status,prior_independent_final=final,local_runner_candidates=suspects,wall=time.time()))
spec=importlib.util.spec_from_file_location('prep',ROOT.parent/'server-fl10-revert-preparation-20261109T051909Z/prepare.py');m=importlib.util.module_from_spec(spec);spec.loader.exec_module(m)
with concurrent.futures.ThreadPoolExecutor(max_workers=3) as e:rows=list(e.map(lambda h:m.invoke(h,m.PROBE),['fl00','fl10']))
save('before-nic-identities.json',rows)
expected={'fl00':'fc:22:1c:10:01:19','fl10':'fc:a3:86:b3:86:72','fl05':'fc:22:1c:10:01:57'}
assert all(x['returncode']==0 and len(x['result']['interfaces'])==1 and x['result']['interfaces'][0]['mac']==expected[x['host']] and len(x['result']['native'])==1 and 'ActiveState=active' in x['result']['service'] for x in rows)
save('gate.json',dict(ok=True,current_identities=expected,fl04_excluded=True))
tree=ast.parse((ROOT.parent/'server-fl10-revert-preparation-20261109T051909Z/stop.py').read_text());STOP=next(ast.literal_eval(n.value) for n in tree.body if isinstance(n,ast.Assign) and any(isinstance(t,ast.Name) and t.id=='STOP' for t in n.targets))
with concurrent.futures.ThreadPoolExecutor(max_workers=2) as e:stops=list(e.map(lambda h:m.invoke(h,STOP),['fl00','fl10']))
ready=all(x.get('returncode')==0 and x.get('result',{}).get('ok') for x in stops)
result=dict(full_ten_restoration_complete=False,fl10_before_stop_actual_channel=157,fl10_before_stop_registry=status['nodes']['10'],latest_authorization='Parent explicitly authorized standard00/10 stop with incomplete restoration; no further recovery',ready_for_user_physical_swap=ready,stops=stops,physical_swap_completed=False,current_identities=expected,expected_after_swap={'fl00':'fc:a3:86:b3:86:72','fl10':'fc:22:1c:10:01:19','fl05':'fc:22:1c:10:01:57'},other_clients_including_05_unchanged=True,management_wifi_changed=False,interface_bindings_changed=False,power_parameters_changed=False,new_trial_started=False,wall=time.time())
save('standard-stop-results.json',result);print(json.dumps(result));assert ready
