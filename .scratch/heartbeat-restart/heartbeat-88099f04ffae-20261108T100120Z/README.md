# 88099f0 统一发布本地冻结与待执行规划

> 本文为执行前记录。获得下一轮执行授权后已完成全11节点发布，实际结果与父agent交接见 [EXECUTED.md](EXECUTED.md)。

状态：仅本地准备完成，等待用户明令执行下一批。真实 model 仍在运行；本轮未进行 SSH、远端读取、preflight、stage、prepare、stop、install、start、audit、重启或配置变更，未创建远端 release source root，未获取远端锁。运行中的全 11 节点仍按用户指定保持 c988。

冻结 commit：`88099f04ffaee56c576bb35a9e50baec5f1113d7`。本地 `source.tar` SHA256：`6b0f1ebb80cc23343fced4ba3f323a498d04472e7ef03bf2bfa25265605903c4`。归档按既有规则不入 Git，可用 state 中 commit 和 release CLI archive 路径列表精确重建。

state.expected 显式为 grant_duration_ms=18、guard_interval_ms=2、fec_n=14；completed=[]。CLI 提供正式 init 参数 `--expected-grant-ms`、`--expected-guard-ms`、`--expected-fec-n`，CLI 无参数默认分别为10、10、14；本批显式传入18、2、14。node helper 只消费本批 state 参数，不回退旧 state。server audit 同时要求 source JSON（字段缺省10/10/14）和实际 native argv 等于显式 state 预期，不会忽略18/2。全节点 native argv K/N 必须为8/14，且 GET_FEC 实际响应须为8/14。

客户端 FEC12 的 startup `--fec-n 12` 已由88099f0支持。helper 在未来获准执行的 prepare 中为客户端标准模块 argv 显式设置本批 `--fec-n 14`，如已有则更新值，并记录 `startup-configuration-change.json` 的 before/after/expected；其余启动参数保留。支持未来显式选择12，本批并未选择或运行12。不改 node.json，不自动改 server JSON，server预期若不一致则拒绝。当前标准模块载入机制、manifest consumer 契约保持不变，不新增 wrapper。

## 本地验证

`local-tests.txt`：9项纯函数测试通过，测试以 AST 提取函数方式隔离 node helper 顶层，未调用任何真实 socket、systemctl 或远端命令。覆盖18/2/14、旧10/10缺省、错误schedule/FEC、重复/缺失参数、bytes proc解析、FEC12/14 startup保持其它参数、GET_FEC正确和错误响应。

`local-prescan.json`：归档SHA复算一致；116个冻结Python文件AST语法通过；src、fec.c、fec.h、Makefile、scripts/install、scripts/systemd相对c988无变化。init亦通过既有d898d21原生源码一致性检查。native固定预期SHA仍为 `ad3340e51eecc6e5691a6edb84f26f3a262b518c3555497df62ea6f7865e1e56`。这是静态ABI预扫，不代表已远端验证依赖、实际Python导入、设备或运行态。

## 获得新一轮明确执行指令后的规划（本轮不执行）

已读取CLI known-stages，顺序为：preflight → stage → prepare → stop → install → start → audit。prepare会创建远端release root并备份、解包，不能当只读步骤提前执行。

1. 用户明确授权且model作业结束后，确认全11完整目标、server无活动作业且IDLE；执行全11 preflight，任何节点失败即停止推进，不排除fl03。不借机重设其它参数。
2. 复核本地archive/helper摘要与state后执行stage、prepare。逐节点记录实际接口、原参数、配置、UFTP与备份；客户端唯一预期参数差异为显式 `--fec-n 14`。source JSON中的server grant18/guard2/FEC14若不符需报告，不自动重置配置。
3. 全11前置阶段通过后再统一stop/install/start；保持标准Python模块单一路径和已有identity consumer契约，不改载入机制。
4. 全11audit核验标准proc入口、源码/manifest、nativeSHA、native argv与GET_FEC、18/2实际schedule、UFTP、15W、LGI、149/40MHz/idx18和原接口。待全部client READY后方可交付；READY本身不等于FEC一致性证明。
5. 本批不触发HT40 calibration或通信实验；88099f0只是交付该能力，候选标定需另行明确指令。执行期间继续遵守5分钟边界与1分钟状态回报。

本报告用于父agent交接；未通过工具向其它agent发送消息。helper修改和证据仅以中文提交，不混入其它工作。
