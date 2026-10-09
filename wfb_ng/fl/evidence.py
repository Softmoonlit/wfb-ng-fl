"""Client 终态小型证据归档；最终目录的出现表示完整提交。"""

import ctypes
import errno
import os
from pathlib import Path
import re
import shutil
import stat
import tempfile
import uuid
from typing import Any, Dict

from .artifacts import (
    archive_open_file, read_json, validate_path_safe_identifier, write_json_atomic,
)
from .errors import FLRuntimeError


DEFAULT_EVIDENCE_DIR = '/var/lib/wfb-ng-fl/evidence'
BUILD_IDENTITY_PATH = Path(__file__).with_name('build_identity.json')
MAX_EVIDENCE_FILE_BYTES = 16 * 1024 * 1024
ROOT_FILES = (
    'role_service.log', 'uftpd.log', 'client_role.json', 'algorithm_config.json',
    'issue41-client-result.json', 'observation.jsonl', 'current-round.json',
)
ROUND_FILES = ('round-state.json', 'model.manifest.json', 'update.manifest.json')
LIFECYCLE_OUTCOMES = ('succeeded', 'start_failed', 'aborted', 'failed')


def _open_directory(path: str, *, create: bool = False) -> int:
    """逐级拒绝符号链接，避免沙箱或 evidence 父目录越界。"""
    fd = os.open('/', os.O_RDONLY | os.O_DIRECTORY)
    try:
        for part in Path(os.path.abspath(path)).parts[1:]:
            if create:
                try:
                    os.mkdir(part, dir_fd=fd)
                    os.fsync(fd)
                except FileExistsError:
                    pass
            child = os.open(part, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=fd)
            os.close(fd)
            fd = child
        return fd
    except BaseException:
        os.close(fd)
        raise


def _copy_file(directory_fd: int, name: str, target: Path) -> Dict[str, Any]:
    fd = os.open(name, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=directory_fd)
    with os.fdopen(fd, 'rb') as source:
        source_stat = os.fstat(source.fileno())
        if not stat.S_ISREG(source_stat.st_mode):
            raise FLRuntimeError('invalid_evidence_file', f'证据必须为普通文件: {name}')
        if source_stat.st_size > MAX_EVIDENCE_FILE_BYTES:
            raise FLRuntimeError('evidence_too_large', f'证据超过大小上限: {name}')
        size, digest = archive_open_file(source, source_stat, str(target), MAX_EVIDENCE_FILE_BYTES)
        return {'size_bytes': size, 'sha256': digest}


def _copy_existing(directory_fd: int, names: tuple, target: Path) -> Dict[str, Any]:
    files = {}
    for name in names:
        try:
            os.stat(name, dir_fd=directory_fd, follow_symlinks=False)
        except FileNotFoundError:
            continue
        files[name] = _copy_file(directory_fd, name, target / name)
    return files


def _publish_directory(root_fd: int, temporary_name: str, job_id: str) -> None:
    # Linux renameat2(RENAME_NOREPLACE) 原子拒绝任何已有目标（包括空目录）。
    renameat2 = ctypes.CDLL(None, use_errno=True).renameat2
    renameat2.argtypes = [ctypes.c_int, ctypes.c_char_p, ctypes.c_int, ctypes.c_char_p, ctypes.c_uint]
    renameat2.restype = ctypes.c_int
    if renameat2(root_fd, os.fsencode(temporary_name), root_fd, os.fsencode(job_id), 1):
        error = ctypes.get_errno()
        raise OSError(error, os.strerror(error), job_id)
    os.fsync(root_fd)


def archive_client_evidence(
    sandbox_dir: str, evidence_dir: str, *, run_id: str, job_id: str,
    node_id: int, lifecycle_outcome: str, returncode: int,
) -> str:
    """归档实际存在的白名单文件；任一步失败不覆盖目标，调用者保留沙箱。"""
    validate_path_safe_identifier(run_id, 'run_id')
    validate_path_safe_identifier(job_id, 'job_id')
    if lifecycle_outcome not in LIFECYCLE_OUTCOMES:
        raise ValueError('未知 lifecycle outcome')
    root_fd = _open_directory(evidence_dir, create=True)
    temporary = None
    try:
        try:
            os.stat(job_id, dir_fd=root_fd, follow_symlinks=False)
        except FileNotFoundError:
            pass
        else:
            raise FileExistsError(errno.EEXIST, '证据目录已存在', job_id)
        # /proc/self/fd 固定已验证的根目录，避免路径被替换后写到其他目录。
        root_path = Path(f'/proc/self/fd/{root_fd}')
        temporary = Path(tempfile.mkdtemp(prefix=f'.{job_id}.', dir=root_path))
        with_directory = _open_directory(str(BUILD_IDENTITY_PATH.parent))
        try:
            identity_info = _copy_file(with_directory, BUILD_IDENTITY_PATH.name, temporary / 'build_identity.json')
        finally:
            os.close(with_directory)
        identity = read_json(str(temporary / 'build_identity.json'))
        if (not isinstance(identity, dict) or type(identity.get('schema_version')) is not int
                or identity['schema_version'] != 1
                or not isinstance(identity.get('commit'), str)
                or re.fullmatch(r'[0-9a-f]{40}', identity['commit']) is None):
            raise FLRuntimeError('invalid_build_identity', '安装包 build identity 缺失完整提交')
        files = {'build_identity.json': identity_info}
        source_fd = _open_directory(sandbox_dir)
        try:
            files.update(_copy_existing(source_fd, ROOT_FILES, temporary))
            try:
                rounds_fd = os.open('rounds', os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=source_fd)
            except FileNotFoundError:
                rounds_fd = None
            if rounds_fd is not None:
                try:
                    for name in sorted(os.listdir(rounds_fd)):
                        try:
                            if str(uuid.UUID(name)) != name:
                                continue
                        except ValueError:
                            continue
                        round_fd = os.open(name, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=rounds_fd)
                        try:
                            for filename, info in _copy_existing(round_fd, ROUND_FILES, temporary / 'rounds' / name).items():
                                files[f'rounds/{name}/{filename}'] = info
                        finally:
                            os.close(round_fd)
                finally:
                    os.close(rounds_fd)
        finally:
            os.close(source_fd)
        write_json_atomic(str(temporary / 'evidence_manifest.json'), {
            'schema_version': 1, 'run_id': run_id, 'job_id': job_id, 'node_id': node_id,
            'runtime_commit': identity['commit'], 'build_identity_sha256': identity_info['sha256'],
            'lifecycle_outcome': lifecycle_outcome, 'returncode': returncode, 'files': files,
        })
        # 刷写目录树后才发布；archive_open_file 已刷写每个文件及其直接父目录。
        for directory, _, _ in os.walk(temporary, topdown=False):
            fd = os.open(directory, os.O_RDONLY | os.O_DIRECTORY)
            try:
                os.fsync(fd)
            finally:
                os.close(fd)
        _publish_directory(root_fd, temporary.name, job_id)
        temporary = None
        return os.path.join(os.path.abspath(evidence_dir), job_id)
    finally:
        if temporary is not None and temporary.exists():
            shutil.rmtree(temporary)
        os.close(root_fd)
