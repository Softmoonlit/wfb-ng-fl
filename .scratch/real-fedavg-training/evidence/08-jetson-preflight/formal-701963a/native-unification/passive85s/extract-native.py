import os,pathlib,json,re,sys
host=os.uname().nodename;root=pathlib.Path('/srv/wfb-ng-fl');role='server' if host=='fl00' else 'client';lo=float(sys.argv[1])*1000-2000;hi=float(sys.argv[2])*1000+2000
path=root/role/'wfb_uplink.log';anchor=None;events=[];stats=[]
for line in path.open():
 m=re.match(r'^(\d+)\t',line)
 if m:anchor=int(m.group(1))
 if anchor is None or not lo<=anchor<=hi:continue
 if any(key in line for key in ['ready_accept','ready_refresh','silent_remove','grant seq=','grant_accept','first_declare','redeclare','state_transition','RX_ANT','TX_ANT']):events.append({'anchor_ms':anchor,'line':line.strip()})
 if any(key in line for key in ['\tPKT\t','\tPKT_SRC\t','\tGRANT_FILTER\t','\tREADY_FILTER\t','\tTOKEN_AUTH\t','\tRX_BATCH\t','\tGRANT_SEQ\t','\tCLIENT_LOOP\t']):stats.append(line.strip())
print(json.dumps({'host':host,'path':str(path),'range_ms':[lo,hi],'anchor_resolution_ms':1000,'events':events,'stats':stats},ensure_ascii=False,indent=2))
