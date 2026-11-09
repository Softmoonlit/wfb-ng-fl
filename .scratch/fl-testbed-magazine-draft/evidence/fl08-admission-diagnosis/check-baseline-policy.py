"""只在本地隔离测试进程内还原固定基线方法，区分既有失败并复现旧缺陷。"""
import ast
import subprocess
import sys
import textwrap

import pytest
from wfb_ng.fl import control

baseline = 'c07b382f9d014972f6729293dc7770d1fa45f96e'
source = subprocess.check_output(['git', 'show', baseline + ':wfb_ng/fl/control.py'], text=True)
root = ast.parse(source)
client = next(item for item in root.body if isinstance(item, ast.ClassDef) and item.name == 'ControlPlaneClient')
methods = {'_supervisory_loop', '_send_heartbeat', '_handle_radio_switch_confirmed'}
for method in client.body:
    if not isinstance(method, ast.FunctionDef) or method.name not in methods:
        continue
    namespace = {}
    exec(compile(textwrap.dedent(ast.get_source_segment(source, method)), '<baseline-client-method>', 'exec'),
         control.__dict__, namespace)
    setattr(control.ControlPlaneClient, method.name, namespace[method.name])
raise SystemExit(pytest.main(sys.argv[1:] or ['-q', 'wfb_ng/tests/test_fl_job_radio_policy.py']))
