# fl04 临时提高发射功率到 18 档的常驻心跳对照

## 条件与做法

用户要求提高 fl04 发射功率排查心跳丢失。四端保持实际部署提交 `607c064`、149/HT40+、下行 MCS3、上行 MCS4、UFTP15000。只有 fl04 从驱动14档临时提高到18档；不重启服务、不更改其他端、协议期限、MCS或持久配置。通过已部署 `LinuxNetworkAdapter.set_txpower()` 同时设置驱动 override 和 `iw`；22次采样读回均为 -18.00 dBm，override=18。

守护进程的配置/心跳仍申报14，因此网页配置值不能作为本实验实际功率证据；本实验只调整硬件，真实射频参数以 `power18-readback.txt` 和 `samples.jsonl` 的 iw 为准。不修改注册表或伪造 READY。执行器为上一级 `test-fl04-power.py`。

## 结果

120.21秒取得102份采样：约35.91秒从 READY/IDLE 进入 CONNECTING/HUNTING（当时申报信道153），36.91秒恢复 READY/IDLE/149。未采到 OFFLINE，最大心跳年龄7.74秒，但未满足连续全员READY、无寻频的稳定门禁。提高功率未消除故障。

两端85秒被动捕获均正常结束：fl04发出25条心跳，Server均观测到，并有25个可按心跳身份配对的ACK；其中8个RTT超过230ms，最大4.349秒。ACK实际到达不等于在原请求截止时间内通过信道与请求校验，详见 `ack-summary.json`。

前一14档三客户端85秒窗口为45条心跳、45个可配对ACK，31个RTT超过230ms、最大9.686秒。两个窗口中的寻频次数和请求数量不同，仅各一次，不能将18档较小的RTT数字当作已确认的功率收益；确定结论仅为18档仍会触发寻频。

## 控制帧 MCS

当前实现没有单独最低速率控制通道。`src/v6_radio_control.cpp` 的 `build_v6_radio_target()` 让 `control_radiotap=data_radiotap`；`AirTransmitter::send_ready()` / `send_grant()` 均经该控制radiotap发送。客户端上行心跳、READY随上行MCS4；Server下行ACK、GRANT随下行MCS3。802.11 HT物理层存在更低的MCS0，当前未对控制帧使用它；普通项目配置允许值另受radio校验范围限制。

## 收尾

通过 finally 恢复 fl04 到14档；`restored.json` 明确读回149/HT40+/-14.00 dBm。无活动训练作业，未启动四机冒烟、HT20标定或真实训练。此结果不证明网卡损坏，也没有进一步确定上行丢失发生在发射、驱动、空口或接收重组哪个环节。
