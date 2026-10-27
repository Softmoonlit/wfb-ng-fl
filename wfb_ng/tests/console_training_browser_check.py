"""工单07真实页面检查：只使用 loopback ConsoleHTTPServer 与现有 Chromium CDP helper。"""
import subprocess
import tempfile
import threading
import time
from pathlib import Path
from unittest.mock import Mock

from wfb_ng.fl.client_daemon import NetworkAdapter
from wfb_ng.fl.console_http import ConsoleHTTPServer, ConsoleRequestHandler
from wfb_ng.fl.server_daemon import ServerDaemon, ServerDaemonConfig, ServerState


def main() -> None:
    # Chromium snap 只能稳定访问其 common 临时目录；服务仍只绑定 loopback。
    with tempfile.TemporaryDirectory(
            prefix='wfb-console-training-browser-',
            dir=Path.home() / 'snap/chromium/common') as tmp:
        adapter = Mock(spec=NetworkAdapter)
        adapter.is_tun_active.return_value = True
        daemon = ServerDaemon(ServerDaemonConfig(
            enable_link_process=False,
            enable_control_plane=False,
            model_library_dir=tmp + '/models',
            work_dir=tmp,
        ), adapter)
        daemon.server_state = ServerState.IDLE
        daemon._recent_job = {
            'job_id': 'job-07-browser',
            'run_id': 'run-07-browser',
            'model_sha256': 'a' * 64,
            'model_size_bytes': 10,
            'target_nodes': [1, 2],
            'rounds': 2,
            'rounds_completed': 2,
            'server_phase': 'recovery_unconfirmed',
            'execution_result': 'failed',
            'recovery_state': 'unconfirmed',
            'server_recovery_complete': False,
            'recovery_missing_nodes': [2],
            'recovery_reports': {
                1: {'report_id': 'recovery-1', 'report': {
                    'recovery_epoch': 1, 'recovery_state': 'ready'}},
            },
            'summary': {
                'status': 'failed',
                'execution_result': 'failed',
                'rounds_completed': 2,
                'initial_metrics': {'loss': 2.0, 'accuracy': 0.0},
                'rounds': [{
                    'round_index': 1,
                    'input_model_sha256': 'a' * 64,
                    'output_model_sha256': 'b' * 64,
                    'metrics': {'loss': 0.0, 'accuracy': 0.0},
                    'verdict': False,
                    'reason': '受控未达标',
                    'committed_nodes': [1, 2],
                    'client_submission_returns_received': False,
                    'training': {
                        '1': {
                            'node_id': 1,
                            'round_index': 1,
                            'base_model_sha256': 'a' * 64,
                            'sample_count': 60000,
                            'optimizer_steps': 938,
                            'train_loss': 0.0,
                            'device': {'type': 'cuda', 'name': 'GPU 1'},
                            'metadata_status': 'available',
                        },
                        '2': {
                            'node_id': 2,
                            'round_index': 1,
                            'base_model_sha256': 'a' * 64,
                            'sample_count': 60000,
                            'optimizer_steps': 938,
                            'train_loss': 0.0,
                            'device': {'type': 'cuda', 'name': 'GPU 2'},
                            'metadata_status': 'available',
                        },
                    },
                }, {
                    'round_index': 2,
                    'input_model_sha256': 'b' * 64,
                    'output_model_sha256': 'c' * 64,
                    'metrics': {'loss': 1.25, 'accuracy': 0.75},
                    'verdict': False,
                    'reason': '受控未达标',
                    'committed_nodes': [1, 2],
                    'client_submission_returns_received': True,
                    'training': {
                        '1': {'node_id': 1, 'round_index': 2, 'sample_count': 60000,
                              'optimizer_steps': 938, 'train_loss': 0.5,
                              'device': {'type': 'cuda', 'name': 'GPU 1'},
                              'metadata_status': 'available'},
                        '2': {'node_id': 2, 'round_index': 2, 'sample_count': 60000,
                              'optimizer_steps': 938, 'train_loss': 0.5,
                              'device': {'type': 'cuda', 'name': 'GPU 2'},
                              'metadata_status': 'available'},
                    },
                }],
                'effect_result': {'round_index': 2, 'metrics': {'loss': 1.25, 'accuracy': 0.75},
                                  'verdict': False, 'reason': '受控未达标'},
                'final_model_sha256': 'c' * 64,
                'final_model_size_bytes': 12,
            },
            'client_preparation_reports': {
                1: {'sample_count': 60000, 'device': {'type': 'cuda', 'name': 'GPU 1'}},
                2: {'sample_count': 60000, 'device': {'type': 'cuda', 'name': 'GPU 2'}},
            },
        }
        # controlled snapshot：不让宿主测试触发真实 recovery control 查询。
        daemon._refresh_job_recovery = lambda: None
        server = ConsoleHTTPServer(('127.0.0.1', 0), ConsoleRequestHandler)
        server.application = daemon.console
        worker = threading.Thread(target=server.serve_forever, daemon=True)
        worker.start()
        chromium = None
        try:
            with open(tmp + '/chromium.log', 'w', encoding='utf-8') as log:
                chromium = subprocess.Popen([
                    '/snap/bin/chromium', '--headless', '--no-sandbox', '--disable-gpu',
                    '--remote-debugging-port=0', '--user-data-dir=' + tmp + '/chrome',
                    'about:blank',
                ], stdout=subprocess.DEVNULL, stderr=log)
                port_file = Path(tmp) / 'chrome/DevToolsActivePort'
                deadline = time.monotonic() + 20
                while not port_file.exists():
                    if chromium.poll() is not None or time.monotonic() >= deadline:
                        raise RuntimeError(Path(tmp + '/chromium.log').read_text(encoding='utf-8'))
                    time.sleep(0.1)
                debug_port = port_file.read_text(encoding='utf-8').splitlines()[0]
                subprocess.run([
                    'node', str(Path(__file__).with_suffix('.js')),
                    f'http://127.0.0.1:{debug_port}',
                    f'http://127.0.0.1:{server.server_port}/',
                ], check=True, timeout=30)
        finally:
            if chromium is not None:
                chromium.terminate()
                chromium.wait(timeout=10)
            server.shutdown()
            worker.join(timeout=5)
            server.server_close()
            daemon.stop()


if __name__ == '__main__':
    main()
