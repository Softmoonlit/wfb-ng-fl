# fl00/fl10 网卡及天线换回前停服准备完成

2026-11-09 05:20:15 UTC 核验完成，可以通知用户物理换回 fl00/fl10 的网卡及对应天线。物理操作尚未完成，天线由用户现场识别确认。

已先读取 server-fl10-swap-20261109T044926Z/README.md 及旧 hardware-swap-preparation 的 README、before-nic-identities.json、standard-stop-results.json；旧准备目录没有独立脚本。本次复用既有本机 fl00 sudo 执行和 fl10 SSH BatchMode/ConnectTimeout=4 的连接方式，以及标准 systemctl stop 方式；prepare.py 和 stop.py 保存本次有界操作。单次工具调用不超过30秒，SSH/本机远程脚本最长22秒、外层26秒，systemctl stop 最长15秒。

停服前 API 实读 active_job=null、server_state=IDLE；在线节点恰为 [1,2,3,5,6,7,8,9,10]，04不参与。本地进程检查无活动实验 runner。完整状态和进程证据见 preflight.json；前置检查及身份检查均通过，见 gate.json。

## 停服前硬件身份

| 主机 | interface | MAC | USB设备路径 | 速度 | 驱动 |
| --- | --- | --- | --- | --- | --- |
| fl00 | wlxfca386b38672 | fc:a3:86:b3:86:72 | /sys/devices/platform/bus@0/3610000.usb/usb1/1-2/1-2.1/1-2.1:1.0 | 480 Mbps | /sys/bus/usb/drivers/rtl88xxau_wfb |
| fl10 | wlxfc221c100119 | fc:22:1c:10:01:19 | /sys/devices/platform/bus@0/3610000.usb/usb2/2-1/2-1.3/2-1.3:1.0 | 5000 Mbps | /sys/bus/usb/drivers/rtl88xxau_wfb |

停服前两端均 active/running；fl00 MainPID=12048，fl10 MainPID=12609。完整 ExecStart、native argv、sysfs USB路径和速度见 before-nic-identities.json。native argv 原有 known-clients 中仍含04，本次没有修改任何绑定或参数；参与拓扑以本次固定九客户端和API在线记录为准。

## 标准停服结果

- fl00：systemctl stop wfb-fl-server-daemon.service 返回0；ActiveState=inactive、SubState=dead、MainPID=0，无 wfb_v6_uplink 进程。
- fl10：通过SSH执行 systemctl stop wfb-fl-client-daemon.service 返回0；ActiveState=inactive、SubState=dead、MainPID=0，无 wfb_v6_uplink 进程。

两端核验成功，stderr为空；standard-stop-results.json 中 ready_for_user_physical_swap=true。两端主机保持运行，SSH管理未关闭。

## 物理回换方向

- 将当前 fl10 的 fc:22:1c:10:01:19 网卡及对应天线移回 fl00。
- 将当前 fl00 的 fc:a3:86:b3:86:72 网卡及对应天线移回 fl10。
- 回换后预期：fl00=fc:22:1c:10:01:19，fl10=fc:a3:86:b3:86:72。
- 其他客户端（包含05）不换；本次未对其他客户端执行停服或配置操作。

只停上述两个WFB服务；没有关机、修改管理Wi-Fi、接口绑定、功耗或射频参数，没有编译、部署、启动新测试，也没有Git/index操作。服务保持停止；物理回换后尚需另行核验身份与绑定，本次不启动服务或实验。
