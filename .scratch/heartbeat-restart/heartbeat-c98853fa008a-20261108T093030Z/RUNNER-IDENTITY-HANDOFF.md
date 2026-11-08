# Runner consumer 标准模块 identity 交接

当前部署保持不变；不重启、不重装、不调整配置、不恢复 wrapper。此次交接只提供只读校验，不执行 runner 通信阶段。

## 唯一发布路径（每个 fl00..fl10 节点相同）

- commit: `c98853fa008a945e3a87503051bae49f5d0083bd`
- release: `/srv/wfb-ng-fl/releases/heartbeat-c98853fa008a-20261108T093030Z`
- identity manifest: `/srv/wfb-ng-fl/releases/heartbeat-c98853fa008a-20261108T093030Z/manifest.json`
- frozen source: `/srv/wfb-ng-fl/releases/heartbeat-c98853fa008a-20261108T093030Z/source/wfb_ng/fl`
- installed: `/usr/local/lib/python3.10/dist-packages/wfb_ng/fl`
- build identity: `/usr/local/lib/python3.10/dist-packages/wfb_ng/fl/build_identity.json`
- `actualargs.json`: 发布时记录的标准模块 argv；实际准入仍须读取当前 `/proc/<MainPID>/cmdline`。
- `loaded.json`: audit 生成的独立解释器模块解析、PID、commit、source JSON 和 actualargs 记录，**不是** daemon 写出的 sys.modules，不存在旧式 `modules` 字段，不可按旧 wrapper 含义消费。

## Consumer 改动契约

`communication/node_agent.py` prepare 当前仍硬编码 `wrappers`、`WRAPPER_SHA`、`loaded['modules']`（约 188..205 行）。由 runneragent 修改自己的 consumer，删除这些 wrapper 前提，使用上面的显式 release/manifest 路径，并绑定固定 COMMIT。不要通过扫描目录选择 latest，也不要同时保留旧 wrapper 路径作为 fallback。

1. 根据节点角色，通过 systemctl MainPID 读取实际 proc argv 和 exe。argv 前五项必须严格为 `/usr/bin/python3 -I -B -m wfb_ng.fl.server_daemon`（fl00），或 `/usr/bin/python3 -I -B -m wfb_ng.fl.client_daemon`（fl01..fl10），实际 exe 解析到 `/usr/bin/python3` 同一文件。后续参数照常逐项验证；接口遵循实际绑定与既有 actualiface 规则。
2. 读取显式 identity manifest，验证 commit、native_sha、installed 受限路径；复用现有 `verify_source_files()` 检查安全相对路径、完整安装 SHA、冻结源码 SHA 和顶层 Python 集合一致。build_identity.json 是安装时生成元数据，对它验证安装 SHA 与 commit，不要求冻结归档中同 SHA。
3. 用 `/usr/bin/python3 -I -B -` 启动独立 probe，不修改 daemon，不注入 sys.path。对 daemon 对应模块、role、radio、control、transport 的 `import_module()` 结果检查 `__file__.resolve()` 映射到 manifest.installed，并逐模块匹配 manifest SHA。probe 文件提供具体可复用实现。runner 自己加载 role 时也须检查解析后的来源和 SHA；不能仅凭追加 sys.path 就认定来源正确。
4. 证据字段用 `mapped_imports`、`resident_actualargs`、`resident_pid`，标注 `separate isolated probe interpreter; not resident sys.modules`。不要把探针 import 称为 resident loaded modules。
5. native 唯一性/SHA、实际参数、radio、UFTP、15W、registry READY/IDLE、resident PID/starttime 等其它门禁继续独立保留。grant15/guard5 从当前 server JSON 与 native 实参核验，helper 的无字段缺省仍为10/10。

## 已验证的只读实现与证据

同目录 `standard-identity-probe.py` 可作为 reader 校验代码参考。执行方式是将其内容通过 stdin 送入节点 `/usr/bin/python3 -I -B -`，不是 daemon wrapper，不参与服务启动。

`standard-identity-fl00.json` .. `standard-identity-fl10.json` 均 exit=0，包含每个节点实际 proc 身份与 mapped imports 路径/SHA。没有运行 prepare、通信或无线重配，也没有修改标准发布机制。

当前 runner 文件存在并行工作区修改，本交接未修改这些文件，以免覆盖 runneragent 的工作。后续发布载入机制变更必须先明确提出并获得用户授权。
