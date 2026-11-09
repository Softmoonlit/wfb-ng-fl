# B2模型 continuation：九端完整闭环成功

父明确授权在small后已独立确认全10/九端恢复成功的事实基础上继续核心B2模型对照。此目录仅执行剩余1次模型，不重跑small；本批合计2execute。父目录原small after-audit、STOP、worker和原阶段README均保持原样，它们记录当时阶段，不代表本continuation之后的最终状态。

两次完整模型前准入均通过：初始server10/10，以及标准设置30/5后的全10实读审计/九端READY门禁；不需settled复核，不改功耗、源码、native、依赖或冻结bundle。固定fl00+[1,2,3,5,6,7,8,9,10]，04排除。审计source引用release_root/source/wfb_ng/fl，原冻结bundle SHA为ab8697fe4573ac3c1088dd11022a1fd75a25cf13752d2717de02e6aa2e625e9e。

模型runid=rsmc20261109T060537Z-g30-d5-model。模型44770302字节/SHA4b55cee2f0bdcfbbf610167a505efee0e2c50e3306fed02bda3613807c234e96；30/5、FEC14、实际UFTP12000Kbps，方向600s/overall1290。下行整组54.181630838s（sender49.064992870s），九端receive_verified；上行整组416.281289804s，九端submit成功。原始transfer_verified=true、cleanup_ok=true、restore_ok=true，immediate独立after-audit=true/九端READY=true。冻结bundle未变。

最终IDLE独立读取server的九份updates/<node>/update.bin，每份44770302字节且SHA正确，见model-delivery-facts.json。十端exactrun UFTP和kernel日志保存在*-final-idle-exact-uftp-kernel-files.json，并另留before-final-recovery日志；未匹配USB reset。客户端本地update.bin不充当上传送达证据。

只回收本轮exactunits。server从30/5标准恢复10/10，由配置变化触发一次server标准restart；没有额外客户端重启、升级或重试。final-closeout/result.json：全10实际15W/source/native/149HT40 LongGI/index18/MCS3/4/GETFEC8/14基线=true，九端真实READY/IDLE=true，exact cleanup=true，总execute_count=2，无测量错误。追加定向快照前后server均IDLE、active_job=null，worker已finished/complete。

B1模型48.5879s对应sender失败/零receive_verified，不应作为完整下行吞吐完成时间；A2模型50.3435s九端下行完整、UL600s仅03完整上传；本次B2模型54.1816s九端下行完整、UL416.2813s九端完整上传。因此此次没有重现B1下行失败，B2全闭环成功。单次对照不构成NIC损坏或排除环境/状态因素的因果结论。small原始恢复标志虽true，但瞬时fl02实际157导致独立after-audit=false，原失败与之后已恢复及模型成功分别保留。

追加的02 vs全端定向快照仅只读白名单字段，不含管理环境或secret，见final-idle-02-vs-all-targeted-snapshot.json和02-vs-all-comparison.json。全10OS release为Ubuntu22.04.5LTS、kernel release为5.15.148-tegra。全10 L4T release与driver sysfs srcversion也相同。九客户端driver模块文件SHA全部9a3a2a1303d7585cdb5245a92e8fd2280902de2e209f22319c7b2e42152ef491；server文件SHA为1a845230125dab762649f80da85ee53bfd494ebaf1a8eb68d43372477a604a13。此为磁盘模块文件摘要，不是内核运行内存摘要。所选显式client radio值channel149/HT40/txpower18/uplinkMCS4/FEC14一致，node身份及interface/MAC按各端不同；快照记录unit/drop-in文件摘要及过滤后的关键radio字段，不主张所有包版本/全部配置相同。02当下USB5000Mbps、实际149，00为480Mbps、10为5000Mbps；速率及伙伴路径本身不判故障。

UFTP可执行程序/5.0.3版本一致；source/native按冻结manifest核验通过。不能写所有动态库全相同：libcrypto.so.3 SHA两组，00/01/02/03/05为6df6c9ca3eb2a0ecded968b7aafb9e97d0942ffeebdcf916a35d3be2bf6a5810，06/07/08/09/10为e7f5e30d7eeec2259c07493083c3ae6d0c0d361e2ae9127b19fe49c0a1a32f6f。该分组不孤立02，现有证据不能据此归因02；依赖未变更。
