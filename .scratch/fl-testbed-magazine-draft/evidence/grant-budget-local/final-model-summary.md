# nine-grant10-model-20261108-1 只读最终审计
下行九端收完并 hash 一致；上行 2/9 完成（fl01、fl07），服务端等待 300s 后配额不足失败。
publish 开始约 1794124974.7539816（前序 barrier 推定）；submit 开始约 1794125077.3580048（最终接收 barrier 推定），精确 dispatch 未记录。
实际 bytescompleted=89540604；remainingpartial_bytes=0。已提交文件保留，partial 不计作完成。

- {'path': '/srv/wfb-ng-fl/comm-nine-grant10-model-20261108-1/role/server_role.json', 'size_bytes': 240}
- {'path': '/srv/wfb-ng-fl/comm-nine-grant10-model-20261108-1/role/runtime.lock', 'size_bytes': 0}
- {'path': '/srv/wfb-ng-fl/comm-nine-grant10-model-20261108-1/role/current-round.json', 'size_bytes': 87}
- {'path': '/srv/wfb-ng-fl/comm-nine-grant10-model-20261108-1/role/rounds/923d6e06-36cf-4002-be7d-8e0c0b2954eb/uftp-d7b0d41e-4f84-45a0-93c9-5353bf5e71cd.status', 'size_bytes': 2354}
- {'path': '/srv/wfb-ng-fl/comm-nine-grant10-model-20261108-1/role/rounds/923d6e06-36cf-4002-be7d-8e0c0b2954eb/uftp-e6342bae-f7bb-435d-a28e-886c0dd2ed4d.log', 'size_bytes': 19602}
- {'path': '/srv/wfb-ng-fl/comm-nine-grant10-model-20261108-1/role/rounds/923d6e06-36cf-4002-be7d-8e0c0b2954eb/model.manifest.json', 'size_bytes': 237}
- {'path': '/srv/wfb-ng-fl/comm-nine-grant10-model-20261108-1/role/rounds/923d6e06-36cf-4002-be7d-8e0c0b2954eb/round-state.json', 'size_bytes': 266}
- {'path': '/srv/wfb-ng-fl/comm-nine-grant10-model-20261108-1/role/rounds/923d6e06-36cf-4002-be7d-8e0c0b2954eb/model.bin', 'size_bytes': 44770302, 'sha256': '4b55cee2f0bdcfbbf610167a505efee0e2c50e3306fed02bda3613807c234e96'}
- {'path': '/srv/wfb-ng-fl/comm-nine-grant10-model-20261108-1/role/rounds/923d6e06-36cf-4002-be7d-8e0c0b2954eb/updates/7/update.bin', 'size_bytes': 44770302, 'sha256': '4b55cee2f0bdcfbbf610167a505efee0e2c50e3306fed02bda3613807c234e96'}
- {'path': '/srv/wfb-ng-fl/comm-nine-grant10-model-20261108-1/role/rounds/923d6e06-36cf-4002-be7d-8e0c0b2954eb/updates/7/update.manifest.json', 'size_bytes': 206}
- {'path': '/srv/wfb-ng-fl/comm-nine-grant10-model-20261108-1/role/rounds/923d6e06-36cf-4002-be7d-8e0c0b2954eb/updates/1/update.bin', 'size_bytes': 44770302, 'sha256': '4b55cee2f0bdcfbbf610167a505efee0e2c50e3306fed02bda3613807c234e96'}
- {'path': '/srv/wfb-ng-fl/comm-nine-grant10-model-20261108-1/role/rounds/923d6e06-36cf-4002-be7d-8e0c0b2954eb/updates/1/update.manifest.json', 'size_bytes': 206}
- receive fl05: publish+101.297s
- receive fl02: publish+101.301s
- receive fl06: publish+101.306s
- receive fl08: publish+101.310s
- receive fl01: publish+101.313s
- receive fl07: publish+101.318s
- receive fl04: publish+101.384s
- receive fl09: publish+102.356s
- receive fl10: publish+102.604s
- submit fl07: +192.882s ok=True
- submit fl01: +196.203s ok=True
- submit fl00: +300.014s ok=False
- fl00: RF149/40=True, 15W/sys=True, PID=True, USB reset=0, errors=[]
- fl01: RF149/40=True, 15W/sys=True, PID=True, USB reset=0, errors=[]
- fl02: RF149/40=True, 15W/sys=True, PID=True, USB reset=0, errors=[]
- fl04: RF149/40=True, 15W/sys=True, PID=True, USB reset=0, errors=[]
- fl05: RF149/40=True, 15W/sys=True, PID=True, USB reset=0, errors=[]
- fl06: RF149/40=True, 15W/sys=True, PID=True, USB reset=0, errors=[]
- fl07: RF149/40=True, 15W/sys=True, PID=True, USB reset=0, errors=[]
- fl08: RF149/40=True, 15W/sys=True, PID=True, USB reset=0, errors=[]
- fl09: RF149/40=True, 15W/sys=True, PID=True, USB reset=0, errors=[]
- fl10: RF149/40=True, 15W/sys=True, PID=True, USB reset=0, errors=[]
GET readiness 与原始 RF、PID、/sys、kernel 输出保存在 JSON；kernel 查询失败或空输出不等于已证明没有 USB reset。未发送新实验、未创建 role、未清理或改写服务端文件、未改源码或提交。

最终 GET：九客户端全部 READY/IDLE；十台实际 RF149/40MHz、15W及既有 resident PID/starttick 全部通过。
临时文件按 transport 实际前缀 .update.bin.* 核对，服务端目录无剩余临时文件，remainingpartial=0。
Kernel：十台 journal 查询均为 -- No entries --；未发现 USB reset，但该窗口无可用记录，不能证明未发生。
