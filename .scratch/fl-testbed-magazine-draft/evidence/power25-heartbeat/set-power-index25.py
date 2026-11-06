import json
import os
import pathlib
import socket
import subprocess

host = socket.gethostname()
assert host in ['fl00','fl01','fl02','fl04','fl05','fl06','fl07','fl08','fl09','fl10']
assert os.geteuid() == 0
interfaces = []
for path in pathlib.Path('/sys/class/net').iterdir():
    driver = path/'device/driver'
    if driver.exists() and pathlib.Path(os.path.realpath(driver)).name == 'rtl88xxau_wfb':
        interfaces.append(path.name)
assert len(interfaces) == 1, interfaces
interface = interfaces[0]

def run(args):
    p = subprocess.run(args,capture_output=True,text=True,timeout=10)
    p.check_returncode()
    return p.stdout

before = run(['iw','dev',interface,'info'])
run(['iw','dev',interface,'set','txpower','fixed','-2500'])
after = run(['iw','dev',interface,'info'])
assert 'txpower -25.00 dBm' in after, after
module = pathlib.Path('/sys/module/88XXau_wfb/parameters/rtw_tx_pwr_idx_override')
result = {'host':host,'interface':interface,'before':before,'after':after,'actual_power_index':25,'module_override':module.read_text().strip() if module.exists() else None,'scope':'受控硬件功率对照，守护进程配置仍为原值；不将心跳中配置值视为实际功率'}
print(json.dumps(result,ensure_ascii=False,indent=2))
