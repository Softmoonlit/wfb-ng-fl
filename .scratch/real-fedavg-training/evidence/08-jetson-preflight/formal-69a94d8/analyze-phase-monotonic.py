import json,pathlib,collections,statistics,re
P=pathlib.Path(__file__).resolve().parent
result={}
def rows(f):return [json.loads(l) for l in f.read_text().splitlines()]
def maxgap(events,key='mono'):return max((b[key]-a[key] for a,b in zip(events,events[1:])),default=None)
def pct(vals,q):return sorted(vals)[min(len(vals)-1,int((len(vals)-1)*q))] if vals else None
for phase in ['all3','only-fl02','fl01-fl02','restore-all']:
 sr=rows(P/(phase+'-fl00-capture.jsonl'));su=[r for r in sr if r['kind']=='udp'];api=[r for r in sr if r['kind']=='api'];phaseout={}
 for n in [1,2,4]:
  f=P/(phase+'-fl%02d-capture.jsonl'%n)
  if not f.exists():continue
  cr=rows(f);cu=[r for r in cr if r['kind']=='udp'];hb=[r for r in cu if r['body'].get('type')=='NODE_HEARTBEAT'];received=[r for r in su if r['body'].get('type')=='NODE_HEARTBEAT' and r['body'].get('node_id')==n];acks=[r for r in cu if r['body']=={'ack':True} and r['dst']=='10.80.0.'+str(n+10)];sacks=[r for r in su if r['body']=={'ack':True} and r['dst']=='10.80.0.'+str(n+10)]
  nearest=[];ambiguous=0
  for i,r in enumerate(hb):
   nextsend=hb[i+1]['mono'] if i+1<len(hb) else cr[-1]['mono'];candidates=[a for a in acks if a['dport']==r['sport'] and r['mono']<=a['mono']<nextsend]
   if candidates:nearest.append(candidates[0]['mono']-r['mono']);ambiguous+=len(candidates)>1
  # ACK response on same server clock; ACK has no identity so nearest is not causal proof.
  response=[]
  for r in received:
   cand=[a['mono']-r['mono'] for a in sacks if a['dport']==r['sport'] and 0<=a['mono']-r['mono']<.2]
   if cand:response.append(min(cand))
  snapshots=[r for r in cr if r['kind']=='snapshot'];tokens=[];grantc=[];seqs=set();capture_start_ms=cr[0]['mono']*1000;token_records={};grant_records={}
  for r in snapshots:
   for line in r['native_tail_selected']:
    if '\tTOKEN_AUTH\t' in line and float(line.split('\t')[0])>=capture_start_ms:token_records[int(line.split('\t')[0])]=tuple(map(int,line.split('\t')[2].split(':')))
    if '\tGRANT_FILTER\t' in line and float(line.split('\t')[0])>=capture_start_ms:grant_records[int(line.split('\t')[0])]=tuple(map(int,line.split('\t')[2].split(':')))
    m=re.search(r'grant_accept node_id=\d+ sequence=(\d+).*expires_at_ms=(\d+)',line)
    if m and int(m.group(2))>=capture_start_ms:seqs.add(int(m.group(1)))
  # dedup repeated native tails by timestamp order; first/last capture near boundaries, recorded as sampled counter deltas.
  tokens=[v for k,v in sorted(token_records.items())];grantc=[v for k,v in sorted(grant_records.items())]
  tokdelta=[b-a for a,b in zip(tokens[0],tokens[-1])] if tokens else None;gdelta=[b-a for a,b in zip(grantc[0],grantc[-1])] if grantc else None
  av=[{'mono':r['mono'],**r['nodes'][str(n)]} for r in api];offline=[r for r in av if r['readiness']=='OFFLINE'];first_update=next((r['mono'] for r in av if r['last_heartbeat_time']>=api[0]['mono']),None)
  steady=[r for r in av if first_update is not None and r['mono']>=first_update]
  intervals=[];openat=None
  for r in av:
   if r['readiness']=='OFFLINE' and openat is None:openat=r['mono']
   elif r['readiness']!='OFFLINE' and openat is not None:intervals.append({'from_mono':openat,'to_mono':r['mono'],'sampled_s':r['mono']-openat});openat=None
  if openat is not None:intervals.append({'from_mono':openat,'to_mono':av[-1]['mono'],'sampled_s':av[-1]['mono']-openat,'right_censored':True})
  phaseout[str(n)]={'duration_client_s':cr[-1]['mono']-cr[0]['mono'],'hb_client_count':len(hb),'hb_server_count':len(received),'max_client_HBsend_gap_s':maxgap(hb),'max_server_HBrx_gap_s':maxgap(received),'max_server_registry_HBage_s':max(r['last_heartbeat_ago_seconds'] for r in av),'server_api_offline_samples':len(offline),'api_samples':len(av),'server_offline_samples_after_first_current_phase_HB':sum(r['readiness']=='OFFLINE' for r in steady),'offline_intervals_server_monotonic':intervals,'hunt_packet_ratio':sum(r['body']['state']=='HUNTING' for r in hb)/len(hb) if hb else None,'client_states':dict(collections.Counter(r['body']['state'] for r in hb)),'channels':dict(collections.Counter(r['body']['current_channel'] for r in hb)),'client_nearest_ACK_after_HB_before_next_send_count':len(nearest),'client_nearest_ACKdelay_ms_median':statistics.median(nearest)*1000 if nearest else None,'client_nearest_ACKdelay_ms_max':max(nearest)*1000 if nearest else None,'client_nearest_ACKdelay_over230ms_count':sum(x>.23 for x in nearest),'multiple_ACK_same_send_window':ambiguous,'server_nearest_ACKresponse_ms_max':max(response)*1000 if response else None,'token_counter_delta':tokdelta,'grant_filter_delta':gdelta,'grant_accept_unique_sequences_in_sampled_tails_lower_bound':len(seqs),'capture_drops':cr[-1]['socket_stats'][1],'notes':'HB gaps exclude pre/post capture boundary. ACK nearest association is ambiguous because ACK has no HB timestamp/seq; it is not exact RTT. Grant counters sampled native tails, not full event ledger. Initial offline after stopped node starts includes stale registry and startup.'}
 result[phase]=phaseout
(P/'phase-monotonic-metrics.json').write_text(json.dumps(result,ensure_ascii=False,indent=2)+'\n')
for ph,ns in result.items():
 print(ph)
 for n,v in ns.items():print(n,'HBsend/rxgap',v['max_client_HBsend_gap_s'],v['max_server_HBrx_gap_s'],'ACKmedian/maxms',v['client_nearest_ACKdelay_ms_median'],v['client_nearest_ACKdelay_ms_max'],'offline',v['server_api_offline_samples'],'steadyoffline',v['server_offline_samples_after_first_current_phase_HB'],'hunt',v['hunt_packet_ratio'],'grant_delta',v['grant_filter_delta'])
