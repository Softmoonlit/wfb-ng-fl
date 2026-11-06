"""离线重放重启前后序列，验证新会话入网及旧报文隔离，不创建socket。"""
from dataclasses import replace

from wfb_ng.fl.control import ClientNodeState, NodeHeartbeat, NodeHorizonRegistry, NodeReadiness
from wfb_ng.fl.radio import RadioConfig

registry = NodeHorizonRegistry()
radio = RadioConfig()
address = ("127.0.0.1", 12345)


def admit(boot_id, timestamp):
    request = NodeHeartbeat(
        node_id=1, state=ClientNodeState.HUNTING.value, elapsed_ms=0,
        current_channel=radio.channel, txpower_dbm=radio.radio_txpower_dbm,
        uplink_mcs=radio.uplink_mcs, timestamp_ms=timestamp, boot_id=boot_id)
    challenge, _ = registry.update_heartbeat(request, address, radio)
    assert not challenge.confirmed
    confirmation = replace(request, session=challenge.session)
    accepted, _ = registry.update_heartbeat(confirmation, address, radio)
    assert accepted.confirmed
    assert registry.get_node(1).readiness == NodeReadiness.CONNECTING
    idle = replace(confirmation, state=ClientNodeState.IDLE.value, timestamp_ms=timestamp + 1)
    registry.update_heartbeat(idle, address, radio)
    assert registry.get_node(1).readiness == NodeReadiness.READY
    print("启动", boot_id, "当前请求", registry.get_node(1).timestamp_ms, "READY")
    return idle


old = admit("before-reboot", 9038344)
new = admit("after-reboot", 62406)
assert registry.get_node(1).timestamp_ms == 62407
old_reply, _ = registry.update_heartbeat(replace(old, timestamp_ms=99999999), address, radio)
assert not old_reply.confirmed
assert registry.get_node(1).timestamp_ms == 62407
print("PASS：重启后新序列生效，旧启动的大序列不能覆盖新状态")
