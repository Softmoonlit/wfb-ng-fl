# 69a94d8 native 同构建统一记录

当前四端 Python 安装提交保持 `69a94d84b006666d01e257155bc7a2fc969bedef`。四端已安装且**运行中**的 `/proc/<nativePID>/exe` 均为同一个构建文件：

```text
SHA-256 8ddb419be0c99e174eeb91239092f266398ea248e0c3c71eff643e28bbcc08e0
ELF Build ID d1b2f1ef2705ef1d67a202178410533d83baad40
```

没有因为程序运行角色不同而使用不同native。后续验证必须以此统一记录为准，不把之前混合native数量实验包装成最终基线验收。

## 原差异及更正

此前fl00 SHA为8ddb419b…，三客户端为b4f81258…。`wfb_v6_uplink`是同一个多角色程序，先前README以server/client构建角色解释差异无证据，已撤回并更正。只读证据实际确认：

- fl00编译器及ELF.comment：GCC 11.4.0，Ubuntu修订 `11.4.0-1ubuntu1~22.04.2`；原Build ID为d1b2f1ef…。
- 三客户端：GCC 11.4.0，Ubuntu修订 `11.4.0-1ubuntu1~22.04`；原Build ID为6d174597…。修订差异、object摘要及完整ELF记录保存在 `flXX-abi-before.json`。
- 上述证明构建环境和产物不同，**未逐字节归因SHA差异全部来源**。没有把“编译器不同”当作功能等价的充分证明。

## 单次冻结构建与兼容检查

从git archive `69a94d8`全新解包到 `/srv/wfb-ng-fl/native-69a94d8-unified/source`，归档SHA为 `d723b33001929f5ff4ae687ff4ad0150c75b15abc2369725511b4209de20701b`。不使用协作dirty工作区。

只构建一次：

```text
make build_v6 COMMIT=69a94d84b006666d01e257155bc7a2fc969bedef VERSION=0.0.0 PYTHON=/usr/bin/python3
```

完整编译/链接命令、flags在 `build-once.log`；`build-manifest.json`保留全部object和native源文件SHA、编译器、ELF头/解释器/Build ID/.comment/符号版本和ldd。canonical文件留在/srv，不进入git证据。

安装前对同一canonical文件在四板执行 `readelf` 与 `ldd -v`：四端AArch64、ELF解释器 `/lib/ld-linux-aarch64.so.1`、glibc2.35，所需SONAME及符号版本可解析，没有`not found`。结果在 `flXX-new-binary-abi.txt`。这是动态加载依赖检查，不宣称所有运行分支已验收。

## 安装与运行来源

先确认server IDLE/no active job，逐端备份旧native至 `/srv/wfb-ng-fl/deploy-69a94d8/native-unification/native-before`；四端daemon停止后，确认MainPID=0、inactive且无native/UFTP残留，再安装同一canonical文件，之后恢复daemon。没有写Python安装源码、JSON、drop-in、期限或RF配置，没有发训练。

`flXX-stop/install/start.txt`保留实际动作，`flXX-elf-after.txt`保留最终Build ID。`runtime-unified-summary.json`和 `flXX-runtime-after.json`确认：

- /usr/bin安装SHA与/proc运行exeSHA均为8ddb419b…，四端相同。
- daemon cwd为 `/`，实际环境无PYTHONPATH；按实际cwd+实际environ重做systempython find_spec，均来自 `/usr/local/lib/python3.10/dist-packages`，Python身份和critical SHA均仍为69；没有dirty10/11 shadow。
- 运行native PID在统一安装后为fl00 215543、fl01 33474、fl02 30627、fl04 33342，daemon PID215499/33457/30609/33324。
- 所有daemon active，157/40MHz/驱动读回-14dBm（配置14），MCS3/4与15000基线保持。

## READY与诊断边界

统一后四个短API快照记录了node1先OFFLINE、随后全员READY、随后node2 OFFLINE。仅确认服务恢复及身份统一，**不宣称空闲链路已稳定或根因已修复**。恢复只读复查在 `flXX-resume-*.json`，运行SHA仍一致，无部署/抓包脚本残留。

用户随后授权一次当前fl02 OFFLINE的85秒被动双端捕获，记录在 [fl02-passive-90s/README.md](fl02-passive-90s/README.md)。没有重跑数量矩阵，也没有训练、调RF或改timeout。
