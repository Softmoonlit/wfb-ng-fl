#!/usr/bin/env python
# -*- coding: utf-8 -*-

import os
import shutil
import signal
import stat
import subprocess
import tempfile
import time
import unittest
import uuid


_SYSTEMD_TEST_ENABLED = (
    os.environ.get('WFB_RUN_SYSTEMD_TESTS') == '1' and
    hasattr(os, 'geteuid') and os.geteuid() == 0 and
    shutil.which('systemctl') is not None
)


@unittest.skipUnless(
    _SYSTEMD_TEST_ENABLED,
    '设置 WFB_RUN_SYSTEMD_TESTS=1 并以 root 运行 systemd 生命周期测试')
class RoleSystemdLifecycleTestCase(unittest.TestCase):
    def setUp(self):
        self.root = tempfile.mkdtemp(prefix='wfb-v8-systemd-')
        self.unit_name = 'wfb-fl-lifecycle-%s.service' % uuid.uuid4().hex
        self.unit_path = os.path.join('/run/systemd/system', self.unit_name)
        self.generation_path = os.path.join(self.root, 'generation')
        fixture = self.write_fixture()
        with open(self.unit_path, 'w', encoding='utf-8') as fh:
            fh.write(
                '[Unit]\n'
                'Description=WFB-ng FL lifecycle test\n\n'
                '[Service]\n'
                'Type=simple\n'
                'ExecStart=%s %s\n'
                'KillMode=control-group\n'
                'TimeoutStopSec=2s\n'
                'Restart=on-failure\n'
                'RestartSec=100ms\n' % (fixture, self.root))
        subprocess.run(['systemctl', 'daemon-reload'], check=True)
        self.addCleanup(self.cleanup_unit)

    def test_stop_kill_and_restart_leave_no_old_children(self):
        subprocess.run(['systemctl', 'start', self.unit_name], check=True)
        first_main, first_child = self.wait_for_generation(1)

        subprocess.run(['systemctl', 'stop', self.unit_name], check=True)
        self.assert_process_gone(first_main)
        self.assert_process_gone(first_child)

        subprocess.run(['systemctl', 'start', self.unit_name], check=True)
        second_main, second_child = self.wait_for_generation(2)
        os.kill(second_main, signal.SIGKILL)
        third_main, third_child = self.wait_for_generation(3)

        self.assertNotEqual(second_main, third_main)
        self.assert_process_gone(second_child)
        subprocess.run(['systemctl', 'stop', self.unit_name], check=True)
        self.assert_process_gone(third_main)
        self.assert_process_gone(third_child)

    def write_fixture(self):
        path = os.path.join(self.root, 'role-fixture')
        with open(path, 'w', encoding='utf-8') as fh:
            fh.write(
                '#!/usr/bin/env python3\n'
                'import os\n'
                'import subprocess\n'
                'import sys\n'
                'import time\n'
                'root = sys.argv[1]\n'
                'generation_path = os.path.join(root, "generation")\n'
                'try:\n'
                '    with open(generation_path, "r") as source:\n'
                '        generation = int(source.read()) + 1\n'
                'except FileNotFoundError:\n'
                '    generation = 1\n'
                'with open(generation_path, "w") as output:\n'
                '    output.write(str(generation))\n'
                'child = subprocess.Popen([sys.executable, "-c", '
                '"import time; time.sleep(3600)"])\n'
                'with open(os.path.join(root, "pids-%d" % generation), "w") as output:\n'
                '    output.write("%d %d" % (os.getpid(), child.pid))\n'
                'while True:\n'
                '    time.sleep(3600)\n')
        os.chmod(path, stat.S_IRUSR | stat.S_IWUSR | stat.S_IXUSR)
        return path

    def wait_for_generation(self, generation):
        path = os.path.join(self.root, 'pids-%d' % generation)
        deadline = time.monotonic() + 5
        while time.monotonic() < deadline:
            try:
                with open(path, 'r', encoding='ascii') as fh:
                    main_pid, child_pid = map(int, fh.read().split())
                if self.process_exists(main_pid) and self.process_exists(child_pid):
                    return main_pid, child_pid
            except (FileNotFoundError, ValueError):
                pass
            time.sleep(0.02)
        self.fail('systemd unit generation %d did not start' % generation)

    def assert_process_gone(self, pid):
        deadline = time.monotonic() + 5
        while time.monotonic() < deadline:
            if not self.process_exists(pid):
                return
            time.sleep(0.02)
        self.fail('process %d was not removed from the unit cgroup' % pid)

    def process_exists(self, pid):
        try:
            os.kill(pid, 0)
        except ProcessLookupError:
            return False
        return True

    def cleanup_unit(self):
        subprocess.run(
            ['systemctl', 'stop', self.unit_name], check=False,
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        try:
            os.unlink(self.unit_path)
        except FileNotFoundError:
            pass
        subprocess.run(
            ['systemctl', 'daemon-reload'], check=False,
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        shutil.rmtree(self.root, ignore_errors=True)


if __name__ == '__main__':
    unittest.main()
