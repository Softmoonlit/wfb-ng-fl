#!/usr/bin/env python3
"""按显式指定的新schema批次独立汇总；历史schema不在本工具输入范围，归档批次清单见archive-summary-inputs.json。"""
import argparse
from collections import defaultdict
import hashlib
import json
from pathlib import Path
import statistics

parser=argparse.ArgumentParser(description=__doc__)
parser.add_argument('runs',type=Path,nargs='+')
parser.add_argument('--output',type=Path,required=True)
args=parser.parse_args()
rows=[]
groups=defaultdict(list)
for run in args.runs:
 for result in sorted(run.glob('*-mcs*/result.json')):
  r=json.loads(result.read_text())
  if 'phase' not in r or 'target_nodes' not in r:
   raise ValueError('不接受历史schema，请使用archive-summary-inputs.json中的批次：'+str(result))
  if r['phase']!='finished':continue
  r['evidence']=str(result)
  r['result_sha256']=hashlib.sha256(result.read_bytes()).hexdigest()
  targets=tuple(r['target_nodes'])
  if not targets or len(set(targets))!=len(targets):raise ValueError('无效目标集合：'+str(result))
  clients={f'fl{n:02d}' for n in targets}
  # 复核文件交付与实际证据，不能仅采信runner success字段。
  delivered=(r.get('sender_rc')==0 and r.get('group_complete') is True
             and set(r.get('received',{}))==clients
             and all(v.get('bytes')==r['mib']*1048576 and v.get('sha256')==r['source']['sha256']
                     and v.get('receiver_stopped') is True for v in r['received'].values()))
  accepted=delivered and all(r.get(k) is True for k in ['pid_tun_unchanged','endpoint_status_matches','cleanup_complete','tx_mcs_stable'])
  accepted=accepted and set(r.get('rx_ant_targets',{}))==clients and all(r['rx_ant_targets'].values())
  r['independently_accepted']=accepted
  if r['success']!=accepted:
   raise ValueError('runner与独立口径不同，请逐项复核：'+str(result))
  rows.append(r)
  if r.get('sender_rc') is not None:groups[(targets,r['mcs'],r['rate_kbps'],r['mib'])].append(r)
summary=[]
for (targets,mcs,rate,mib),attempts in sorted(groups.items()):
 good=[r for r in attempts if r['independently_accepted']]
 durations=[r['sender_wall_seconds'] for r in good]
 summary.append({'target_nodes':list(targets),'mcs':mcs,'rate_kbps':rate,'mib':mib,'sessions':len(attempts),'successful':len(good),
   'repeatable':len(good)>=3 and len(good)==len(attempts),
   'sender_seconds':{'min':min(durations),'median':statistics.median(durations),'max':max(durations)} if durations else None,
   'evidence':[r['evidence'] for r in attempts]})
obj={'attempts':rows,'candidates':summary,'admission_or_setup_failures':sum(r.get('sender_rc') is None for r in rows),
     'note':'8MiB固定点不计40MiB矩阵；仅同条件3/3完整成功点可形成经验包络，失败记录不删除'}
args.output.write_text(json.dumps(obj,ensure_ascii=False,indent=2)+'\n')
print(json.dumps({'candidates':summary,'admission_or_setup_failures':obj['admission_or_setup_failures']},ensure_ascii=False,indent=2))
