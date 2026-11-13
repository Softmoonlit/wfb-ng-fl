"""本地监督快照：只读阶段证据，单次等候不超过 25 秒。"""
import json,pathlib,time,sys
ROOT=pathlib.Path(__file__).resolve().parent
if len(sys.argv)>1:time.sleep(min(25,float(sys.argv[1])))
def read(name):
    p=ROOT/name
    try:return json.loads(p.read_text())
    except (OSError,ValueError):return {}
m=read('matrix.json');cp=read('checkpoint.json');c=cp.get('current') or {}
nodes=(c.get('client_upload_progress') or {}).get('result',{}).get('nodes',[])
summary=dict(wall=time.time(),status=m.get('status'),checkpoint_age_seconds=time.time()-cp.get('wall',time.time()),
    stage=cp.get('stage'),candidate=[c.get('grant_duration_ms'),c.get('guard_interval_ms'),c.get('mode')],
    phase=c.get('currentphase'),execute_count=sum(x.get('execute_count',0) for x in m.get('candidates',[])),
    completed=[dict(grant=x['grant_duration_ms'],guard=x['guard_interval_ms'],mode=x['mode'],outcome=x.get('outcome'),DL=x.get('DLduration'),UL=x.get('ULduration'),clean=x.get('independent_exact_cleanup_ok'),restore=x.get('independent_restore_ok'),ready=x.get('independent_ready_ok')) for x in m.get('candidates',[]) if x.get('outcome') or x.get('stage')=='skipped'],
    upload_progress=[dict(node=x.get('node'),bytes=x.get('bytes'),committed=x.get('committed')) for x in nodes],
    STOP=read('STOP'),final=read('final-closeout/result.json'),error=m.get('error'))
if c.get('rawpath'):
    events=list(pathlib.Path(c['rawpath']).glob('*/progress.jsonl'))
    if events:
        lines=[json.loads(x) for x in events[0].read_text().splitlines()]
        summary['runner_recent']=[dict(phase=x.get('phase'),host=x.get('host'),ok=x.get('ok'),error=x.get('error')) for x in lines[-4:] if x.get('phase')!='upload_progress']
        starts=[x for x in lines if x.get('event')=='dispatch_start']
        if starts:summary['phase_elapsed_seconds']=time.monotonic()-starts[-1].get('dispatch_start',time.monotonic())
with (ROOT/'supervisor-observations.jsonl').open('a') as out:out.write(json.dumps(summary,ensure_ascii=False)+'\n')
compact=dict(summary)
compact['completed_count']=len(summary['completed'])
compact['last_completed']=summary['completed'][-1] if summary['completed'] else None
compact.pop('completed')
if summary['upload_progress']:
    values=[x['bytes'] for x in summary['upload_progress'] if isinstance(x.get('bytes'),int)]
    compact['upload_progress']=dict(observed=len(summary['upload_progress']),minimum_bytes=min(values) if values else None,maximum_bytes=max(values) if values else None,committed=sum(bool(x.get('committed')) for x in summary['upload_progress']))
print(json.dumps(compact,ensure_ascii=False))
