"""离线核验故障识别与八次执行边界；只提取函数，不导入编排模块。"""
import ast
import json
from pathlib import Path
import tempfile

ROOT=Path(__file__).resolve().parent

def main():
    tree=ast.parse((ROOT/'helpers.py').read_text())
    function=next(n for n in tree.body if isinstance(n,ast.FunctionDef) and n.name=='infrastructure_failure')
    code=compile(ast.fix_missing_locations(ast.Module(body=[function],type_ignores=[])),'离线故障识别','exec')
    cases=[('正常日志','UFTP version 5.0.3; completed',False),('中文退出码','UFTP 退出码为 8',True),('英文退出码','uftp exited with code 8',True),('磁盘不足','Not enough disk space',True),('系统空间不足','No space left on device',True),('磁盘错误编号','ENOSPC',True),('普通传输超时','UFTP transfer timed out',False),('普通非八退出码','UFTP 退出码为 4',False)]
    results=[]
    with tempfile.TemporaryDirectory(prefix='fec14-policy-') as directory:
        temp=Path(directory)
        def write(path,value):path.write_text(json.dumps(value,ensure_ascii=False))
        namespace=dict(ROOT=temp,json=json,write=write)
        exec(code,namespace)
        run=temp/'candidate';run.mkdir();(run/'fixture').mkdir()
        for label,text,expected in cases:
            write(temp/'fixture-after-exact-uftp-kernel-files.json',dict(logs={'uftp.log':text}))
            # 同时覆盖冻结 runner 默认转义中文的 JSONL 输出。
            (run/'fixture/progress.jsonl').write_text(json.dumps(dict(error=text))+'\n')
            actual=namespace['infrastructure_failure']('fixture',run)
            assert actual is expected,(label,expected,actual)
            results.append(dict(case=label,expected=expected,actual=actual,passed=True))
    driver=ast.parse((ROOT/'driver-base.py').read_text())
    schedule=next(ast.literal_eval(n.value) for n in driver.body if isinstance(n,ast.Assign) and any(isinstance(t,ast.Name) and t.id=='SCHEDULE' for t in n.targets))
    assert schedule==[(10,10),(20,10),(30,10),(20,5)] and len(schedule)*2==8
    result=dict(ok=True,cases=results,candidate_count=4,max_executions=8,worker_imported_or_started=False,remote_or_live_HTTP_actions=0,scope='仅在临时目录以合成日志核验故障分类，未运行传输或发布')
    (ROOT/'offline-policy-verification.json').write_text(json.dumps(result,ensure_ascii=False,indent=2)+'\n')
    print(json.dumps(result,ensure_ascii=False,indent=2))

if __name__=='__main__':main()
