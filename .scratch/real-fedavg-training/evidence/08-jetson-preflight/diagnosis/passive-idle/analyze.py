import pathlib,json,collections,statistics
p=pathlib.Path(__file__).resolve().parent
allrows={}
for n in ['fl00','fl01','fl02','fl04']:
 rows=[]
 for line in (p/(n+'-capture.jsonl')).read_text().splitlines():
  try:rows.append(json.loads(line))
  except Exception:pass
 allrows[n]=rows
server=allrows['fl00'];s_udp=[r for r in server if r['kind']=='udp'];apis=[r for r in server if r['kind']=='api'];out={'capture_complete':{n:any(r['kind']=='end' for r in rows) for n,rows in allrows.items()},'nodes':{}}
for n,num in [('fl01',1),('fl02',2),('fl04',4)]:
 c=allrows[n]; hb=[r for r in c if r['kind']=='udp' and r['body'].get('type')=='NODE_HEARTBEAT'];rx=[r for r in s_udp if r['body'].get('type')=='NODE_HEARTBEAT' and r['body'].get('node_id')==num]
 # Only compare common wall-free window: a payload sent after capture begins at all endpoints, not near capture-end.
 s_by_hash=collections.defaultdict(list)
 for r in rx:s_by_hash[r['payload_sha256']].append(r)
 matches=[]
 for r in hb:
  sr=s_by_hash.get(r['payload_sha256'],[])
  matches.append({'timestamp_ms':r['body'].get('timestamp_ms'),'state':r['body'].get('state'),'channel':r['body'].get('current_channel'),'client_send_mono':r['mono'],'client_ip_id':r['ip_id'],'server_receipts':[{'mono':x['mono'],'ip_id':x['ip_id']} for x in sr]})
 acks_s=[r for r in s_udp if r['body']=={'ack':True} and r['dst']=='10.80.0.'+str(10+num)];acks_c=[r for r in c if r['kind']=='udp' and r['body']=={'ack':True} and r['dst']=='10.80.0.'+str(10+num)];cack_ids={(r['ip_id'],r['dport']) for r in acks_c}
 stamps=[r['body']['timestamp_ms'] for r in rx];reordered=[(a,b) for a,b in zip(stamps,stamps[1:]) if b<=a]
 api=[{'mono':r['mono'],'readiness':r['nodes'][str(num)]['readiness'],'last_hb_mono':r['nodes'][str(num)]['last_heartbeat_time'],'age':r['nodes'][str(num)]['last_heartbeat_ago_seconds'],'reported_state':r['nodes'][str(num)]['reported_state']} for r in apis]
 ingress_registry=[]
 for r in rx:
  future=[a for a in api if a['mono']>=r['mono']]
  a=future[0] if future else None
  ingress_registry.append({'server_rx_mono':r['mono'],'payload_timestamp_ms':r['body']['timestamp_ms'],'timestamp_is_client_monotonic_ms':True,'next_snapshot':a,'registry_advanced_at_or_after_rx':bool(a and a['last_hb_mono']>=r['mono']-.05)})
 sendgaps=[b['mono']-a['mono'] for a,b in zip(hb,hb[1:])];rxgaps=[b['mono']-a['mono'] for a,b in zip(rx,rx[1:])]
 summary={'hb_client_observed':len(hb),'hb_server_observed':len(rx),'hb_client_matched_at_server':sum(bool(m['server_receipts']) for m in matches),'hb_client_missing_at_server':sum(not m['server_receipts'] for m in matches),'client_states':dict(collections.Counter(r['body']['state'] for r in hb)),'client_channels':dict(collections.Counter(r['body']['current_channel'] for r in hb)),'ack_server_observed':len(acks_s),'ack_client_observed':len(acks_c),'ack_server_matched_at_client':sum((r['ip_id'],r['dport']) in cack_ids for r in acks_s),'server_payload_timestamp_nondecreasing_violations':reordered,'max_client_send_gap_s':max(sendgaps,default=None),'max_server_hb_receive_gap_s':max(rxgaps,default=None),'api_offline_samples':sum(a['readiness']=='OFFLINE' for a in api),'api_max_age_s':max((a['age'] for a in api),default=None)}
 out['nodes'][n]={'summary':summary,'hb_matches':matches,'api_samples':api,'ingress_registry_checks':ingress_registry}
 print(n,json.dumps(summary,ensure_ascii=False))
(p/'aligned-analysis.json').write_text(json.dumps(out,ensure_ascii=False,indent=2)+'\n')
