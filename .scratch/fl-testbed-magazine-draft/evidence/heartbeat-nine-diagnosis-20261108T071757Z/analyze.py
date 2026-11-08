import json,pathlib,collections,re
r=pathlib.Path(pathlib.Path('/tmp/hb-nine-root').read_text());s=json.loads((r/'fl00.json').read_text());out={}
def key(e):
 b=e['body'];return (b.get('boot_id'),b.get('timestamp_ms',b.get('heartbeat_timestamp_ms')))
def channel(x):return re.findall(r'type monitor\s+channel (\d+)',x)
for h in ['fl01','fl05','fl04']:
 d=json.loads((r/(h+'.json')).read_text());ip='10.80.0.'+str(10+int(h[2:]));req=[e for e in d['events'] if e['direction']=='out' and e['body'].get('type')=='NODE_HEARTBEAT'];sr={key(e):e for e in s['events'] if e['direction']=='in' and e['src']==ip};sa={key(e):e for e in s['events'] if e['direction']=='out' and e['dst']==ip};ca={key(e):e for e in d['events'] if e['direction']=='in' and e['dst']==ip};rows=[]
 for e in req:
  k=key(e);a=ca.get(k); ack=a and a['body'];rows.append({'boot_id':k[0],'session':e['body'].get('session'),'timestamp_ms':k[1],'request_delta':e['delta'],'request_channel':e['body'].get('current_channel'),'request_state':e['body'].get('state'),'server_received':k in sr,'server_ACK':k in sa,'client_ACK':bool(a),'confirmed':ack.get('confirmed') if ack else None,'session_match':ack.get('session')==e['body'].get('session') if ack else None,'client_local_RTT_ms':(a['mono']-e['mono'])*1000 if a else None,'server_processing_ms':(sa[k]['mono']-sr[k]['mono'])*1000 if k in sr and k in sa else None,'deadline_seconds':10,'within_10s':bool(a and 0<=a['mono']-e['mono']<=10)})
 confirmed=[x for x in rows if x['confirmed'] and x['session_match'] and x['within_10s']]; cnt=collections.Counter()
 for x in rows:
  if not x['server_received']:cnt['request_to_server_missing']+=1
  elif not x['server_ACK']:cnt['server_no_ACK']+=1
  elif not x['client_ACK']:cnt['ACK_to_client_missing']+=1
  elif not x['confirmed']:cnt['challenge_unconfirmed']+=1
  elif not x['session_match']:cnt['ACK_session_mismatch']+=1
  else:cnt['confirmed_ACK_delivered']+=1
 stats={'emitted':len(req),'server_received':sum(x['server_received'] for x in rows),'server_ACK':sum(x['server_ACK'] for x in rows),'client_ACK':sum(x['client_ACK'] for x in rows),'confirmed_matching_within_deadline':len(confirmed),'segments':dict(cnt),'actual_monitor_channel_before':channel(d['before']['iw']),'actual_monitor_channel_after':channel(d['after']['iw']),'reported_channels':dict(collections.Counter(str(x['request_channel']) for x in rows)),'deadline_warnings':d['journal'].count('十秒内未确认有效心跳'),'liveness':'RED' if '十秒内未确认有效心跳' in d['journal'] else 'GREEN' if confirmed else 'UNKNOWN','channel_drift':'RED' if any(c!='149' for c in channel(d['before']['iw'])+channel(d['after']['iw'])) else 'GREEN_AT_SNAPSHOTS','native_keyword_lines':[]}
 for stage in ['before','after']:
  for n in d[stage]['native']:
   for fd in ['1','2']:
    stats['native_keyword_lines'] += [{'stage':stage,'pid':n['pid'],'fd':fd,'line':line} for line in n.get('tail'+fd,'').splitlines() if re.search('grant|ready|fec|session|CLIENT_LOOP',line,re.I)][-100:]
 out[h]=stats;(r/(h+'-chains.json')).write_text(json.dumps(rows,indent=2))
(r/'verdict.json').write_text(json.dumps(out,indent=2));print(json.dumps({h:{k:v for k,v in st.items() if k!='native_keyword_lines'} for h,st in out.items()},indent=2))
