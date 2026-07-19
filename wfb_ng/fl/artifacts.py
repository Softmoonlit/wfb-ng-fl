#!/usr/bin/env python
# -*- coding: utf-8 -*-

import hashlib
import json
import os
import stat
import tempfile

from .errors import FLRuntimeError


CHUNK_SIZE = 64 * 1024


def archive_file(source_path, target_path, max_size_bytes=None):
    source_path = os.path.abspath(os.fspath(source_path))
    try:
        source = open(source_path, 'rb')
    except OSError as exc:
        raise FLRuntimeError('invalid_artifact', '无法读取输入文件') from exc

    temp_path = None
    try:
        source_stat = os.fstat(source.fileno())
        if not stat.S_ISREG(source_stat.st_mode):
            raise FLRuntimeError('invalid_artifact', '输入必须是普通文件')
        if max_size_bytes is not None and source_stat.st_size > max_size_bytes:
            raise FLRuntimeError('artifact_too_large', '输入文件超过大小限制')

        os.makedirs(os.path.dirname(target_path), exist_ok=True)
        digest = hashlib.sha256()
        size_bytes = 0
        with tempfile.NamedTemporaryFile(
                mode='wb', dir=os.path.dirname(target_path),
                prefix='.%s.' % os.path.basename(target_path), delete=False) as target:
            temp_path = target.name
            while True:
                chunk = source.read(CHUNK_SIZE)
                if not chunk:
                    break
                target.write(chunk)
                digest.update(chunk)
                size_bytes += len(chunk)
            target.flush()
            os.fsync(target.fileno())

        final_stat = os.fstat(source.fileno())
        identity = ('st_dev', 'st_ino', 'st_size', 'st_mtime_ns')
        if any(getattr(source_stat, name) != getattr(final_stat, name) for name in identity):
            raise FLRuntimeError('artifact_changed', '输入文件在归档期间发生变化')
        os.replace(temp_path, target_path)
        temp_path = None
        return size_bytes, digest.hexdigest()
    finally:
        source.close()
        if temp_path is not None:
            try:
                os.unlink(temp_path)
            except FileNotFoundError:
                pass


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
    finally:
        if temp_path is not None:
            try:
                os.unlink(temp_path)
            except FileNotFoundError:
                pass


def read_json(path):
    try:
        with open(path, 'r', encoding='utf-8') as fh:
            return json.load(fh)
    except (OSError, ValueError) as exc:
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


def validate_artifact(path, size_bytes, expected_sha256):
    try:
        actual_size = os.path.getsize(path)
    except OSError as exc:
        raise FLRuntimeError('artifact_integrity_failed', '托管文件不存在') from exc
    if actual_size != size_bytes or file_sha256(path) != expected_sha256:
        raise FLRuntimeError('artifact_integrity_failed', '托管文件完整性校验失败')
