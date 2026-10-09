Status: ready-for-human
Type: grilling
Blocked by:

# 定义无外网直连管理网部署契约

## Question

Stage 2 当前 REST IPC 默认监听 `127.0.0.1:9090`，而 Stage 4 浏览器位于用户电脑的直连管理网。如何在不改变 WFB 控制面绑定和 UFTP/HTTP 数据面端口的前提下，定义 Web 管理 HTTP 监听地址、端口、启动失败行为、浏览器访问 URL、静态资源同源规则和部署配置；daemon 如何继续避免绑定所有接口？

## Decision

- Stage 4 不另建 Web 后端服务。`wfb-fl-server-daemon` 同时保留 `127.0.0.1:9090` 本机 IPC，并增加绑定明确管理网 IP 的 Web HTTP listener；两个 listener 共享 REST handler、状态快照和领域逻辑，禁止绑定 `0.0.0.0`。管理网地址暂不可用时，管理 listener 只进入不可用/降级状态，不阻止 Server Daemon 核心运行时或 Client Daemon 开机自启；网络地址就绪后由服务生命周期恢复管理 listener。
- 浏览器只通过 Server Daemon 操作系统：射频配置、作业启动、状态观察和急停均由 Server Daemon 经现有控制面协调 Client Daemon 完成。Web 不直接连接 Client，不通过 SSH，不直接操作 WFB、TUN 或数据面。
- Client 的物理网卡选择、节点身份、TUN/IP、驱动、USB/xHCI、软件包、systemd 和私有数据集/凭据属于部署维护面；Web 只展示就绪状态和错误原因。首次部署可以通过 SSH 完成安装和配置；部署完成后 Server/Client daemon 由 systemd 开机自启，正常 FL 作业不使用 SSH。