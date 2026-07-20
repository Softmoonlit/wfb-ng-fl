# Issue #41 三机真实硬件验收执行手册

本手册扩展 `v6新底座无SSH手动上行演示手册.md` 已建立的三机无线拓扑和 uplink 基线证据，但采用不同的控制方式：**当前工作机是 server，并通过 SSH 控制 client1、client2 两台独立远程机器**。旧手册的“无 SSH”步骤只用于已有 `baseline_uplink`，不用于本次完整闭环编排。

不得用 namespace、same-host、ping、普通 TCP 文件探针或人工复制 model/update 替代正式 systemd 角色服务、Runtime 四接口、UFTP downlink 和 HTTP uplink。

## 1. 前提与参数

三台机器必须：

- 各自连接一张独立真实 monitor/raw injection 网卡，最终处于同一 `157/HT40+`。
- 在相同绝对路径保有同一 commit 的仓库；编排器会通过 SSH fail-closed 核对三个 hostname 和 commit。
- 已安装构建依赖、`uftp`、`uftpd` 和 systemd。
- 当前 server 能以非交互 SSH 访问两个 client；三机当前用户能非交互执行本流程所需的 `sudo`。
- 没有复用旧 TUN、multicast route、角色进程或工作目录。停止和无孤儿检查通过前不得清目录。

在 server 创建参数文件：

```bash
cd /home/现场用户/code/wfb-ng-fl
cp tests/real_hardware/issue41_ssh.env.example /tmp/issue41.env
$EDITOR /tmp/issue41.env
```

必须填写：

```bash
CLIENT1_SSH=user@client1-host
CLIENT2_SSH=user@client2-host
REMOTE_REPO=/home/user/code/wfb-ng-fl
SERVER_IFACE=server-monitor-interface
CLIENT1_IFACE=client1-monitor-interface
CLIENT2_IFACE=client2-monitor-interface
RUN_NAME=issue41_YYYYMMDD_HHMMSS
ARCHIVE_ROOT=/tmp/issue41_YYYYMMDD_HHMMSS
SSH_OPTIONS='-o BatchMode=yes -o ConnectTimeout=10'
```

`CLIENT1_SSH` 和 `CLIENT2_SSH` 必须解析到不同远程 hostname，且都不能是当前 server hostname。

## 2. 生成、部署与预检

所有命令只在 server 执行：

```bash
ORCH=tests/real_hardware/issue41_ssh_orchestrate.sh
bash "$ORCH" generate /tmp/issue41.env
bash "$ORCH" deploy /tmp/issue41.env
```

`deploy` 会在三机各自源码树执行 `make build_v6 && sudo make install_v8`，随后安装本角色配置、算法 JSON 和 `/etc/default/wfb-fl-*` 环境文件。通用 unit 显式启用安装包内的 `wfb_ng.fl.acceptance_fixture`，不使用源码树 `PYTHONPATH` 或验收专用 RoleService 分支。

人工把三张网卡配置为同一 `157/HT40+ monitor/UP` 后执行：

```bash
bash "$ORCH" preflight /tmp/issue41.env
```

该阶段在三机分别检查安装后二进制/unit、实际 monitor 网卡，并拒绝 `wfb_v6_uplink`、`wfb-fl-*`、`uftp/uftpd`、TUN、UFTP/HTTP 端口和 multicast route 残留。任何一台失败都必须先修复，不能继续。

## 3. 启动正式闭环

```bash
bash "$ORCH" start /tmp/issue41.env
```

编排顺序固定为：

1. SSH 启动 client1、client2 的 `wfb-fl-client.service`。
2. 每台 client 出现 `wfb-fl0` 后安装 `230.4.4.1 dev wfb-fl0`，运行 runtime 预检并采集 active unit/cgroup、TUN、route、socket 和无线快照。
3. 本机启动 server unit，安装相同 multicast route，运行 runtime 预检并采集同类快照。
4. 三端全部 ready 后，本机创建 `/run/wfb-ng/issue41-start.gate`，server fixture 才调用 `publish_model()`；client fixture 已阻塞在 `wait_for_model()`。
5. server 每轮只启动一次 shared native UFTP operation，把 `model.bin` 和 `model.manifest.json` 发给 UID 1、2。
6. client1 默认零训练延迟，client2 默认延迟 2 秒；两者使用同一 callable，独立校验模型后分别调用一次 `submit_update()`。
7. client `submit_update` timing 完成表示正式 Runtime 已收到并验证 HTTP `201 Created`；server `wait_for_updates` timing 完成才表示下行屏障和全员 update 严格同步边界均达成。

监控状态：

```bash
systemctl status wfb-fl-server.service --no-pager
ssh "$CLIENT1_SSH" systemctl status wfb-fl-client.service --no-pager
ssh "$CLIENT2_SSH" systemctl status wfb-fl-client.service --no-pager
```

算法正常返回后 unit 正常退出；这不是驻留服务。不要因 unit 变为 inactive 就清理 work dir。

## 4. 采集、停服与重启

三端算法完成后在 server 执行：

```bash
bash "$ORCH" collect /tmp/issue41.env
bash "$ORCH" stop /tmp/issue41.env
```

`collect` 通过 SSH 回收两个 client 的完整 round、算法结果、journal、queue、manifest、SHA 和 UFTP 证据，并生成 `server/downlink-formal-2a-summary.json`。`stop` 明确停止三端 unit，并记录 `MainPID=0`、cgroup 回收及无 `wfb_v6_uplink`/角色/UFTP 孤儿事实。确认该阶段 PASS 后才允许移动正式 work dir。

若要执行 restart 生命周期验收，先把三端正式 work dir 移到只读归档并准备新的短作业，再执行：

```bash
bash "$ORCH" restart /tmp/issue41.env
```

该检查执行 restart、确认 active/MainPID/cgroup，再 stop 并确认无孤儿。它验证现有 systemd 生命周期要求，不代表尚未实现的统一服务级协作终止契约。

## 5. 既有 uplink 基线

将先前按 v6 正式手册取得的真实三机 uplink run 完整目录放入：

```text
$ARCHIVE_ROOT/baseline_uplink/
```

至少保留规范的 `formal_2a_summary.json`、明确 PASS 的 `result.md`、原始日志、queue/reassembly 和 SHA 证据。已有 uplink 基线与本轮 Runtime 小模型闭环分开判定；不得要求小模型闭环重新制造长压中的 queue pause/resume，也不得用旧 uplink PASS 覆盖新 downlink 或 Runtime FAIL。

## 6. 离线汇总

最终目录为：

```text
$ARCHIVE_ROOT/
  baseline_uplink/
  server/
  client1/
  client2/
```

在 server 执行：

```bash
bash "$ORCH" validate /tmp/issue41.env
```

输出：

- `summary/baseline_uplink.json`
- `summary/downlink_feedback.json`
- `summary/runtime_loop.json`
- `summary/formal_summary.json`
- `summary/result.md`

校验器要求两 client x `model.bin`/`model.manifest.json` 的完整 UFTP matrix、正式 shared downlink 2A feedback open/close/hit、三端算法 JSON、同一 round、model/update manifest 与端到端 SHA、数值升序 `[1, 2]` 完整 mapping、systemd/cgroup 快照以及停止后无孤儿记录。缺一项即对应分区和总结果 FAIL。

只有三个分区均 PASS 才能回填 issue #41 通过。真实三机尚未运行前，本仓库中的代码、脚本和合成测试只表示“验收前置已准备”，不表示真机验收已经通过。
