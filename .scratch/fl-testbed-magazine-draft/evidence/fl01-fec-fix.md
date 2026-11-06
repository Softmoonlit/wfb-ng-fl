# fl01 上行 FEC 截止修复

## 根因

客户端 `run_client` 在存在 pending TUN 数据、授权已失效且 FEC deadline 已到期时，跳过 FEC 封口逻辑并继续零超时轮询。该路径造成 `CLIENT_LOOP` 忙循环，放大授权恢复、FEC重组和心跳延迟。未授权期间不能直接封口，因为 FEC parity 发送也必须受 grant 约束。

## 修复

`src/v6_uplink.cpp`：

- 未授权时不再把 FEC deadline 作为 `poll` 的唤醒源。
- 无 pending 时仅在授权有效时封口到期 block。
- 授权有效且即将发送 pending payload 时，先封口已到期旧 block，再发送新 payload。
- 未授权路径不发送 DATA 或 FEC parity。

## TDD证据

旧实现已用真实 `run_client`、UDP 空口 socket 和 TUN socketpair 复现：忙循环断言失败；旧 block 封口后再发送 pending 的顺序断言失败。当前实现使用 `fec_k=2/n=3`、100ms首 grant、显式等待授权过期和3000ms新 grant，验证未授权窗口无输出、旧 block 合法封口及 pending 在新 block中发送。

最终命令：

```text
make -j2 v6_radio_update_test
./v6_radio_update_test
```

结果：

```text
native run_client unauthorized pending FEC deadline/order: PASS
v6 radio command validation and control/data radiotap sync: PASS
```

`git diff --check` 通过。尚未部署实机，尚未完成九客户端10分钟待命和两次通信闭环；部署必须使用明确提交的源码。
