#!/usr/bin/env python
# -*- coding: utf-8 -*-

import hashlib
import json
import os
import stat
import tempfile

from .errors import FLRuntimeError


CHUNK_SIZE = 64 * 1024


def _fsync_directory(path):
    directory_fd = os.open(path, os.O_RDONLY | os.O_DIRECTORY)
    try:
        os.fsync(directory_fd)
    finally:
        os.close(directory_fd)


def _open_artifact(source_path, max_size_bytes=None):
    try:
        source_path = os.path.abspath(os.fspath(source_path))
        source = open(source_path, 'rb')
    except (OSError, TypeError, ValueError) as exc:
        raise FLRuntimeError('invalid_artifact', '无法读取输入文件') from exc
    try:
        source_stat = os.fstat(source.fileno())
        if not stat.S_ISREG(source_stat.st_mode):
            raise FLRuntimeError('invalid_artifact', '输入必须是普通文件')
        if max_size_bytes is not None and source_stat.st_size > max_size_bytes:
            raise FLRuntimeError('artifact_too_large', '输入文件超过大小限制')
        return source, source_stat
    except Exception:
        source.close()
        raise


def inspect_artifact(source_path, max_size_bytes=None):
    source, _ = _open_artifact(source_path, max_size_bytes)
    try:
        return os.path.abspath(os.fspath(source_path))
    finally:
        source.close()


def _read_archive_chunk(source):
    return source.read(CHUNK_SIZE)


def archive_file(source_path, target_path, max_size_bytes=None):
    source, source_stat = _open_artifact(source_path, max_size_bytes)
    try:
        return archive_open_file(source, source_stat, target_path, max_size_bytes)
    finally:
        source.close()


def archive_open_file(source, source_stat, target_path, max_size_bytes=None):
    temp_path = None
    try:
        os.makedirs(os.path.dirname(target_path), exist_ok=True)
        digest = hashlib.sha256()
        size_bytes = 0
        with tempfile.NamedTemporaryFile(
                mode='wb', dir=os.path.dirname(target_path),
                prefix='.%s.' % os.path.basename(target_path), delete=False) as target:
            temp_path = target.name
            while True:
                chunk = _read_archive_chunk(source)
                if not chunk:
                    break
                target.write(chunk)
                digest.update(chunk)
                size_bytes += len(chunk)
                if max_size_bytes is not None and size_bytes > max_size_bytes:
                    raise FLRuntimeError('artifact_too_large', '输入文件超过大小限制')
            target.flush()
            os.fsync(target.fileno())

        final_stat = os.fstat(source.fileno())
        identity = ('st_dev', 'st_ino', 'st_size', 'st_mtime_ns')
        if any(getattr(source_stat, name) != getattr(final_stat, name)
               for name in identity):
            raise FLRuntimeError('artifact_changed', '输入文件在归档期间发生变化')

        source.seek(0)
        verification_digest = hashlib.sha256()
        verification_size = 0
        while True:
            chunk = _read_archive_chunk(source)
            if not chunk:
                break
            verification_digest.update(chunk)
            verification_size += len(chunk)
        verified_stat = os.fstat(source.fileno())
        if (any(getattr(source_stat, name) != getattr(verified_stat, name)
                for name in identity) or
                verification_size != size_bytes or
                verification_digest.digest() != digest.digest()):
            raise FLRuntimeError('artifact_changed', '输入文件在归档期间发生变化')

        os.replace(temp_path, target_path)
        temp_path = None
        _fsync_directory(os.path.dirname(target_path))
        return size_bytes, digest.hexdigest()
    finally:
        if temp_path is not None:
            try:
                os.unlink(temp_path)
            except FileNotFoundError:
                pass


def preflight_artifact(source_path, max_size_bytes=None):
    return _open_artifact(source_path, max_size_bytes)


def write_json_atomic(path, value):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    temp_path = None
    try:
        with tempfile.NamedTemporaryFile(
                mode='w', encoding='utf-8', dir=os.path.dirname(path),
                prefix='.%s.' % os.path.basename(path), delete=False) as fh:
            temp_path = fh.name
            json.dump(value, fh, ensure_ascii=False, sort_keys=True, separators=(',', ':'))
            fh.write('\n')
            fh.flush()
            os.fsync(fh.fileno())
        os.replace(temp_path, path)
        temp_path = None
        _fsync_directory(os.path.dirname(path))
    finally:
        if temp_path is not None:
            try:
                os.unlink(temp_path)
            except FileNotFoundError:
                pass


def _strict_object(pairs):
    value = {}
    for key, item in pairs:
        if key in value:
            raise ValueError('duplicate JSON key')
        value[key] = item
    return value


def _reject_json_constant(value):
    raise ValueError('invalid JSON constant: %s' % value)


def read_json(path):
    try:
        with open(path, 'r', encoding='utf-8') as fh:
            return json.load(
                fh, object_pairs_hook=_strict_object,
                parse_constant=_reject_json_constant)
    except (OSError, UnicodeError, ValueError) as exc:
        raise FLRuntimeError('invalid_manifest', '无法读取 manifest') from exc


def file_sha256(path):
    digest = hashlib.sha256()
    try:
        with open(path, 'rb') as fh:
            while True:
                chunk = fh.read(CHUNK_SIZE)
                if not chunk:
                    return digest.hexdigest()
                digest.update(chunk)
    except OSError as exc:
        raise FLRuntimeError('artifact_integrity_failed', '无法校验托管文件') from exc


def validate_artifact(path, size_bytes, expected_sha256, max_size_bytes=None):
    try:
        source, source_stat = _open_artifact(path, max_size_bytes)
    except FLRuntimeError as exc:
        if exc.error_code == 'artifact_too_large':
            raise
        raise FLRuntimeError(
            'artifact_integrity_failed', '无法校验托管文件') from exc
    try:
        if source_stat.st_size != size_bytes:
            raise FLRuntimeError('artifact_integrity_failed', '托管文件完整性校验失败')
        digest = hashlib.sha256()
        size = 0
        while True:
            chunk = source.read(CHUNK_SIZE)
            if not chunk:
                break
            digest.update(chunk)
            size += len(chunk)
        final_stat = os.fstat(source.fileno())
        identity = ('st_dev', 'st_ino', 'st_size', 'st_mtime_ns')
        if (any(getattr(source_stat, name) != getattr(final_stat, name)
                for name in identity) or
                size != size_bytes or digest.hexdigest() != expected_sha256):
            raise FLRuntimeError('artifact_integrity_failed', '托管文件完整性校验失败')
    except FLRuntimeError:
        raise
    except OSError as exc:
        raise FLRuntimeError('artifact_integrity_failed', '无法校验托管文件') from exc
    finally:
        source.close()
