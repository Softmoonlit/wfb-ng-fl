# 可以现场再次互换00/10网卡及对应天线

2026-11-09 05:49:34 UTC核验：00/10标准systemctl stop均成功；inactive/dead、MainPID=0，无wfb_v6_uplink残留。SSH管理保持、其它客户端未再次重启、绑定未提前改动。

当前00=fc:22:1c:10:01:19，10=fc:a3:86:b3:86:72；再次交换后应00=fc:a3:86:b3:86:72、10=fc:22:1c:10:01:19。05=fc:22:1c:10:01:57不动，02不动，天线由用户现场随卡确认。物理交换尚未完成，交换后另行只读核验，尚未授权自动B2测量。

本轮exactunits回收成功，server配置10/10；全10恢复并未成功：停止前fl10实际157且注册OFFLINE，一次标准客户端恢复后仍未归149。父明确授权保留这个false并进行物理停服准备；ready_for_user_physical_swap=true不等于完整基线恢复。preflight.json、before-nic-identities.json、standard-stop-results.json保存原始事实。
