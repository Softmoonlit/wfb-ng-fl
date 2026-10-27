"""在 Jetson 上用现有 snap Chromium 检查真实页面；不连接正在运行的无线服务。

运行：PYTHONPATH=. python3 wfb_ng/tests/online_target_browser_check.py
"""
import io
from pathlib import Path
import subprocess
import tempfile
import threading
import time
from unittest.mock import Mock

from wfb_ng.fl.client_daemon import NetworkAdapter
from wfb_ng.fl.console_http import ConsoleHTTPServer, ConsoleRequestHandler
from wfb_ng.fl.control import NodeHeartbeat
from wfb_ng.fl.server_daemon import ServerDaemon, ServerDaemonConfig, ServerState


def main():
    # snap 的 /tmp 不与宿主共享，浏览器临时目录放在其可访问位置。
    with tempfile.TemporaryDirectory(prefix='wfb-online-browser-',
                                     dir=Path.home() / 'snap/chromium/common') as tmp:
        adapter = Mock(spec=NetworkAdapter)
        adapter.is_tun_active.return_value = True
        daemon = ServerDaemon(ServerDaemonConfig(
            enable_link_process=False, enable_control_plane=False,
            model_library_dir=tmp + '/models', work_dir=tmp), adapter)
        daemon.server_state = ServerState.IDLE

        def heartbeat(node_id, state):
            daemon.control_plane.registry.update_heartbeat(NodeHeartbeat(
                node_id=node_id, state=state, elapsed_ms=0, current_channel=157,
                txpower_dbm=12, uplink_mcs=6),
                ('127.0.0.1', 0), daemon.radio_config)

        heartbeat(1, 'HUNTING')
        heartbeat(1, 'IDLE')
        heartbeat(2, 'HUNTING')
        daemon.console.upload_model(io.BytesIO(b'model'), 5, 'model.bin')

        class Handler(ConsoleRequestHandler):
            def do_POST(self):
                # 在候选呈现之后、正式受理之前改变服务端事实，复现提交竞态。
                if self.path == '/api/v1/jobs':
                    heartbeat(1, 'RUNNING')
                super().do_POST()

        server = ConsoleHTTPServer(('127.0.0.1', 0), Handler)
        server.application = daemon.console
        worker = threading.Thread(target=server.serve_forever)
        worker.start()
        chromium = None
        try:
            with open(tmp + '/chromium.log', 'w') as log:
                chromium = subprocess.Popen([
                    '/snap/bin/chromium', '--headless', '--no-sandbox', '--disable-gpu',
                    '--remote-debugging-port=0', '--user-data-dir=' + tmp + '/chrome',
                    'about:blank'], stdout=subprocess.DEVNULL, stderr=log)
                port_file = Path(tmp) / 'chrome/DevToolsActivePort'
                deadline = time.monotonic() + 20
                while not port_file.exists():
                    if chromium.poll() is not None or time.monotonic() >= deadline:
                        raise RuntimeError(Path(tmp + '/chromium.log').read_text())
                    time.sleep(.1)
                port = port_file.read_text().splitlines()[0]
                subprocess.run([
                    'node', str(Path(__file__).with_suffix('.js')),
                    f'http://127.0.0.1:{port}', f'http://127.0.0.1:{server.server_port}/'],
                    check=True, timeout=30)
        finally:
            if chromium is not None:
                chromium.terminate()
                chromium.wait(timeout=10)
            server.shutdown()
            worker.join()
            server.server_close()


if __name__ == '__main__':
    main()
