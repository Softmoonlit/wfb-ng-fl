"""模型库的同源 HTTP 契约。"""
from contextlib import contextmanager
import http.client
import json
import threading

import pytest

from wfb_ng.fl.console_http import ConsoleHTTPServer, ConsoleRequestHandler
from wfb_ng.tests.test_fl_console import make_daemon
from wfb_ng.tests.test_fl_model_library import ABC_SHA256


def multipart(content=b'abc', filename='model.bin', boundary='model-boundary'):
    return (f'--{boundary}\r\nContent-Disposition: form-data; name="file"; filename="{filename}"\r\n'
            'Content-Type: application/octet-stream\r\n\r\n').encode() + content + f'\r\n--{boundary}--\r\n'.encode()


@contextmanager
def model_server(tmp_path):
    daemon = make_daemon(model_library_dir=str(tmp_path))
    server = ConsoleHTTPServer(('127.0.0.1', 0), ConsoleRequestHandler)
    server.application = daemon.console
    worker = threading.Thread(target=server.serve_forever)
    worker.start()
    def request(method, path='/api/v1/models', body=None, headers=None):
        conn = http.client.HTTPConnection(*server.server_address, timeout=3)
        conn.request(method, path, body=body, headers=headers or {})
        response = conn.getresponse()
        result = response.status, json.loads(response.read())
        conn.close()
        return result
    try:
        yield daemon, server, request
    finally:
        server.shutdown()
        worker.join()
        server.server_close()


def test_http_upload_dedupe_list_reference_protection_and_delete(tmp_path):
    with model_server(tmp_path) as (daemon, server, request):
        headers = {'Content-Type':'multipart/form-data; boundary=model-boundary'}
        status, result = request('POST', body=multipart(), headers=headers)
        assert status == 201
        assert result['model']['sha256'] == ABC_SHA256
        assert result['deduplicated'] is False
        assert request('POST', body=multipart(), headers=headers) == (200, dict(result, deduplicated=True))
        assert request('GET') == (200, {'models':[result['model']]})
        daemon.active_job = {'job_id':'job', 'model_sha256':ABC_SHA256}
        assert request('GET')[1]['models'][0]['referenced'] is True
        assert request('DELETE', '/api/v1/models/' + ABC_SHA256)[1]['error']['code'] == 'MODEL_REFERENCED'
        assert daemon.console.models.artifact_path(ABC_SHA256).read_bytes() == b'abc'
        daemon.active_job = None
        assert request('DELETE', '/api/v1/models/' + ABC_SHA256) == (200, {'deleted':True})
        assert request('GET') == (200, {'models':[]})


@pytest.mark.parametrize('body,headers,code', [
    (multipart(b''), {'Content-Type':'multipart/form-data; boundary=model-boundary'}, 'MODEL_EMPTY'),
    (multipart()[:-1] + b'x', {'Content-Type':'multipart/form-data; boundary=model-boundary'}, 'INVALID_MULTIPART'),
    (multipart(), {'Content-Type':'application/json'}, 'INVALID_MULTIPART'),
    (multipart(), {'Content-Type':'multipart/form-data'}, 'INVALID_MULTIPART'),
    (multipart().replace(b'name="file"', b'name="other"'),
     {'Content-Type':'multipart/form-data; boundary=model-boundary'}, 'INVALID_MULTIPART'),
    (multipart().replace(b'\r\n--model-boundary--\r\n',
                        b'\r\n--model-boundary\r\nContent-Disposition: form-data; name="extra"\r\n\r\nx\r\n--model-boundary--\r\n'),
     {'Content-Type':'multipart/form-data; boundary=model-boundary'}, 'INVALID_MULTIPART'),
    (multipart(), {'Content-Type':'multipart/form-data; boundary=model-boundary', 'Content-Length':'-1'}, 'INVALID_REQUEST'),
    (multipart(), {'Content-Type':'multipart/form-data; boundary=model-boundary', 'Content-Length':str(1024**3 + 16385)}, 'MODEL_TOO_LARGE'),
    (multipart(), {'Content-Type':'multipart/form-data; boundary=model-boundary', 'Transfer-Encoding':'chunked'}, 'INVALID_REQUEST'),
])
def test_invalid_upload_is_visible_and_leaves_no_artifact(tmp_path, body, headers, code):
    with model_server(tmp_path) as (_, server, request):
        status, result = request('POST', body=body, headers=headers)
        assert status >= 400
        assert result['error']['code'] == code
        assert result['error']['request_id']
        assert request('GET') == (200, {'models':[]})
        assert list(tmp_path.iterdir()) == []
        assert request('POST', body=multipart(), headers={
            'Content-Type':'multipart/form-data; boundary=model-boundary'})[0] == 201


