"""Issue 04 synchronous file simulation at the Web seam."""
import http.client
import json
import threading

import pytest

from wfb_ng.fl.console_http import ConsoleHTTPServer, ConsoleRequestHandler
from wfb_ng.tests.test_fl_console import make_daemon, register_idle


def _server(daemon):
    server = ConsoleHTTPServer(('127.0.0.1', 0), ConsoleRequestHandler)
    server.application = daemon.console
    worker = threading.Thread(target=server.serve_forever)
    worker.start()
    return server, worker


def _request(server, method, path, body=None, headers=None):
    conn = http.client.HTTPConnection(*server.server_address, timeout=3)
    payload = None if body is None else json.dumps(body).encode()
    conn.request(method, path, body=payload, headers=headers or {})
    response = conn.getresponse()
    result = json.loads(response.read())
    conn.close()
    return response.status, result


def test_web_file_job_accepts_digest_nodes_rounds_and_is_idempotent(tmp_path):
    daemon = make_daemon(model_library_dir=str(tmp_path / 'models'))
    register_idle(daemon)
    uploaded = daemon.console.models.upload(_Bytes(b'model'), 5, 'model.bin')
    digest = uploaded['model']['sha256']
    daemon.console.start_job = lambda payload, idempotency_key: {
        'status': 'accepted', 'job_id': 'job-1', 'run_id': 'run-1',
        'model_sha256': payload['model_sha256'], 'rounds': payload['rounds'],
        'target_nodes': payload['target_nodes'],
    }
    server, worker = _server(daemon)
    try:
        payload = {'model_sha256': digest, 'target_nodes': [1], 'rounds': 2}
        headers = {'Content-Type': 'application/json', 'Idempotency-Key': 'same-request'}
        assert _request(server, 'POST', '/api/v1/jobs', payload, headers)[0] == 202
        assert _request(server, 'POST', '/api/v1/jobs', payload, headers) == (202, {
            'status': 'accepted', 'job_id': 'job-1', 'run_id': 'run-1',
            'model_sha256': digest, 'rounds': 2, 'target_nodes': [1],
        })
    finally:
        server.shutdown(); worker.join(); server.server_close()


@pytest.mark.parametrize('payload', [
    {'model_path': '/tmp/model', 'target_nodes': [1], 'rounds': 1},
    {'model_sha256': 'A' * 64, 'target_nodes': [1], 'rounds': 1},
    {'model_sha256': '0' * 64, 'target_nodes': [1, 1], 'rounds': 1},
    {'model_sha256': '0' * 64, 'target_nodes': [1], 'rounds': 0},
    {'model_sha256': '0' * 64, 'target_nodes': [1], 'rounds': 1, 'mode': 'async'},
])
def test_web_file_job_rejects_legacy_and_invalid_shapes(tmp_path, payload):
    daemon = make_daemon(model_library_dir=str(tmp_path / 'models'))
    server, worker = _server(daemon)
    try:
        status, result = _request(server, 'POST', '/api/v1/jobs', payload,
                                  {'Content-Type': 'application/json', 'Idempotency-Key': 'x'})
        assert status == 400
        assert result['error']['code'] in {'INVALID_JOB_REQUEST', 'MODEL_NOT_FOUND'}
    finally:
        server.shutdown(); worker.join(); server.server_close()


class _Bytes:
    def __init__(self, data): self.data = data
    def read(self, size=-1):
        if not self.data: return b''
        result, self.data = self.data[:size], self.data[size:]
        return result
