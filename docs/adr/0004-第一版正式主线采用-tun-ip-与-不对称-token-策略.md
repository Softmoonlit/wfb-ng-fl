# ADR-0004: 第一版正式主线采用 TUN/IP 与不对称 Token 策略

Status: accepted

为在不偏离最终架构边界的前提下稳定落地第一版，第一版正式主线保留 `TUN/IP` 作为上层边界，基于 split-process 骨架复用现有 Token 能力，采用“`Server -> Client` 常开、`Client -> Server` Token 门控”的不对称策略，而不预先绑定到 `wfb_core` 这一具体实现。之所以这样做，是因为第一版需要先验证单客户端场景下可稳定复现的基础链路闭环：以持续循环、固定且偏宽的 Token 窗口优先保证稳定性，不做动态窗口调整，不引入用户态队列或动态逻辑水位，并以单客户端双向 `ping` 与 `client -> server` 的 `TCP` 小文件传输作为硬验收，`server -> client` 的 `TCP` 小文件传输作为补充增强；这条 ADR 作为对 ADR-0003 的第一版落地边界补充，与其并存，而 ADR-0002 继续仅作为历史背景保留。
