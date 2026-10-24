"""流式、持久化的 SHA-256 模型制品库。"""
from contextlib import ExitStack
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import tempfile
import threading
from typing import Any, BinaryIO, Dict, List

from .artifacts import file_sha256, fsync_directory, write_json_atomic
from .errors import FLRuntimeError


class ModelLibraryError(Exception):
    def __init__(self, code: str, message: str, status: int = 400) -> None:
        super().__init__(message)
        self.code = code
        self.status = status


class ModelLibrary:
    MAX_FILE_BYTES = 1024 ** 3
    MAX_LIBRARY_BYTES = 10 * 1024 ** 3
    CHUNK_BYTES = 64 * 1024

    def __init__(self, root: Path) -> None:
        self.root = Path(root)
        self._lock = threading.RLock()
        self._upload_lock = threading.Lock()
        self._models: Dict[str, Dict[str, Any]] = {}
        self._pending_cleanup: Dict[Path, int] = {}
        self._deleted: set = set()
        self._reserved = 0
        # Initialization is lazy: an unavailable model disk must not prevent
        # the daemon's wireless control plane from starting.
        self._loaded = False

    def _remove_residue(self, path: Path) -> None:
        try:
            if path.exists():
                shutil.rmtree(path)
            fsync_directory(self.root)
        except OSError as exc:
            if path.name.startswith('.deleted-'):
                raise ModelLibraryError('MODEL_DELETE_CLEANUP_FAILED',
                                        '模型已删除，磁盘清理尚未完成，请重试', 503) from exc
            raise

    def _cleanup(self) -> None:
        for path, size in list(self._pending_cleanup.items()):
            self._remove_residue(path)
            self._reserved -= size
            del self._pending_cleanup[path]

    def _load(self) -> None:
        self._cleanup()
        if self._loaded:
            return
        self.root.mkdir(parents=True, exist_ok=True)
        models = {}
        for entry in self.root.iterdir():
            if entry.name.startswith(('.upload-', '.deleted-')):
                deleted = entry.name.startswith('.deleted-')
                if deleted:
                    self._deleted.add(entry.name[len('.deleted-'):])
                self._remove_residue(entry)
            elif re.fullmatch('[0-9a-f]{64}', entry.name):
                metadata = json.loads((entry / 'metadata.json').read_text(encoding='utf-8'))
                if (metadata['sha256'] != entry.name or
                        (entry / 'content').stat().st_size != metadata['size_bytes'] or
                        file_sha256(entry / 'content') != entry.name):
                    raise OSError('模型库元数据与制品不一致')
                models[entry.name] = metadata
        self._models = models
        self._loaded = True

    @staticmethod
    def validate_digest(sha256: str) -> None:
        if re.fullmatch('[0-9a-f]{64}', sha256) is None:
            raise ModelLibraryError('INVALID_MODEL_SHA256', '模型摘要必须为完整小写 SHA-256')

    def _view(self, sha256: str) -> Dict[str, Any]:
        return dict(self._models[sha256], referenced=False)

    def list_models(self) -> List[Dict[str, Any]]:
        try:
            with self._lock:
                self._load()
                return sorted((self._view(key) for key in self._models),
                              key=lambda model: (-datetime.fromisoformat(model['created_at']).timestamp(),
                                                 model['sha256']))
        except (OSError, ValueError, KeyError, TypeError, FLRuntimeError) as exc:
            raise ModelLibraryError('MODEL_STORAGE_FAILED', '模型库暂时不可用，请重试', 503) from exc

    def upload(self, stream: BinaryIO, size: int, filename: str) -> Dict[str, Any]:
        if size <= 0:
            raise ModelLibraryError('MODEL_EMPTY', '不能上传空文件')
        if size > self.MAX_FILE_BYTES:
            raise ModelLibraryError('MODEL_TOO_LARGE', '单文件不能超过 1 GiB', 413)
        if (not filename or len(filename.encode('utf-8')) > 255
                or filename in ('.', '..') or '/' in filename or '\\' in filename
                or any(ord(c) < 32 or ord(c) == 127 for c in filename)):
            raise ModelLibraryError('INVALID_FILENAME', '文件名须为 1～255 UTF-8 字节，且不含路径或控制字符')
        staging = None
        reserved = False
        # One active upload; extra requests fail promptly rather than waiting
        # with a socket timeout running behind another large file.
        if not self._upload_lock.acquire(blocking=False):
            raise ModelLibraryError('MODEL_UPLOAD_BUSY', '已有文件正在上传，请稍后重试', 409)
        try:
            try:
                with self._lock:
                    self._load()
                    used = sum(model['size_bytes'] for model in self._models.values())
                    if used + self._reserved + size <= self.MAX_LIBRARY_BYTES:
                        self._reserved += size
                        reserved = True
                        staging = Path(tempfile.mkdtemp(prefix='.upload-', dir=self.root))
                    # A full library can still deduplicate. Hash without writing
                    # another copy, then reject if this is new content.
                digest = hashlib.sha256()
                remaining = size
                with ExitStack() as stack:
                    output = stack.enter_context((staging / 'content').open('wb')) if staging else None
                    while remaining:
                        chunk = stream.read(min(remaining, self.CHUNK_BYTES))
                        if not chunk:
                            raise ModelLibraryError('UPLOAD_INTERRUPTED', '上传未完成，请重新上传')
                        if len(chunk) > remaining:
                            raise ModelLibraryError('INVALID_REQUEST', '上传长度非法')
                        if output is not None:
                            output.write(chunk)
                        digest.update(chunk)
                        remaining -= len(chunk)
                    if output is not None:
                        output.flush()
                        os.fsync(output.fileno())
                # The HTTP stream validates framing before publication.
                finish = getattr(stream, 'finish', None)
                if finish is not None:
                    finish()
                sha256 = digest.hexdigest()
                metadata = {'sha256': sha256, 'filename': filename, 'size_bytes': size,
                            'created_at': datetime.now(timezone.utc).isoformat()}
                with self._lock:
                    if sha256 in self._models:
                        return {'model': self._view(sha256), 'deduplicated': True}
                    if staging is None:
                        raise ModelLibraryError('MODEL_CAPACITY_EXCEEDED', '模型库容量不能超过 10 GiB', 507)
                    write_json_atomic(staging / 'metadata.json', metadata)
                    destination = self.root / sha256
                    staging.rename(destination)
                    try:
                        fsync_directory(self.root)
                    except OSError:
                        destination.rename(staging)
                        raise
                    staging = None
                    self._models[sha256] = metadata
                    self._deleted.discard(sha256)
                    return {'model': self._view(sha256), 'deduplicated': False}
            except ModelLibraryError:
                raise
            except (TimeoutError, ConnectionError) as exc:
                raise ModelLibraryError('UPLOAD_INTERRUPTED', '上传连接中断，请重新上传') from exc
            except (OSError, ValueError, KeyError, TypeError, FLRuntimeError) as exc:
                raise ModelLibraryError('MODEL_STORAGE_FAILED', '模型写入失败，请重试', 503) from exc
            finally:
                with self._lock:
                    if staging is not None:
                        self._pending_cleanup[staging] = size
                        try:
                            self._cleanup()
                        except OSError as exc:
                            raise ModelLibraryError('MODEL_STORAGE_FAILED', '临时上传清理失败，模型库暂停写入，请重试', 503) from exc
                    elif reserved:
                        self._reserved -= size
        finally:
            self._upload_lock.release()

    def artifact_path(self, sha256: str) -> Path:
        self.validate_digest(sha256)
        with self._lock:
            self.list_models()
            if sha256 not in self._models:
                raise ModelLibraryError('MODEL_NOT_FOUND', '模型不存在', 404)
            return self.root / sha256 / 'content'

    def delete(self, sha256: str) -> None:
        self.validate_digest(sha256)
        with self._lock:
            self.list_models()
            if sha256 in self._deleted and sha256 not in self._models:
                return
            path = self.artifact_path(sha256).parent
            try:
                tombstone = self.root / ('.deleted-' + sha256)
                path.rename(tombstone)
                try:
                    fsync_directory(self.root)
                except OSError:
                    tombstone.rename(path)
                    raise
            except OSError as exc:
                raise ModelLibraryError('MODEL_STORAGE_FAILED', '模型删除失败，请重试', 503) from exc
            size = self._models.pop(sha256)['size_bytes']
            self._deleted.add(sha256)
            self._pending_cleanup[tombstone] = size
            self._reserved += size
            self._cleanup()
