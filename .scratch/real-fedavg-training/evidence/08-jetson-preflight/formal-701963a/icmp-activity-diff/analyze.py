import json,pathlib,re,statistics,collections
P=pathlib.Path(__file__).resolve().parent
PHASES=['baseline','activity','post'];NODES=['fl00','fl01','fl02','fl04']
def read(f):return [json.loads(l) for l in f.read_text().splitlines()]
def gap(rows):return max((b['mono']-a['mono'] for a,b in zip(rows,rows[1:])),default=None)
def event_node(line,node):return re.search(r'(?:^| )node_id='+str(node)+r'(?: |$)',line) is not None
native={n:json.loads((P/(n+'-native-full-window.json')).read_text()) for n in NODES};out={}
for phase in PHASES:
 r={n:read(P/(phase+'-'+n+'.jsonl')) for n in NODES};sr=r['fl00'];s_udp=[x for x in sr if x['kind']=='udp'];apis=[x for x in sr if x['kind']=='api'];slo=sr[0]['mono']*1000;shi=next(x['mono'] for x in sr if x['kind']=='end')*1000;ns={}
 for n,node in [('fl01',1),('fl02',2),('fl04',4)]:
  cr=r[n];end=next(x for x in cr if x['kind']=='end');lo=cr[0]['mono']*1000;hi=end['mono']*1000;cu=[x for x in cr if x['kind']=='udp'];hb=[x for x in cu if x['body'].get('type')=='NODE_HEARTBEAT'];rx=[x for x in s_udp if x['body'].get('type')=='NODE_HEARTBEAT' and x['body'].get('node_id')==node];received={x['payload_sha256'] for x in rx};av=[x['nodes'][str(node)] for x in apis];statecounter=collections.Counter(x['body']['state'] for x in hb)
  st={}
  for line in native[n]['stats']:
   t,k,v=line.split('\t',2)
   if lo<=int(t)<=hi:st[(int(t),k)]=v
  def series(key,parse):return [(t,parse(v)) for (t,k),v in sorted(st.items()) if k==key]
  token=series('TOKEN_AUTH',lambda v:list(map(int,v.split(':'))));grant=series('GRANT_SEQ',lambda v:list(map(int,v.split(':'))));gf=series('GRANT_FILTER',lambda v:list(map(int,v.split(':'))));loop=series('CLIENT_LOOP',lambda v:{k:int(x) for k,x in re.findall(r'(\w+)=(\d+)',v)});batch=series('RX_BATCH',lambda v:list(map(int,v.split(':'))))
  delta=lambda a:[y-x for x,y in zip(a[0][1],a[-1][1])] if a else None
  token_delta=delta(token);gdelta=delta(grant);gfdelta=delta(gf);loopdelta={k:loop[-1][1][k]-loop[0][1][k] for k in loop[0][1]} if loop else None
  server_events=[x for x in native['fl00']['events'] if slo<=x['anchor_ms']<=shi and event_node(x['line'],node)];readies=[x for x in server_events if x['line'].startswith('ready_accept')];refreshes=[x for x in server_events if x['line'].startswith('ready_refresh')];removed=[x for x in server_events if x['line'].startswith('silent_remove')];sentgrants=[x for x in server_events if x['line'].startswith('grant seq=')]
  ping=next((x for x in cr if x['kind']=='owned-ping-reaped'),None);ping_summary=None
  if ping:
   m=re.search(r'(\d+) packets transmitted, (\d+)(?: packets)? received, ([\d.]+)% packet loss',ping['output']);rr=re.search(r'(?:rtt|round-trip) min/avg/max/(?:mdev|stddev) = ([\d.]+)/([\d.]+)/([\d.]+)/([\d.]+)',ping['output']);ping_summary={'pid':ping['pid'],'returncode':ping['returncode'],'transmitted':int(m[1]) if m else None,'received':int(m[2]) if m else None,'loss_percent':float(m[3]) if m else None,'rtt_ms':dict(zip(['min','avg','max','mdev'],map(float,rr.groups()))) if rr else None,'command':ping['args'],'raw_output':ping['output']};(P/(n+'-ping-output.txt')).write_text(ping['output'])
  own='10.80.0.'+str(node+10);requests=[x for x in cr if x['kind']=='icmp' and x['icmp_type']==8 and x['src']==own];serverreq=[x for x in sr if x['kind']=='icmp' and x['icmp_type']==8 and x['src']==own];replies=[x for x in cr if x['kind']=='icmp' and x['icmp_type']==0 and x['dst']==own];reqkeys={(x['identifier'],x['sequence']) for x in serverreq}
  ns[n]={'capture_duration_s':end['mono']-cr[0]['mono'],'capture_drops':end['socket_stats'][1],'max_client_HBsend_gap_s':gap(hb),'max_server_HBrx_gap_s':gap(rx),'hb_client_count':len(hb),'hb_server_count':len(rx),'matched_HB':sum(x['payload_sha256'] in received for x in hb),'hunt_packet_ratio':statecounter['HUNTING']/len(hb) if hb else None,'client_HBstates':dict(statecounter),'channels':dict(collections.Counter(x['body']['current_channel'] for x in hb)),'api_offline_samples':sum(x['readiness']=='OFFLINE' for x in av),'api_samples':len(av),'api_ready_samples':sum(x['readiness']=='READY' for x in av),'api_max_HBage_s':max((x['last_heartbeat_ago_seconds'] for x in av),default=None),'server_READY_accept':len(readies),'server_READY_refresh':len(refreshes),'server_silent_remove':len(removed),'server_grants_logged':len(sentgrants),'GRANT_SEQ_first_last':[grant[0],grant[-1]] if grant else None,'GRANT_SEQ_delta':gdelta,'GRANT_FILTER_delta':gfdelta,'TOKEN_AUTH_first_last':[token[0],token[-1]] if token else None,'TOKEN_AUTH_delta':token_delta,'CLIENT_LOOP_delta':loopdelta,'RX_BATCH_max_us':max((x[1][3] for x in batch),default=None),'RX_BATCH_max_packets':max((x[1][4] for x in batch),default=None),'ping':ping_summary,'icmp_request_at_client':len(requests),'icmp_request_at_server':len(serverreq),'icmp_reply_at_client':len(replies),'icmp_unique_client_request_matched_at_server':sum((x['identifier'],x['sequence']) in reqkeys for x in requests),'owner_cleanup':next((x for x in cr if x['kind']=='owner-cleanup-complete'),None),'server_events':server_events}
 out[phase]={'nodes':ns,'server_capture_duration_s':next(x['mono'] for x in sr if x['kind']=='end')-sr[0]['mono'],'server_capture_drops':next(x['socket_stats'][1] for x in sr if x['kind']=='end'),'server_API_active_jobs_seen':any(x['active_job'] is not None for x in apis)}
(P/'comparison.json').write_text(json.dumps(out,ensure_ascii=False,indent=2)+'\n')
for ph,x in out.items():
 print(ph)
 for n,r in x['nodes'].items():print(n,'gaps',r['max_client_HBsend_gap_s'],r['max_server_HBrx_gap_s'],'HUNT',r['hunt_packet_ratio'],'offline',r['api_offline_samples'],'READY/remove',r['server_READY_accept'],r['server_silent_remove'],'grant/sends/denied',r['GRANT_SEQ_delta'][0],r['TOKEN_AUTH_delta'][2:],'loop',r['CLIENT_LOOP_delta'],'RXmaxus',r['RX_BATCH_max_us'],'icmp client/server/reply',r['icmp_request_at_client'],r['icmp_request_at_server'],r['icmp_reply_at_client'])
