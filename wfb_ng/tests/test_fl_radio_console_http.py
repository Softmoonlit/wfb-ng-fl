"""射频资源的同源 HTTP 契约。"""
from contextlib import contextmanager
import http.client
import json
import threading
from unittest.mock import patch

import pytest

from wfb_ng.fl.console_http import ConsoleHTTPServer, ConsoleRequestHandler
from wfb_ng.tests.test_fl_radio_console import CONFIG, prepared
from wfb_ng.tests import test_fl_radio_rest as radio_rest


@contextmanager
def radio_server(tmp_path):
    daemon = prepared(tmp_path)
    server = ConsoleHTTPServer(('127.0.0.1', 0), ConsoleRequestHandler)
    server.application = daemon.console
    worker = threading.Thread(target=server.serve_forever)
    worker.start()
    def request(method, path='/api/v1/radio/config', body=None, headers=None, raw=None):
        conn = http.client.HTTPConnection(*server.server_address, timeout=5)
        conn.request(method, path, body=raw if raw is not None else json.dumps(body) if body is not None else None,
                     headers=headers or {'Content-Type':'application/json'})
        response = conn.getresponse()
        data = response.read()
        result = response.status, (json.loads(data) if response.getheader('Content-Type').startswith('application/json')
                                   else data.decode())
        conn.close()
        return result
    try:
        yield daemon, request
    finally:
        server.shutdown()
        worker.join()
        server.server_close()
        daemon.stop()


def test_http_read_validate_and_confirm_configuration(tmp_path):
    with radio_server(tmp_path) as (daemon, request):
        status, current = request('GET')
        assert status == 200
        assert current['config']['channel'] == 157
        assert current['rate_bounds']['6']['default_rate_kbps'] == 38000
        status, checked = request('POST', '/api/v1/radio/config/validate', {'config':CONFIG})
        assert status == 200
        assert checked['confirmation']['instance_id'] == current['snapshot']['instance_id']
        peer = radio_rest.TestRadioReconfigureREST()
        peer.daemon = daemon
        with patch.object(daemon.control_plane, 'broadcast_downlink', side_effect=peer.protocol_peer):
            status, applied = request('POST', '/api/v1/radio/config/apply', {
                'confirmation_token':checked['confirmation']['token'], 'confirm_risk':False})
        assert status == 200 and applied['status'] == 'finalized'
        assert request('GET')[1]['config']['channel'] == 149
        assert '射频准备' in request('GET', '/radio')[1]
        assert request('GET', '/assets/radio.js')[0] == 200
        assert request('GET', '/assets/radio.css')[0] == 200
        assert 'href="/radio"' in request('GET', '/')[1]


@pytest.mark.parametrize('method,path,body,status,code', [
    ('POST','/api/v1/radio/config',{},405,'METHOD_NOT_ALLOWED'),
    ('GET','/api/v1/radio/config/validate',None,405,'METHOD_NOT_ALLOWED'),
    ('POST','/api/v1/radio/config/validate',{'config':dict(CONFIG, channel=161)},400,'INVALID_RADIO_CONFIG'),
    ('POST','/api/v1/radio/config/validate',{'config':{'channel':149}},400,'INVALID_RADIO_CONFIG'),
    ('POST','/api/v1/radio/config/apply',{'confirmed':True,'config':CONFIG},400,'INVALID_RADIO_REQUEST'),
    ('POST','/api/v1/radio/config/apply',{'confirmation_token':'forged','confirm_risk':True},409,'RADIO_CONFIRMATION_EXPIRED'),
])
def test_http_radio_errors_are_stable(tmp_path, method, path, body, status, code):
    with radio_server(tmp_path) as (_, request):
        result_status, result = request(method,path,body)
        assert result_status == status
        assert result['error']['code'] == code
        assert result['error']['request_id']


@pytest.mark.parametrize('raw,headers', [
    (b'{', {'Content-Type':'application/json'}),
    (b'{"config":{},"config":{}}', {'Content-Type':'application/json'}),
    (b'{}', {'Content-Type':'text/plain'}),
    (b'{}', {'Content-Type':'application/json', 'Transfer-Encoding':'chunked'}),
    (b'{}', {'Content-Type':'application/json', 'Content-Length':'99999'}),
])
def test_malformed_radio_http_body_is_rejected_before_domain_operation(tmp_path, raw, headers):
    with radio_server(tmp_path) as (_, request):
        status, result = request('POST', '/api/v1/radio/config/validate', raw=raw, headers=headers)
        assert status == 400
        assert result['error']['code'] == 'INVALID_REQUEST'
