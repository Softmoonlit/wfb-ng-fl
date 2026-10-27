import pathlib,subprocess,time,json,datetime,shlex,os,signal,urllib.request
BASE=pathlib.Path(__file__).resolve().parent;CAP=BASE/'capture-tun.py';cap=CAP.read_text().replace('duration=110.0','duration=80.0')
(BASE/'capture-phase.py').write_text(cap)
# phase name -> clients that must remain active; server always remains active.
phases=[('all3',['fl01','fl02','fl04']),('only-fl02',['fl02']),('fl01-fl02',['fl01','fl02']),('restore-all',['fl01','fl02','fl04'])]
ips={'fl01':'201','fl02':'202','fl04':'204'}
def cmd(node,argv,log):
 ip=ips.get(node)
 full=argv if ip is None else ['ssh','-o','BatchMode=yes','-o','ConnectTimeout=7',f'fl@192.168.108.{ip}',*argv]
 r=subprocess.run(full,stdout=log,stderr=subprocess.STDOUT,timeout=30);return r.returncode
def status():
 try:
  op=urllib.request.build_opener(urllib.request.ProxyHandler({}));
  with op.open('http://127.0.0.1:9090/api/v1/status',timeout=5) as f:return json.load(f)
 except Exception as e:return {'error':repr(e)}
def startcap(nodes,phase):
 procs=[]
 for n in nodes:
  cmd=['sudo','-n','env','PYTHONDONTWRITEBYTECODE=1','timeout','90','python3','-B','-'] if n=='fl00' else ['ssh','-o','BatchMode=yes','-o','ConnectTimeout=7',f'fl@{("192.168.108."+ips[n])}','sudo -n env PYTHONDONTWRITEBYTECODE=1 timeout 90 python3 -B -']
  log=(BASE/f'{phase}-{n}-capture.jsonl').open('wb');p=subprocess.Popen(cmd,stdin=subprocess.PIPE,stdout=log,stderr=subprocess.STDOUT,cwd='/tmp',start_new_session=True);p.stdin.write(cap.encode());p.stdin.close();procs.append((p,log,n))
 return procs
records=[];allnodes=['fl00','fl01','fl02','fl04'];active=set(['fl01','fl02','fl04'])
try:
 for phase,want in phases:
  want=set(want)
  # apply only client daemon stop/start; always record before/after API.
  for n in ['fl01','fl02','fl04']:
   if n in want and n not in active:
    with (BASE/f'{phase}-{n}-start.txt').open('wb') as f: r=cmd(n,['sudo','-n','systemctl','start','wfb-fl-client-daemon.service'],f); f.write(f'\nrc {r}\n'.encode());
   if n not in want and n in active:
    with (BASE/f'{phase}-{n}-stop.txt').open('wb') as f: r=cmd(n,['sudo','-n','systemctl','stop','wfb-fl-client-daemon.service'],f); f.write(f'\nrc {r}\n'.encode());
  active=want
  before=status();(BASE/f'{phase}-before.json').write_text(json.dumps(before,ensure_ascii=False,indent=2)+'\n')
  procs=startcap(['fl00']+sorted(want),phase)
  t0=time.time()
  while time.time()-t0<84:
   if all(p.poll() is not None for p,_,_ in procs):break
   time.sleep(2)
  for p,log,n in procs:
   try:p.wait(timeout=10)
   except subprocess.TimeoutExpired:p.terminate();p.wait(timeout=5)
   log.close()
  after=status();(BASE/f'{phase}-after.json').write_text(json.dumps(after,ensure_ascii=False,indent=2)+'\n')
  records.append({'phase':phase,'wanted':sorted(want),'before':before,'after':after,'utc':datetime.datetime.now(datetime.timezone.utc).isoformat()})
 # explicit restore all and capture status only; restore phase already captured all.
finally:
 for n in ['fl01','fl02','fl04']:
  with (BASE/f'finally-{n}-start.txt').open('wb') as f:
   r=cmd(n,['sudo','-n','systemctl','start','wfb-fl-client-daemon.service'],f);f.write(f'\nrc {r}\n'.encode())
 final=status();(BASE/'final-restored-status.json').write_text(json.dumps(final,ensure_ascii=False,indent=2)+'\n')
(BASE/'phase-summary.json').write_text(json.dumps(records,ensure_ascii=False,indent=2)+'\n')
print('DIFF_EXPERIMENT_COMPLETE')
