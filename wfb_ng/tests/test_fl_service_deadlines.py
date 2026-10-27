"""通过 RoleService 的 JSON 加载入口验证 I/O 预算 schema。"""
import json

import pytest

from wfb_ng.fl.errors import FLRuntimeError
from wfb_ng.fl.service import load_role_service


@pytest.fixture(params=['server', 'client'])
def role_config_path(tmp_path, request, monkeypatch):
    # 复用 test_fl_job_contract_stage3 的完整 server_cfg 形状，
    # 添加公开加载入口所需的 known-clients；client 只替换角色专属字段。
    config = {
        'schema_version': 1,
        'role': 'server',
        'node_id': 100,
        'work_dir': str(tmp_path / 'role'),
        'channel': 157,
        'channel_width': 'HT40+',
        'link_args': ['--tun-name', 'fl-deadlines', '--known-clients', '1,2'],
        'participant_node_ids': [1, 2],
        'participant_uftp_uids': [1, 2],
        'server_uftp_uid': 100,
        'uftp_port': 1044,
        'http_host': '10.80.0.1',
        'http_port': 8080,
        'uftp_bind_host': '10.80.0.1',
        'uftp_multicast_host': '239.80.41.1',
        'uftp_private_multicast_host': '239.80.41.2',
        'uftp_rate_kbps': 12000,
        'max_update_size_bytes': 1024,
        'live_observation': True,
        'observation_path': str(tmp_path / 'observation.jsonl'),
        'io_timeout_seconds': 120,
    }
    if request.param == 'client':
        for field in ('participant_node_ids', 'participant_uftp_uids', 'server_uftp_uid',
                      'uftp_rate_kbps', 'http_host', 'http_port'):
            del config[field]
        config.update(role='client', node_id=1, uftp_uid=1,
                      server_http_host='10.80.0.1', server_http_port=8080)
    path = tmp_path / 'role.json'
    path.write_text(json.dumps(config), encoding='utf-8')
    # 仅加载 schema 和真实角色对象，不启动链路或 UFTP；替代外部程序探测。
    monkeypatch.setattr('wfb_ng.fl.service.shutil.which', lambda name: '/usr/bin/' + name)
    return path


@pytest.mark.parametrize('budget', [0.2, 120, 120.5])
def test_role_service_accepts_and_normalizes_positive_finite_io_budget(role_config_path, budget):
    config = json.loads(role_config_path.read_text(encoding='utf-8'))
    config['io_timeout_seconds'] = budget
    role_config_path.write_text(json.dumps(config), encoding='utf-8')
    service = load_role_service(role_config_path)
    try:
        assert service.role.transport.io_timeout == budget
        assert type(service.role.transport.io_timeout) is float
    finally:
        service.close()


@pytest.mark.parametrize('budget', [
    True, False, float('nan'), float('inf'), -float('inf'),
    0, 0.0, -0.0, -0.2, -1, '0.2', None,
    pytest.param(10 ** 400, id='float-overflow'),
])
def test_role_service_rejects_invalid_io_budget_from_json(role_config_path, budget):
    config = json.loads(role_config_path.read_text(encoding='utf-8'))
    config['io_timeout_seconds'] = budget
    # json.load 默认会接受 NaN/Infinity，必须由角色 schema 显式拒绝。
    role_config_path.write_text(json.dumps(config), encoding='utf-8')
    with pytest.raises(FLRuntimeError) as error:
        load_role_service(role_config_path)
    assert error.value.error_code == 'invalid_configuration'


@pytest.mark.parametrize('role_config_path', ['client'], indirect=True)
@pytest.mark.parametrize('host', ['server.local', 'localhost', '::1', '', '127.0.0.999', 2130706433, None])
def test_client_role_schema_rejects_non_numeric_ipv4(role_config_path, host):
    config = json.loads(role_config_path.read_text(encoding='utf-8'))
    config['server_http_host'] = host
    role_config_path.write_text(json.dumps(config), encoding='utf-8')
    with pytest.raises(FLRuntimeError) as error:
        load_role_service(role_config_path)
    assert error.value.error_code == 'invalid_configuration'
