"""按父新增规则监督精确 UFTP 证据；只读本地归档，确认容量拒绝即写 STOP。"""
import json,pathlib,re,time,os
ROOT=pathlib.Path(__file__).resolve().parent
seen=set()
patterns=[re.compile(r'Not enough disk space',re.I),re.compile(r'No space left on device',re.I),
          re.compile(r'(?:UFTP|uftp)[^\n]{0,160}(?:exit(?:ed)?(?: status| code)?|returncode|return code|rc)[ =:]+8\b',re.I)]
(ROOT/'infrastructure-monitor-process.json').write_text(json.dumps(dict(pid=os.getpid(),wall=time.time(),purpose='父新增：确认基础设施容量拒绝则停止后续矩阵'),ensure_ascii=False,indent=2)+'\n')
while True:
    hits=[]
    for path in ROOT.glob('*exact-uftp-kernel-files.json'):
        if str(path) in seen:continue
        try:data=json.loads(path.read_text())
        except (OSError,ValueError):continue
        seen.add(str(path))
        for row in data.get('rows',[]):
            for name,log in row.get('result',{}).get('logs',{}).items():
                lines=str(log).splitlines()
                matches=[line for line in lines if any(p.search(line) for p in patterns)]
                if name.endswith('.status') and re.search(r'(?:uftp|uftpd|publish|submit)',name,re.I) and str(log).strip()=='8':matches.append('精确 UFTP 状态文件退出码：8')
                if matches:hits.append(dict(evidence=str(path),host=row.get('host'),log=name,matches=matches))
    if hits:
        result=dict(reason='父新增停止条件：精确 UFTP 日志证实基础设施容量拒绝，禁止进入后续候选',wall=time.time(),hits=hits)
        (ROOT/'infrastructure-refusal-stop.json').write_text(json.dumps(result,ensure_ascii=False,indent=2)+'\n')
        temp=ROOT/'STOP.infrastructure.tmp';temp.write_text(json.dumps(result,ensure_ascii=False,indent=2)+'\n');temp.replace(ROOT/'STOP')
        print(json.dumps(result,ensure_ascii=False),flush=True)
        break
    try:
        matrix=json.loads((ROOT/'matrix.json').read_text())
        if matrix.get('status') in ['complete','stopped']:break
    except (OSError,ValueError):pass
    time.sleep(.2)
print(json.dumps(dict(finished_wall=time.time(),scanned_archives=len(seen),stop_created=(ROOT/'infrastructure-refusal-stop.json').exists()),ensure_ascii=False),flush=True)
