"""Issue 05 console SSE and abort public seams."""
import http.client
import json
import threading

from wfb_ng.fl.console_http import ConsoleHTTPServer, ConsoleRequestHandler
from wfb_ng.tests.test_fl_console import make_daemon


def test_console_stream_starts_with_snapshot_and_deduplicates_domain_events():
    daemon = make_daemon()
    subscriber = daemon.console.subscribe_events()
    try:
        first = daemon.console.initial_stream_frame(None)
        assert first['kind'] == 'snapshot'
        daemon.publish_event({'type': 'JOB_STARTED', 'job_id': 'job-1'})
        daemon.publish_event({'type': 'JOB_STARTED', 'job_id': 'job-1'})
        event = subscriber.get_nowait()
        assert event['kind'] == 'event'
        assert event['event']['type'] == 'JOB_STARTED'
        assert event['event']['sequence'] > first['id']
        snapshot = subscriber.get_nowait()
        assert snapshot['kind'] == 'snapshot'
        assert subscriber.empty()
    finally:
        daemon.console.unsubscribe_events(subscriber)


def test_console_stream_closes_a_slow_subscriber_after_bounded_overflow():
    daemon = make_daemon()
    subscriber = daemon.console.subscribe_events()
    try:
        for index in range(101):
            daemon.console.record_event({'type': 'ROUND_STARTED', 'round_id': str(index)})
        frames = []
        while not subscriber.empty():
            frames.append(subscriber.get_nowait())
        assert frames[-1] == {'kind': 'overflow'}
        assert subscriber not in daemon.console._stream_subscribers
    finally:
        daemon.console.unsubscribe_events(subscriber)


def test_web_sse_sends_ordered_snapshot_then_event_and_state_snapshot():
    daemon = make_daemon()
    server = ConsoleHTTPServer(('127.0.0.1', 0), ConsoleRequestHandler)
    server.application = daemon.console
    worker = threading.Thread(target=server.serve_forever)
    worker.start()
    conn = http.client.HTTPConnection(*server.server_address, timeout=3)
    try:
        conn.request('GET', '/api/v1/events')
        response = conn.getresponse()
        assert response.status == 200
        assert response.getheader('Content-Type').startswith('text/event-stream')
        assert response.readline().startswith(b'id: ')
        assert response.readline() == b'event: snapshot\n'
        snapshot_data = response.readline()
        assert snapshot_data.startswith(b'data: {')
        assert b'"instance_id"' in snapshot_data
        assert response.readline() == b'\n'
        daemon.publish_event({'type': 'JOB_STARTED', 'job_id': 'job-1'})
        event_id = int(response.readline().split(b': ', 1)[1])
        assert event_id > 0
        assert response.readline() == b'event: operator-event\n'
        assert response.readline().startswith(b'data: {')
        assert response.readline() == b'\n'
        next_id = int(response.readline().split(b': ', 1)[1])
        assert next_id > event_id
    finally:
        conn.close()
        server.shutdown()
        worker.join()
        server.server_close()


def test_web_abort_returns_accepted_and_keeps_execution_and_recovery_separate():
    daemon = make_daemon()
    daemon.active_job = {'job_id': 'job-1', 'target_nodes': [1]}
    accepted = daemon.console._abort_job
    called = []
    daemon.console._abort_job = lambda job_id, reason: called.append((job_id, reason)) or {
        'status': 'accepted', 'job_id': job_id, 'execution_result': 'pending'}
    server = ConsoleHTTPServer(('127.0.0.1', 0), ConsoleRequestHandler)
    server.application = daemon.console
    worker = threading.Thread(target=server.serve_forever)
    worker.start()
    try:
        conn = http.client.HTTPConnection(*server.server_address, timeout=3)
        body = json.dumps({'reason': 'operator_requested'}).encode()
        conn.request('POST', '/api/v1/jobs/job-1/abort', body=body,
                     headers={'Content-Type': 'application/json',
                              'Content-Length': str(len(body))})
        response = conn.getresponse()
        assert response.status == 202
        assert json.loads(response.read())['execution_result'] == 'pending'
        assert called == [('job-1', 'operator_requested')]
        conn.close()
    finally:
        daemon.console._abort_job = accepted
        server.shutdown()
        worker.join()
        server.server_close()
