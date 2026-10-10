# Issue08 射频实机补充执行

本入口只补射频证据，不重新声明完整 Stage4 正常/急停验收。既有 `tests/logs/stage4-hardware-20261010T035439Z-40dd3c5d` 不作修改。

父代理先将执行器及测试纳入三端同一提交，使用 `git fetch` 和 `git merge --ff-only` 同步，确认工作树干净、Server/Client daemon 均 READY。基线必须为157、12 dBm、下行MCS3、上行MCS6、15000 kbps，模型库已有canonical 40 MiB文件。执行器从Server仓库运行；默认使用canonical模型SHA，无须重复上传。

```bash
python -m tests.fl_runtime.stage4_radio_acceptance --execute \
  --web-url http://<Server管理网IP>:8080 \
  --client 1=vm1 --client 2=vm2 --include-rollback
```

`--execute` 明确授权执行下列请求，不等待人工输入。每次射频变更使用管理Web真实 `/api/v1/radio/config/validate` 和 `/api/v1/radio/config/apply` 令牌机制，强告警配置拒绝执行。

1. 基线采集 → 157/11 dBm/down4/up5/18000，证明上下行MCS重建。
2. 165 HT20 → 157 HT40+，证明两次频宽重建。
3. `--include-rollback` 复用Stage3原有fl-c2 iptables规则，149切换在COMMIT失败、回退157。以前配置为底保留11 dBm/down4/up5/18000；遍历全部目标Client保存真实session-bound租约耗尽节点与原日志，最后删除规则并检查。
4. 经Web发两轮同步作业，Server本地采样真实job-bound UFTP `/proc` argv/cwd/start_ticks，恢复后收集原始UFTP日志，证明`-R 18000`。
5. 经Web恢复157/12/down3/up6/15000，复验READY和三端readback。

各步骤记录daemon PID、链路PID/start_ticks/argv、TUN ifindex、动态wl*网卡及驱动/USB/xHCI、iw readback、ss控制socket和Web READY。Server控制监听10.80.0.1:9001，Client监听0.0.0.0:9000；后者沿既有Stage3/4契约。作业窗口禁止Client SSH，仅允许Server本地进程取证和管理Web。

新归档位于`tests/logs/stage4-radio-*`。独立离线重判读取原始已保存文件：

```bash
python -m tests.fl_runtime.stage4_radio_acceptance --recheck tests/logs/stage4-radio-<运行标识>
```

离线重判核验READY/射频、实际argv/readback/socket、PID和TUN重建、事务结果、回退与租约原日志、真实作业终态和UFTP `-R`/对应原日志，并检查seal文件哈希。它仅判断本补充范围，不替代既有完整数据面归档校验器。

失败保留归档并退出1；不自动重跑、自动发新配置或通过SSH恢复资源。若`job_window_open=true`，父代理用管理Web查询并急停已接受作业；可用归档`data-plane/job-request.json`原幂等键解析超时导致的受理不确定性。故障规则正常异常路径均在finally删除；强制杀进程后须按归档fault.json的run_id复用Stage3 remove/observe检查清理。错误边界后的恢复由父代理依据真实状态执行。
