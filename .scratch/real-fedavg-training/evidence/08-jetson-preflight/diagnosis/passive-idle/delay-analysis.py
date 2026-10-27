import pathlib,json,collections,statistics
p=pathlib.Path(__file__).resolve().parent
R={n:[json.loads(l) for l in (p/(n+'-capture.jsonl')).read_text().splitlines()] for n in ['fl00','fl01','fl02','fl04']}
server=[r for r in R['fl00'] if r['kind']=='udp'];result={}
for n,num in [('fl01',1),('fl02',2),('fl04',4)]:
 own='10.80.0.'+str(10+num);client=[r for r in R[n] if r['kind']=='udp']
 sends={r['payload_sha256']:r for r in client if r['body'].get('type')=='NODE_HEARTBEAT'}
 arrivals=[r for r in server if r['body'].get('type')=='NODE_HEARTBEAT' and r['body'].get('node_id')==num]
 cacks={(r['ip_id'],r['sport'],r['dport']):r for r in client if r['body']=={'ack':True} and r['dst']==own}
 sacks=[r for r in server if r['body']=={'ack':True} and r['dst']==own]
 uplinks=[(r,sends[r['payload_sha256']]) for r in arrivals if r['payload_sha256'] in sends]
 downlinks=[(r,cacks[(r['ip_id'],r['sport'],r['dport'])]) for r in sacks if (r['ip_id'],r['sport'],r['dport']) in cacks]
 # O=server_monotonic-client_monotonic. Nonnegative delays bound O independently of wall time.
 upper=min(s['mono']-c['mono'] for s,c in uplinks)
 lower=max(s['mono']-c['mono'] for s,c in downlinks)
 raw_lower,raw_upper=lower,upper
 # Userspace receive timestamps have scheduling/drift error; retain raw inconsistency.
 # The following envelope adds 20ms margin at both ends and is an estimate, not a kernel-timestamp proof.
 lower-=.02;upper+=.02
 assert lower<=upper,(n,lower,upper)
 observations=[]
 for s,c in uplinks:
  diff=s['mono']-c['mono'];observations.append({'timestamp_ms':c['body']['timestamp_ms'],'state':c['body']['state'],'channel':c['body']['current_channel'],'client_send_mono':c['mono'],'server_receive_mono':s['mono'],'up_delay_lower_s':max(0,diff-upper),'up_delay_upper_s':diff-lower})
 down=[]
 for s,c in downlinks:
  diff=c['mono']-s['mono'];down.append({'ip_id':s['ip_id'],'server_send_mono':s['mono'],'client_receive_mono':c['mono'],'down_delay_lower_s':max(0,diff+lower),'down_delay_upper_s':diff+upper})
 # delay includes all native/radio/OS transit; no claim that capture proves a particular internal queue.
 worst=sorted(observations,key=lambda r:r['up_delay_lower_s'],reverse=True)[:5]
 bursts=[]
 for i,r in enumerate(arrivals):
  window=[a for a in arrivals if r['mono']<=a['mono']<r['mono']+1]
  if len(window)>=10 and (not bursts or r['mono']-bursts[-1]['first_server_receive_mono']>1):
   bursts.append({'first_server_receive_mono':r['mono'],'last_server_receive_mono':window[-1]['mono'],'packets_in_1s':len(window),'source_timestamp_span_s':(window[-1]['body']['timestamp_ms']-window[0]['body']['timestamp_ms'])/1000})
 response_delays=[]
 for r in arrivals:
  candidate=[a for a in sacks if a['dport']==r['sport'] and 0<=a['mono']-r['mono']<.2]
  if candidate:response_delays.append(min(a['mono']-r['mono'] for a in candidate))
 summary={'timing_assumption':'userspace capture plus 20ms per-side margin; not rigorous kernel timestamp bounds','raw_offset_intersection_width_ms':(raw_upper-raw_lower)*1000,'server_minus_client_monotonic_offset_lower_s':lower,'offset_upper_s':upper,'offset_bound_width_ms':(upper-lower)*1000,'matched_upstream_packets':len(observations),'upstream_delay_lower_max_s':max(x['up_delay_lower_s'] for x in observations),'upstream_delay_upper_max_s':max(x['up_delay_upper_s'] for x in observations),'matched_downstream_acks':len(down),'downstream_delay_lower_max_s':max(x['down_delay_lower_s'] for x in down),'downstream_delay_upper_max_s':max(x['down_delay_upper_s'] for x in down),'timestamp_ahead_of_own_send_max_ms':max(r['body']['timestamp_ms']-r['mono']*1000 for r in sends.values()),'server_ack_within_200ms_ingress_count':len(response_delays),'server_nearest_ack_delay_max_ms':max(response_delays)*1000,'bursts':bursts,'worst_upstream':worst}
 result[n]={'summary':summary,'upstream_delays':observations,'downstream_delays':down};print(n,json.dumps(summary,ensure_ascii=False))
(p/'delay-bounds.json').write_text(json.dumps(result,ensure_ascii=False,indent=2)+'\n')
