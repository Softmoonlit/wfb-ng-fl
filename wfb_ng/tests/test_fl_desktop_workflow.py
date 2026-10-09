"""Issue 06 desktop console workflow automated tests.

Validates the full operator journey:
- Model library upload, deduplication, full SHA-256 digest, selection
- Job creation, fixed rounds, target nodes, running state observation
- No fake progress/telemetry, authoritative gating
- Refresh and SSE reconnection
- Radio preparation bounds, strong warnings and risk confirmation
- Emergency stop (ABORT) and resource recovery barrier before restart
"""
import http.client
import json
import shutil
import subprocess
import threading
from html.parser import HTMLParser
from pathlib import Path

from wfb_ng.fl.console import ConsoleApplicationService
from wfb_ng.fl.console_http import ConsoleHTTPServer, ConsoleRequestHandler
from wfb_ng.fl.server_daemon import ServerState
from wfb_ng.tests.test_fl_console import make_daemon, register_idle


STATIC = Path(__file__).resolve().parents[1] / 'fl' / 'static'


def test_desktop_workflow_page_behavior_with_node():
    node = shutil.which('node')
    assert node, 'Existing Node runtime required for desktop workflow page tests'
    result = subprocess.run(
        [
            node,
            str(Path(__file__).with_name('desktop_workflow_page_test.js')),
            str(STATIC / 'console.js'),
            str(STATIC / 'models.js'),
            str(STATIC / 'radio.js'),
        ],
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert result.returncode == 0, f"STDOUT:\n{result.stdout}\nSTDERR:\n{result.stderr}"
    assert 'All desktop console workflow automated tests passed successfully!' in result.stdout


def test_desktop_html_conforms_to_1280x720_and_zero_online_assets():
    class PageParser(HTMLParser):
        def __init__(self):
            super().__init__()
            self.tags = []

        def handle_starttag(self, tag, attrs):
            self.tags.append((tag, dict(attrs)))

    html_files = ['index.html', 'models.html', 'radio.html']
    for filename in html_files:
        path = STATIC / filename
        assert path.is_file(), f"Missing static page: {filename}"
        content = path.read_text(encoding='utf-8')

        parser = PageParser()
        parser.feed(content)

        by_id = {attrs['id']: (tag, attrs) for tag, attrs in parser.tags if 'id' in attrs}

        # 1. Viewport meta tag present for responsive baseline (>=1280x720)
        viewport_tags = [attrs for tag, attrs in parser.tags if tag == 'meta' and attrs.get('name') == 'viewport']
        assert viewport_tags, f"{filename} must have viewport meta"

        # 2. Command Bar navigation links present
        nav_links = [attrs.get('href') for tag, attrs in parser.tags if tag == 'a' and 'href' in attrs]
        assert '/' in nav_links, f"{filename} must link to monitor page"
        assert '/models' in nav_links, f"{filename} must link to models page"
        assert '/radio' in nav_links, f"{filename} must link to radio page"

        # 3. Persistent Emergency Stop / Abort button present on all pages
        assert 'abort-job' in by_id or 'emergency-stop' in by_id, f"{filename} must have persistent abort button"

        # 4. Zero online / external CDN assets: all scripts and links must be local /assets/
        for tag, attrs in parser.tags:
            if tag in ('script', 'link'):
                target = attrs.get('src') or attrs.get('href')
                if target and not target.startswith('#'):
                    assert target.startswith('/assets/'), f"Asset {target} in {filename} must be local /assets/"
                    asset_file = STATIC / target.removeprefix('/assets/')
                    assert asset_file.is_file(), f"Asset {asset_file} must exist locally"

        # 5. Strict prohibition of fake progress or training/aggregation claims
        assert 'fake-progress' not in content
        assert '本地训练中' not in content
        assert '模型聚合中' not in content


def test_e2e_desktop_console_api_flow(tmp_path):
    """End-to-end multi-page workflow through ConsoleHTTPServer."""
    daemon = make_daemon()
    daemon.console.models.root = tmp_path / 'models'
    daemon.console.models.root.mkdir(parents=True, exist_ok=True)

    server = ConsoleHTTPServer(('127.0.0.1', 0), ConsoleRequestHandler)
    server.application = daemon.console
    worker = threading.Thread(target=server.serve_forever)
    worker.start()

    host, port = server.server_address
    try:
        # 1. Upload model via multipart POST /api/v1/models
        boundary = '----BoundaryTest123456789'
        payload = b'Deterministic model payload for issue 06 acceptance testing'
        body = (
            f'--{boundary}\r\n'
            f'Content-Disposition: form-data; name="file"; filename="test_model.bin"\r\n'
            f'Content-Type: application/octet-stream\r\n\r\n'
        ).encode('utf-8') + payload + f'\r\n--{boundary}--\r\n'.encode('utf-8')

        conn = http.client.HTTPConnection(host, port, timeout=5)
        conn.request('POST', '/api/v1/models', body=body, headers={
            'Content-Type': f'multipart/form-data; boundary={boundary}',
            'Content-Length': str(len(body))
        })
        resp = conn.getresponse()
        assert resp.status == 201
        res_data = json.loads(resp.read())
        model_sha = res_data['model']['sha256']
        assert len(model_sha) == 64
        conn.close()

        # 2. Upload duplicate model: verify deduplication
        conn = http.client.HTTPConnection(host, port, timeout=5)
        conn.request('POST', '/api/v1/models', body=body, headers={
            'Content-Type': f'multipart/form-data; boundary={boundary}',
            'Content-Length': str(len(body))
        })
        resp = conn.getresponse()
        assert resp.status == 200
        res_dedup = json.loads(resp.read())
        assert res_dedup['deduplicated'] is True
        assert res_dedup['model']['sha256'] == model_sha
        conn.close()

        # 3. Read models list
        conn = http.client.HTTPConnection(host, port, timeout=5)
        conn.request('GET', '/api/v1/models')
        resp = conn.getresponse()
        assert resp.status == 200
        models_list = json.loads(resp.read())['models']
        assert len(models_list) == 1
        assert models_list[0]['sha256'] == model_sha
        assert models_list[0]['referenced'] is False
        conn.close()

        # 4. Verify initial state snapshot
        daemon.server_state = ServerState.IDLE
        register_idle(daemon)
        conn = http.client.HTTPConnection(host, port, timeout=5)
        conn.request('GET', '/api/v1/state')
        resp = conn.getresponse()
        assert resp.status == 200
        snap = json.loads(resp.read())
        assert snap['server']['state'] == 'idle'
        assert snap['server']['can_start_job'] is True
        assert snap['job'] is None
        conn.close()

        # 5. Start job via POST /api/v1/jobs
        called_start = []
        daemon.console._start_job = lambda p, idemp: called_start.append((p, idemp)) or {
            'job_id': 'job-06-test', 'status': 'accepted', 'rounds': p['rounds'], 'target_nodes': p['target_nodes']
        }
        job_req = json.dumps({'model_sha256': model_sha, 'target_nodes': [1, 2], 'rounds': 3}).encode()
        conn = http.client.HTTPConnection(host, port, timeout=5)
        conn.request('POST', '/api/v1/jobs', body=job_req, headers={
            'Content-Type': 'application/json',
            'Content-Length': str(len(job_req)),
            'Idempotency-Key': 'key-issue-06-start-1'
        })
        resp = conn.getresponse()
        assert resp.status == 202
        start_result = json.loads(resp.read())
        assert start_result['job_id'] == 'job-06-test'
        assert len(called_start) == 1
        conn.close()

        # Simulate job running in daemon
        daemon.server_state = ServerState.RUNNING
        daemon.active_job = {
            'job_id': 'job-06-test',
            'run_id': 'run-06',
            'model_sha256': model_sha,
            'target_nodes': [1, 2],
            'rounds': 3,
            'current_round': 1,
            'rounds_completed': 0,
            'server_phase': 'preparing',
            'started_at': 1000.0,
            'round_started_at': 1000.0,
            'phase_started_at': 1000.0,
        }

        # 6. Check state while job is running
        conn = http.client.HTTPConnection(host, port, timeout=5)
        conn.request('GET', '/api/v1/state')
        resp = conn.getresponse()
        snap_running = json.loads(resp.read())
        assert snap_running['server']['state'] == 'running'
        assert snap_running['server']['can_start_job'] is False
        assert any(b['code'] == 'JOB_RUNNING' for b in snap_running['server']['start_blockers'])
        assert snap_running['job']['job_id'] == 'job-06-test'
        assert snap_running['job']['execution_result'] == 'running'
        conn.close()

        # Model is referenced while job is running -> delete is blocked with 409
        conn = http.client.HTTPConnection(host, port, timeout=5)
        conn.request('DELETE', f'/api/v1/models/{model_sha}')
        resp = conn.getresponse()
        assert resp.status == 409
        err = json.loads(resp.read())
        assert err['error']['code'] == 'MODEL_REFERENCED'
        conn.close()

        # 7. Abort job via POST /api/v1/jobs/{job_id}/abort
        called_abort = []
        daemon.console._abort_job = lambda jid, r: called_abort.append((jid, r)) or {
            'status': 'accepted', 'job_id': jid, 'execution_result': 'pending'
        }
        abort_body = json.dumps({'reason': 'operator_requested'}).encode()
        conn = http.client.HTTPConnection(host, port, timeout=5)
        conn.request('POST', '/api/v1/jobs/job-06-test/abort', body=abort_body, headers={
            'Content-Type': 'application/json',
            'Content-Length': str(len(abort_body))
        })
        resp = conn.getresponse()
        assert resp.status == 202
        conn.close()
        assert len(called_abort) == 1

        # Simulate abort outcome with recovery in progress
        daemon.server_state = ServerState.IDLE
        daemon._recent_job = {
            **daemon.active_job,
            'execution_result': 'aborted',
            'recovery_state': 'recovering',
            'server_phase': 'recovering',
            'reason': 'operator_requested'
        }
        daemon.active_job = None

        # 8. Gating before recovery completes
        conn = http.client.HTTPConnection(host, port, timeout=5)
        conn.request('GET', '/api/v1/state')
        resp = conn.getresponse()
        snap_recovering = json.loads(resp.read())
        assert snap_recovering['job']['execution_result'] == 'aborted'
        assert snap_recovering['job']['recovery_state'] == 'recovering'
        assert snap_recovering['server']['can_start_job'] is False
        assert any(b['code'] == 'JOB_RESOURCE_NOT_READY' for b in snap_recovering['server']['start_blockers'])
        conn.close()

        # 9. Recovery completes -> can_start_job restored
        daemon._recent_job['recovery_state'] = 'ready'
        daemon._recent_job['server_phase'] = 'ready'
        conn = http.client.HTTPConnection(host, port, timeout=5)
        conn.request('GET', '/api/v1/state')
        resp = conn.getresponse()
        snap_ready = json.loads(resp.read())
        assert snap_ready['server']['can_start_job'] is True
        assert len(snap_ready['server']['start_blockers']) == 0
        conn.close()

    finally:
        server.shutdown()
        worker.join()
        server.server_close()


def test_desktop_console_real_sse_and_node_http_integration(tmp_path):
    """Verifies real wire HTTP and SSE streaming with Node runtime and Python server."""
    daemon = make_daemon()
    daemon.server_state = ServerState.IDLE
    register_idle(daemon)
    daemon.console.models.root = tmp_path / 'models'
    daemon.console.models.root.mkdir(parents=True, exist_ok=True)

    server = ConsoleHTTPServer(('127.0.0.1', 0), ConsoleRequestHandler)
    server.application = daemon.console
    worker = threading.Thread(target=server.serve_forever)
    worker.start()

    host, port = server.server_address
    base_url = f'http://{host}:{port}'
    try:
        # 1. Verify real SSE stream header and initial snapshot over HTTP
        conn = http.client.HTTPConnection(host, port, timeout=5)
        conn.request('GET', '/api/v1/events')
        resp = conn.getresponse()
        assert resp.status == 200
        assert 'text/event-stream' in resp.getheader('Content-Type')
        line1 = resp.readline()
        assert line1.startswith(b'id: ')
        line2 = resp.readline()
        assert line2 == b'event: snapshot\n'
        data_line = resp.readline()
        assert data_line.startswith(b'data: {')
        conn.close()

        # 2. Run Node script performing real HTTP fetch against the live server
        node = shutil.which('node')
        assert node, 'Existing Node runtime required'
        node_script = f"""
        const assert = require('node:assert/strict');
        (async () => {{
            const base = '{base_url}';
            // Verify static assets
            const assetRes = await fetch(base + '/assets/console.js');
            assert.equal(assetRes.status, 200);
            assert.ok(assetRes.headers.get('content-type').includes('javascript'));

            // Verify state snapshot
            const stateRes = await fetch(base + '/api/v1/state');
            assert.equal(stateRes.status, 200);
            const state = await stateRes.json();
            assert.equal(state.server.state, 'idle');
            assert.equal(state.server.can_start_job, true);

            // Verify models endpoint
            const modelsRes = await fetch(base + '/api/v1/models');
            assert.equal(modelsRes.status, 200);
            const models = await modelsRes.json();
            assert.deepEqual(models.models, []);

            console.log('Real Node HTTP communication verified successfully');
        }})().catch(err => {{
            console.error(err);
            process.exit(1);
        }});
        """
        result = subprocess.run([node, '-e', node_script], capture_output=True, text=True, timeout=15)
        assert result.returncode == 0, f"STDOUT: {result.stdout}\nSTDERR: {result.stderr}"
        assert 'Real Node HTTP communication verified successfully' in result.stdout

    finally:
        server.shutdown()
        worker.join()
        server.server_close()

