
## control 三修后的最终客户端复验

错 job wire PREPARE 现按协议返回拒绝 ACK；测试不再期待无输出，同时保持物理修改次数为零、配置和 pending 状态不变，后续 COMMIT 也不得生效。

- `focused-18.txt`: 10 客户端 jobradio + radio application，**34 passed**（其中 jobradio 正好 18 tests）。
- `client-guardchecks.txt`: 当前客户端相关 guardchecks **167 passed**。
- `typecheck.txt`: client_daemon mypy 通过。
- 限定 client 文件 git diff check 通过。

本轮只修改测试断言与证据；无 core 改动、无硬件、无部署、未 commit。

## 历史失败日志补归档

`red-operator-channel-rollback.txt` 是工作区遗留的原始 RED：操作者信道预期153、回退实际157，断言失败。此次仅保留该失败证据，不重跑测试，不将其计作当前客户端验证结果，也不据此宣称回退问题已经解决。上述历史通过记录不覆盖这一独立失败样本。