def test_http_capacity_duplicate_and_methods(tmp_path):
    with model_server(tmp_path) as (daemon, server, request):
        daemon.console.models.MAX_LIBRARY_BYTES = 3
        headers = {'Content-Type':'multipart/form-data; boundary=model-boundary'}
        assert request('POST', body=multipart(), headers=headers)[0] == 201
        assert request('POST', body=multipart(), headers=headers)[0] == 200
        status, body = request('POST', body=multipart(b'x'), headers=headers)
        assert status == 507 and body['error']['code'] == 'MODEL_CAPACITY_EXCEEDED'
        assert request('PUT')[0] == 405
        assert request('GET', '/api/v1/models/' + ABC_SHA256)[0] == 405
        assert request('DELETE', '/api/v1/models/not-a-digest')[1]['error']['code'] == 'INVALID_MODEL_SHA256'
        assert request('DELETE', '/api/v1/models/' + '0' * 64)[0] == 404
        daemon._recent_job = {'model_sha256':ABC_SHA256, 'recovery_state':'blocked'}
        assert request('DELETE', '/api/v1/models/' + ABC_SHA256)[0] == 409
        daemon._recent_job['recovery_state'] = 'ready'
        assert request('DELETE', '/api/v1/models/' + ABC_SHA256)[0] == 200


def test_disconnected_upload_and_duplicate_lengths_leave_capacity_available(tmp_path):
    import socket
    with model_server(tmp_path) as (daemon, server, request):
        daemon.console.models.MAX_LIBRARY_BYTES = 3
        for lengths, body in [('Content-Length: 154\r\nContent-Length: 154\r\n', multipart()),
                              (f'Content-Length: {len(multipart())}\r\n', multipart()[:-25])]:
            with socket.create_connection(server.server_address, timeout=3) as client:
                client.sendall(('POST /api/v1/models HTTP/1.1\r\nHost: localhost\r\n'
                                'Content-Type: multipart/form-data; boundary=model-boundary\r\n' +
                                lengths + '\r\n').encode() + body)
                client.shutdown(socket.SHUT_WR)
                response = client.makefile('rb')
                assert b'400' in response.readline()
                response.read()
                response.close()
            assert request('GET') == (200, {'models':[]})
            assert list(tmp_path.iterdir()) == []
        assert request('POST', body=multipart(), headers={
            'Content-Type':'multipart/form-data; boundary=model-boundary'})[0] == 201


def test_native_formdata_upload_uses_real_http_and_preserves_unicode_name(tmp_path):
    import shutil
    import subprocess
    script = """
    (async () => {
      const form = new FormData();
      form.append('file', new Blob(['abc']), '模型 文件.bin');
      const response = await fetch(process.argv[1] + '/api/v1/models', {method:'POST', body:form});
      console.log(JSON.stringify({status:response.status, body:await response.json()}));
    })().catch(error => {console.error(error); process.exitCode=1;});
    """
    with model_server(tmp_path) as (_, server, request):
        host, port = server.server_address
        result = subprocess.run([shutil.which('node'), '-e', script, f'http://{host}:{port}'],
                                text=True, capture_output=True, timeout=10)
        assert result.returncode == 0, result.stderr
        response = json.loads(result.stdout)
        assert response['status'] == 201
        assert response['body']['model']['filename'] == '模型 文件.bin'
        assert response['body']['model']['sha256'] == ABC_SHA256
        assert request('GET')[1]['models'] == [response['body']['model']]


def test_http_storage_failure_and_static_model_page(tmp_path, monkeypatch):
    from pathlib import Path
    with model_server(tmp_path) as (_, server, request):
        original = Path.open
        def fail_content(path, *args, **kwargs):
            if path.name == 'content':
                raise OSError('/secret/server/path')
            return original(path, *args, **kwargs)
        with monkeypatch.context() as patcher:
            patcher.setattr(Path, 'open', fail_content)
            status, result = request('POST', body=multipart(), headers={
                'Content-Type':'multipart/form-data; boundary=model-boundary'})
        assert status == 503
        assert result['error']['code'] == 'MODEL_STORAGE_FAILED'
        assert '/secret/server/path' not in json.dumps(result)
        assert list(tmp_path.iterdir()) == []
        for path in ('/models', '/assets/models.js', '/assets/models.css'):
            conn = http.client.HTTPConnection(*server.server_address)
            conn.request('GET', path)
            response = conn.getresponse()
            assert response.status == 200 and response.read()
            conn.close()
