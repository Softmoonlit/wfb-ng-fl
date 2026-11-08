from pathlib import Path
import json,time,os,signal,hashlib,subprocess
ROOT=Path('/home/fl/projects/wfb-ng-fl/.scratch/fl-testbed-magazine-draft/evidence/grant-fec-nine-20261108/after-power-restored')
DRIVER_PID=116223
request=dict(reason='User prioritizes guard comparison: finish existing FEC14 model30/10 exactly once, independently audit, then hold40/60. New20/5 and30/5 require later explicit command.',candidate_boundary=30,requested_wall=time.time(),no_runner_signal=True)
(ROOT/'model-pause-request.json').write_text(json.dumps(request,indent=2))
p=ROOT/'model-fec14-g30-d10/model-fec14-g30-d10/progress.jsonl'
end=time.monotonic()+1370
while time.monotonic()<end:
    matrix=json.loads((ROOT/'model-matrix-fec14.json').read_text())
    candidate=matrix['candidates'][2]
    if any(c['execute_count'] for c in matrix['candidates'][3:]):
        raise RuntimeError('next candidate already executed; do not signal an active later run')
    if matrix['status']=='stopped':
        raise RuntimeError('original controller already stopped; preserve failure, no automatic action')
    try:
        rows=[json.loads(line) for line in p.read_text().splitlines()]
    except (ValueError,FileNotFoundError):
        time.sleep(.1);continue
    if rows and rows[-1]['phase']=='result':
        os.kill(DRIVER_PID,signal.SIGSTOP)
        break
    time.sleep(.1)
else:
    raise RuntimeError('pause observer deadline; no active runner interrupted')
# Only the local sequencing driver is suspended; the runner already emitted final result.
process=json.loads((ROOT/'model-fec14-g30-d10/runner-process.json').read_text())
runner_pid=process['pid']
for _ in range(50):
    proc=Path('/proc',str(runner_pid))
    state=(proc/'stat').read_text().rsplit(')',1)[1].split()[0] if proc.exists() else 'absent'
    if state in ['absent','Z']:break
    time.sleep(.1)
else:
    raise RuntimeError('runner did not exit after final result; driver remains suspended for review')
(ROOT/'model-driver-boundary-suspension.json').write_text(json.dumps(dict(driver_pid=DRIVER_PID,runner_pid=runner_pid,runner_state=state,raw_final=rows[-1],wall=time.time(),note='No signal sent to runner or residents; local sequencing driver suspended after final result to prevent next schedule'),indent=2))
source=(ROOT/'orchestration-model-fec14.py').read_text()
header=source[:source.index("checkpoint('starting_model_matrix')")]
ns={'__name__':'model_boundary_audit'}
exec(compile(header,str(ROOT/'orchestration-model-fec14.py'),'exec'),ns)
matrix=json.loads((ROOT/'model-matrix-fec14.json').read_text())
ns['MATRIX']=matrix
c=matrix['candidates'][2]
ns['CURRENT']=c
final=rows[-1]
c.update(transfer_verified=final['transfer_verified'],cleanup_ok=final['cleanup_ok'],restore_ok=final['restore_ok'],runner_restore_ok=final['restore_ok'],original_runner_final_flags={key:final[key] for key in ['transfer_verified','cleanup_ok','restore_ok']},progress_path=str(p),elapsed=final['monotonic']-rows[0]['monotonic'],runner_returncode=0 if all(final[key] for key in ['transfer_verified','cleanup_ok','restore_ok']) else 1)
for phase,key in [('publish','DLduration'),('submit','ULduration')]:
    starts=[row['dispatch_start'] for row in rows if row.get('phase')==phase and row.get('event')=='dispatch_start']
    ends=[row['monotonic'] for row in rows if row.get('phase')==phase and 'ok' in row]
    c[key]=max(ends)-starts[0] if starts and ends else None
    c.setdefault('phase_completion_seconds',{})[phase]={row['host']:row['monotonic']-starts[0] for row in rows if starts and row.get('phase')==phase and row.get('ok') is True and 'host' in row}
c['server_verified_files']=next((row['result']['files'] for row in rows if row.get('phase')=='verify' and row.get('host')=='fl00' and row.get('ok') is True),{})
c['failures']=[row for row in rows if row.get('ok') is False]
frozen=json.loads((ROOT/'model-fec14-g30-d10/frozen-bundle.json').read_text())
c['bundle_unchanged']=all(hashlib.sha256((ns['TOOL']/name).read_bytes()).hexdigest()==digest for name,digest in frozen['files'].items())
base=ROOT/'model-fec14-g30-d10'
audit=ns['audit'](base,'after',30,run_id='model-fec14-g30-d10',since=int(c['started_wall']))
c['independent_restore_ok']=audit['ok']
c['outcome']='passed' if c['transfer_verified'] and c['runner_restore_ok'] else 'runner_failed'
c['finished_wall']=time.time()
c['stage']='complete_candidate'
ns['write'](base/'run-summary.json',dict(candidate=c,result=final))
assert all(x['stage']=='pending' and x['execute_count']==0 for x in matrix['candidates'][3:])
# The completed runner has exited and full audit is now on disk. Terminate only suspended sequencer.
os.kill(DRIVER_PID,signal.SIGTERM)
os.kill(DRIVER_PID,signal.SIGCONT)
matrix['status']='paused' if audit['ok'] and c['cleanup_ok'] and c['bundle_unchanged'] else 'stopped'
matrix['pause_reason']=request['reason']
matrix['pause_boundary_grant']=30
matrix['schedule_after_pause']='30/10 retained; no additional server restart or parameter change during handoff'
matrix['pending_grants']=[40,60]
matrix.setdefault('recoveryhistory',[]).append(dict(event='user_requested_safe_pause',wall=time.time(),reason=request['reason'],raw_flags=c['original_runner_final_flags'],independent_restore_ok=audit['ok'],runner_state=state,driver_only_signals=['SIGSTOP after raw final result','SIGTERM/SIGCONT after independent audit'],no_signal_to_runner=True))
ns['checkpoint']('paused_at_candidate_boundary' if matrix['status']=='paused' else 'stopped_at_candidate_boundary')
ns['write'](ROOT/'model-pause-complete.json',dict(status=matrix['status'],candidate_grant=30,raw_final=final,independent_restore_ok=audit['ok'],pending_grants=[40,60],runner_state=state,driver_pid=DRIVER_PID,wall=time.time()))
print(json.dumps(dict(status=matrix['status'],grant=30,flags=c['original_runner_final_flags'],independent=audit['ok'],pending=[40,60])),flush=True)
