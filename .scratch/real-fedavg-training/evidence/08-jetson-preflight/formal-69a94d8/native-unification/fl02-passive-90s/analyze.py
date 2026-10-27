import json,pathlib,re,collections,statistics
p=pathlib.Path(__file__).resolve().parent
S=[json.loads(l) for l in (p/'fl00-capture.jsonl').read_text().splitlines()];C=[json.loads(l) for l in (p/'fl02-capture.jsonl').read_text().splitlines()];sn=json.loads((p/'fl00-native-window.json').read_text());cn=json.loads((p/'fl02-native-window.json').read_text())
sent=[r for r in C if r['kind']=='udp' and r['body'].get('type')=='NODE_HEARTBEAT'];rx=[r for r in S if r['kind']=='udp' and r['body'].get('type')=='NODE_HEARTBEAT' and r['body'].get('node_id')==2];hashes={r['payload_sha256'] for r in rx}
api=[r for r in S if r['kind']=='api'];grants={};accepts={};ready=[];removal=[]
for r in sn['events']:
 m=re.search(r'grant seq=(\d+) node_id=(\d+) duration_ms=(\d+)',r['line'])
 if m and m[2]=='2':grants[int(m[1])]=r
 if 'ready_' in r['line'] and re.search(r'(?:^| )node_id=2(?: |$)',r['line']):ready.append(r)
 if 'silent_remove' in r['line'] and re.search(r'(?:^| )node_id=2(?: |$)',r['line']):removal.append(r)
for r in cn['events']:
 m=re.search(r'grant_accept node_id=2 sequence=(\d+).*expires_at_ms=(\d+)',r['line'])
 if m:accepts[int(m[1])]={**r,'accepted_at_ms_estimate':int(m[2])-120}
sequence=[{'sequence':seq,'server':r,'client':accepts.get(seq)} for seq,r in sorted(grants.items())]
acks=[r for r in C if r['kind']=='udp' and r['body']=={'ack':True} and r['dst']=='10.80.0.12'];delays=[]
for i,r in enumerate(sent):
 nextsend=sent[i+1]['mono'] if i+1<len(sent) else C[-1]['mono'];matches=[a for a in acks if a['dport']==r['sport'] and r['mono']<=a['mono']<nextsend]
 if matches:delays.append(matches[0]['mono']-r['mono'])
# dedup stats and intervals to locate accepted flat/authorized flat periods, not just phase-end status
snap=[]
for r in C:
 if r['kind']!='snapshot':continue
 lines=[x for x in r['native_tail_selected'] if '\tTOKEN_AUTH\t' in x];gf=[x for x in r['native_tail_selected'] if '\tGRANT_FILTER\t' in x]
 snap.append({'mono':r['mono'],'token':list(map(int,lines[-1].split('\t')[2].split(':'))) if lines else None,'grant_filter':list(map(int,gf[-1].split('\t')[2].split(':'))) if gf else None,'radio':r['radio']['stdout']})
burst=[]
for r in rx:
 w=[a for a in rx if r['mono']<=a['mono']<r['mono']+1]
 if len(w)>5 and (not burst or r['mono']-burst[-1]['server_mono']>1):burst.append({'server_mono':r['mono'],'packet_count':len(w),'arrival_span_s':w[-1]['mono']-w[0]['mono'],'source_timestamp_span_s':(w[-1]['body']['timestamp_ms']-w[0]['body']['timestamp_ms'])/1000})
def gap(r):return max((b['mono']-a['mono'] for a,b in zip(r,r[1:])),default=None)
result={'duration_s':S[-1]['mono']-S[0]['mono'],'capture_drop_server':S[-1]['socket_stats'][1],'capture_drop_client':C[-1]['socket_stats'][1],'client_hb':len(sent),'server_hb':len(rx),'matched_hb':sum(r['payload_sha256'] in hashes for r in sent),'client_max_HB_gap_s':gap(sent),'server_max_HB_gap_s':gap(rx),'hunt_ratio':sum(r['body']['state']=='HUNTING' for r in sent)/len(sent),'states':dict(collections.Counter(r['body']['state'] for r in sent)),'channels':dict(collections.Counter(r['body']['current_channel'] for r in sent)),'ACK_nearest_median_ms':statistics.median(delays)*1000 if delays else None,'ACK_nearest_max_ms':max(delays)*1000 if delays else None,'server_offline_samples':sum(r['nodes']['2']['readiness']=='OFFLINE' for r in api),'server_api_samples':len(api),'server_max_HB_age_s':max(r['nodes']['2']['last_heartbeat_ago_seconds'] for r in api),'server_node2_grants':len(grants),'client_node2_accepts':len(accepts),'matched_grantseq':len(set(grants)&set(accepts)),'ready_events':ready,'silent_removals':removal,'bursts':burst,'grant_sequences':sequence,'client_snapshots':snap,'API':[{'mono':r['mono'],'node':r['nodes']['2']} for r in api],'notes':'native event timestamps use prior stats anchor (~1s), grant expiry gives exact client accepted time minus120ms. Server/client clocks have separate offsets. Nearest ACK not causal identity. Seq boundary mismatch not packet-loss proof.'}
(p/'analysis.json').write_text(json.dumps(result,ensure_ascii=False,indent=2)+'\n')
print(json.dumps({k:v for k,v in result.items() if k not in ['grant_sequences','client_snapshots','API']},ensure_ascii=False,indent=2))
