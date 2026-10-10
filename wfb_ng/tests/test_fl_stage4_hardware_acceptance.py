from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from tests.fl_runtime.stage4_hardware_acceptance import AuditedExecutor, _multipart
from tests.fl_runtime.stage4_hardware_archive import STAGES, init_archive, record_stage


class Stage4HardwareToolTests(unittest.TestCase):
    def test_archive_stages_are_strictly_ordered(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp) / "stage4-hardware-20261010T120000Z-abcdef12"
            init_archive(root, run_id=root.name, commit="a" * 40, web_url="http://10.0.0.1:8080")
            record_stage(root, "preflight", status="passed")
            with self.assertRaisesRegex(ValueError, "invalid stage order"):
                record_stage(root, "normal-job", status="passed")

    def test_executor_refuses_client_ssh_during_job_window(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp) / "stage4-hardware-20261010T120000Z-abcdef12"
            init_archive(root, run_id=root.name, commit="a" * 40, web_url="http://10.0.0.1:8080")
            executor = AuditedExecutor(root)
            executor.job_window = True
            with self.assertRaisesRegex(RuntimeError, "SSH is forbidden"):
                executor.run("client1", "true")

    def test_server_commands_remain_local_during_job_window(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp) / "stage4-hardware-20261010T120000Z-abcdef12"
            init_archive(root, run_id=root.name, commit="a" * 40, web_url="http://10.0.0.1:8080")
            executor = AuditedExecutor(root)
            executor.job_window = True
            with patch("subprocess.run") as run:
                run.return_value.returncode = 0
                run.return_value.stdout = "ok\n"
                run.return_value.stderr = ""
                self.assertEqual(executor.checked("server", "true"), "ok\n")
                self.assertEqual(run.call_args.args[0][0:2], ["bash", "-lc"])

    def test_multipart_contains_only_the_model_file_part(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            source = Path(temp) / "model.bin"
            source.write_bytes(b"model")
            body, boundary = _multipart(source)
            self.assertEqual(body.count(("--" + boundary).encode()), 2)
            self.assertIn(b'name="file"; filename="model.bin"', body)
            self.assertTrue(body.endswith(("--" + boundary + "--\r\n").encode()))

    def test_archive_contract_lists_required_partitions(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp) / "stage4-hardware-20261010T120000Z-abcdef12"
            envelope = init_archive(root, run_id=root.name, commit="a" * 40, web_url="http://10.0.0.1:8080")
            self.assertEqual(tuple(envelope["topology"]["clients"][i]["node_id"] for i in range(2)), (1, 2))
            self.assertEqual(tuple(sorted(p.name for p in root.iterdir() if p.is_dir())),
                             ("control-plane", "data-plane", "lifecycle", "management-web", "summary"))
            self.assertEqual(tuple(STAGES), ("preflight", "web-upload", "normal-job", "abort-job", "collect", "summary"))


if __name__ == "__main__":
    unittest.main()
