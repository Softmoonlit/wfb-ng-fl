# fl02与当前九客户端的软件和配置对比

本次仅核对已保存的实际审计结果和模型结束后的只读快照，没有更改版本、驱动或无线参数。参与集合为fl00＋[1,2,3,5,6,7,8,9,10]。

## 已确认相同的内容

- 九客户端40项发布文件的预期、安装目录和冻结源码摘要一致；源码提交为`1cd6264d5d6be660998ce60f3a349063009ecfa7`。
- 运行中native二进制SHA-256全部为`44d6bbf500f51df4d0b3ae8cd7a53c4a45cb55b09ca66cb5217e7ac75674feb2`。排除节点编号、TUN名称/IP和网卡接口名称后，native启动参数相同。
- native查询均为40 MHz、上行MCS4、固定Long GI、RS(8,14)。基线射频读回为149/HT40、驱动功率索引18；实际15 W CPU/GPU/EMC限制相同。
- 九客户端均为Ubuntu 22.04.5、内核5.15.148-tegra、L4T 36.4.3；驱动版本、sysfs srcversion和磁盘驱动模块摘要均相同，模块摘要为`9a3a2a1303d7585cdb5245a92e8fd2280902de2e209f22319c7b2e42152ef491`。磁盘模块摘要不等于对已加载模块内存逐字节取证。
- 九客户端当前专用网卡USB均读回5000 Mbit/s。fl02当前MAC为`fc:22:1c:30:0f:04`。
- UFTP/uftpd均为5.0.3，两个程序各自的可执行文件摘要全十板相同。

依据：本目录`initial-ten-board-baseline-audit.json`与`model-continuation-after-confirmed-recovery/final-idle-02-vs-all-targeted-snapshot.json`。

## 确实存在的差异及边界

- 节点编号、IP、接口/MAC不同是身份所需。systemd历史drop-in文件布局和摘要不完全相同，但已提取的有效无线启动参数相同；不能把整套配置文件说成逐字节一致。
- `libcrypto.so.3`有两组摘要：fl00/01/02/03/05为`6df6c9ca3eb2a0ecded968b7aafb9e97d0942ffeebdcf916a35d3be2bf6a5810`，fl06/07/08/09/10为`e7f5e30d7eeec2259c07493083c3ae6d0c0d361e2ae9127b19fe49c0a1a32f6f`。这不是fl02独有差异，目前没有证据把fl02的上传或信道恢复异常归因于它。测量期间没有升级依赖。
- 服务端磁盘驱动模块摘要为`1a845230125dab762649f80da85ee53bfd494ebaf1a8eb68d43372477a604a13`，与客户端不同；报告的版本、srcversion及vermagic相同，尚未分析二进制差异原因，不能称全十板驱动文件逐字节一致，也不能据此归因fl02。

## fl02本次异常和最终结果

1 MiB闭环成功后，首次独立恢复检查在fl02读到157信道，其他native参数仍正常。原始失败审计保留，随后独立全十板149基线和九端READY检查通过。该证据证明恢复存在过瞬时不一致，但不能仅凭它判断是切换确认时序、重新扫频还是其他原因；未做专门复现，因此根因尚未确定。

全新模型续测没有重跑小文件。30/5、RS(8,14)、实际UFTP 12000 Kbps下，44,770,302字节模型九端全部完成：下行54.1816秒，上传416.2813秒，所有size/SHA校验、清理和恢复均通过。fl02也成功完成，最终全十板149实际基线与九端READY通过。见`model-continuation-after-confirmed-recovery/fec14-g30-d5-model/run-summary.json`与`final-closeout/result.json`。

因此目前没有证据显示fl02因单独运行旧版软件或使用独有无线配置而异常；它的历史上传尾部问题与本次短暂信道恢复异常应分别保留。本次通过不代表这些间歇问题已被修复，也不代表调度/FEC组合已获得连续两次模型闭环验收。
