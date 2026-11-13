# 存储证据索引修复后的独立续跑

本目录独立承接 1833 root 的零执行存储停止事实。1833 root、STOP、fl02 空间不足、B2 保留和所有旧失败记录不覆盖。

本次仅修复本地 `storage.py` 的授权证据索引遗漏：bases 在原有 `fec14-remaining`、`fec12`、`fec14-matched-completion`、新 root 基础上加入已明确授权的旧 `selected-validation`。硬门槛仍为每节点 512MiB，执行目标仍为 1GiB；只允许 fl01/fl02，逐文件依据本地完整成功轮证据做远端 exists、归档日志、re-stat/hash 和 exact unlink。B2 `comm-rsmc20261109T060537Z-g30-d5-model`、旧 trial2 失败轮、partial、日志、source、evidence、真实训练目录均保留。

本地扫描确认旧 `selected-validation/trial1` 三 flags 全真、九份独立 size/SHA 和 exact-uftp 证据存在；trial2 三 flags 不全真并排除。远端删除前仍需新 root worker 逐文件复核，索引本身不代表远端文件存在。

存储准入通过后，worker 重新核验当前十端 actual/九 READY/旧 unit 与路由；当前已知服务基础为 FEC12、30/5、149，无需再次标准重启。随后才允许两次新的模型闭环，不继承旧 trial1 的连续计数。
