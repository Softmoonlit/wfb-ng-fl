Status: resolved
Type: grilling
Blocked by:

# 定义无外网直连管理网部署契约

## Question

Stage 2 当前 REST IPC 默认监听 `127.0.0.1:9090`，而 Stage 4 浏览器位于用户电脑的直连管理网。如何在不改变 WFB 控制面绑定和 UFTP/HTTP 数据面端口的前提下，定义 Web 管理 HTTP 监听地址、端口、启动失败行为、浏览器访问 URL、静态资源同源规则和部署配置；daemon 如何继续避免绑定所有接口？

## Decision

- Stage 4 不另建 Web 后端服务。`wfb-fl-server-daemon` 同时保留 `127.0.0.1:9090` 本机 IPC，并增加绑定明确管理网 IP 的 Web HTTP listener；两个 listener 共享 REST handler、状态快照和领域逻辑，禁止绑定 `0.0.0.0`。管理网地址暂不可用时，管理 listener 只进入不可用/降级状态，不阻止 Server Daemon 核心运行时或 Client Daemon 开机自启；网络地址就绪后由服务生命周期恢复管理 listener。
- 浏览器只通过 Server Daemon 操作系统：射频配置、作业启动、状态观察和急停均由 Server Daemon 经现有控制面协调 Client Daemon 完成。Web 不直接连接 Client，不通过 SSH，不直接操作 WFB、TUN 或数据面。
- Client 的物理网卡选择、节点身份、TUN/IP、驱动、USB/xHCI、软件包、systemd 和私有数据集/凭据属于部署维护面；Web 只展示就绪状态和错误原因。首次部署可以通过 SSH 完成安装和配置；部署完成后 Server/Client daemon 由 systemd 开机自启，正常 FL 作业不使用 SSH。

## Answer

用户确认采用以下完整管理网契约：

- Server Daemon 保留本机 IPC `127.0.0.1:9090`，并新增独立 Web listener 配置。`/etc/wfb-ng-fl/server.json` 使用 `web_host` 指定明确的管理网 IPv4 地址，使用固定 `web_port: 8080`；`web_host` 不自动推断，不绑定 `0.0.0.0`，不绑定 TUN 地址 `10.80.0.1`，也不绑定无线数据接口。systemd 只启动 daemon，不在 unit 中重复维护 Web 地址。
- 管理网地址暂不可用或 listener 绑定失败时，Server Daemon 核心、控制面、TUN 和 Client Daemon 继续运行。Web listener 进入 `management_web_unavailable` 状态，记录明确错误原因，并通过本机 IPC 状态快照暴露；daemon 不因该状态退出，也不使用 `0.0.0.0` 或回环地址作为 Web 降级监听。网络地址就绪后由 daemon 的服务生命周期重新尝试绑定并恢复 Web listener；失败时继续保持降级状态。systemd 的 `Restart` 只处理 daemon 进程故障，不因管理网暂不可用制造重启风暴。
- 浏览器访问 URL 固定为 `http://<web_host>:8080/`。静态 HTML/CSS/JavaScript、REST API 和 SSE 均由同一个 `wfb-fl-server-daemon` listener 提供；前端使用相对路径访问 `/api/v1/...`，不启用 CORS，也不允许配置其他 API 主机或端口。
- Web 只访问 Server Daemon；Client、SSH、WFB 控制面、TUN、UFTP 和 HTTP PUT 数据面不暴露给浏览器。首次安装和写入 `/etc/wfb-ng-fl/server.json` 可通过 SSH 完成，`web_host` 由部署者根据直连管理网中的 Server 地址显式填写。Client 物理网卡、`node.json`、TUN/IP、驱动、USB/xHCI、systemd 和私有数据仍属于部署维护面，Web 只展示就绪状态和错误原因，不提供修改入口。首版不增加应用鉴权、HTTPS 或额外来源防火墙。