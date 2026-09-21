#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import argparse
import hashlib
import os
import sys
import tempfile

from .artifacts import file_sha256
from .errors import FLRuntimeError


DEFAULT_ARTIFACT_SIZE_BYTES = 4 * 1024 * 1024
DEFAULT_ROUNDS = 1
DEFAULT_PARTICIPANT_NODE_IDS = (1, 2)
DEFAULT_TRAINING_DELAY_MS_BY_NODE = {'1': 0, '2': 3000}

MODEL_PATTERN = b'wfb-ng-issue41-model-4mib-v1\n'
CLIENT1_UPDATE_PATTERN = b'wfb-ng-issue41-client1-update-4mib-v1\n'
CLIENT2_UPDATE_PATTERN = b'wfb-ng-issue41-client2-update-4mib-v1\n'

PATTERNS_BY_ROLE = {
    'model': MODEL_PATTERN,
    'client1': CLIENT1_UPDATE_PATTERN,
    'client2': CLIENT2_UPDATE_PATTERN,
}

FILENAMES_BY_ROLE = {
    'model': 'model-4mib.bin',
    'client1': 'update-client1-4mib.bin',
    'client2': 'update-client2-4mib.bin',
}


def generate_deterministic_file(path, size_bytes=DEFAULT_ARTIFACT_SIZE_BYTES,
                                pattern=MODEL_PATTERN):
    """原子化确定性写入指定大小的二进制测试数据，并返回元数据。"""
    if size_bytes <= 0:
        raise ValueError('size_bytes 必须是正整数')
    if not pattern:
        raise ValueError('pattern 不能为空')

    abs_path = os.path.abspath(path)
    parent_dir = os.path.dirname(abs_path)
    os.makedirs(parent_dir, exist_ok=True)

    digest = hashlib.sha256()
    block_size = 64 * 1024
    chunk = (pattern * ((block_size // len(pattern)) + 1))[:block_size]

    fd, temp_path = tempfile.mkstemp(
        prefix='.issue41-fixture-', dir=parent_dir)
    try:
        with open(fd, 'wb') as fh:
            remaining = size_bytes
            while remaining > 0:
                current = chunk[:remaining]
                fh.write(current)
                digest.update(current)
                remaining -= len(current)
            fh.flush()
            os.fdatasync(fh.fileno())
        os.replace(temp_path, abs_path)
        os.chmod(abs_path, 0o644)
    except BaseException:
        if os.path.exists(temp_path):
            os.unlink(temp_path)
        raise

    return {
        'path': abs_path,
        'size_bytes': size_bytes,
        'sha256': digest.hexdigest(),
    }


def generate_model_fixture(dest_path_or_dir,
                           size_bytes=DEFAULT_ARTIFACT_SIZE_BYTES):
    """生成确定性初始模型文件。"""
    path = dest_path_or_dir
    if os.path.isdir(path) or path.endswith(os.sep):
        path = os.path.join(path, FILENAMES_BY_ROLE['model'])
    return generate_deterministic_file(
        path, size_bytes=size_bytes, pattern=PATTERNS_BY_ROLE['model'])


def generate_client_fixture(dest_path_or_dir, node_id,
                            size_bytes=DEFAULT_ARTIFACT_SIZE_BYTES):
    """生成指定客户端节点的确定性 update 模板。"""
    role_key = 'client%d' % node_id
    if role_key not in PATTERNS_BY_ROLE:
        raise ValueError('不支持的节点 ID：%s' % node_id)
    path = dest_path_or_dir
    if os.path.isdir(path) or path.endswith(os.sep):
        path = os.path.join(path, FILENAMES_BY_ROLE[role_key])
    return generate_deterministic_file(
        path, size_bytes=size_bytes, pattern=PATTERNS_BY_ROLE[role_key])


def generate_all_fixtures(dest_dir, size_bytes=DEFAULT_ARTIFACT_SIZE_BYTES):
    """在指定目录中确定性生成全部模型和 update 模板。"""
    os.makedirs(dest_dir, exist_ok=True)
    model = generate_model_fixture(dest_dir, size_bytes=size_bytes)
    client1 = generate_client_fixture(dest_dir, 1, size_bytes=size_bytes)
    client2 = generate_client_fixture(dest_dir, 2, size_bytes=size_bytes)

    if client1['sha256'] == client2['sha256']:
        raise FLRuntimeError(
            'fixture_generation_failed',
            'client1 与 client2 生成的 update 模板 SHA-256 意外相同')

    return {
        'model': model,
        'client1': client1,
        'client2': client2,
    }


def verify_fixture_file(path, expected_size=DEFAULT_ARTIFACT_SIZE_BYTES,
                        expected_sha256=None):
    """校验 fixture 文件存在、可读、大小匹配且 SHA-256 正确。"""
    if not os.path.isfile(path):
        raise FLRuntimeError(
            'invalid_configuration', 'fixture 不是普通文件：%s' % path)
    if not os.access(path, os.R_OK):
        raise FLRuntimeError(
            'invalid_configuration', 'fixture 不可读：%s' % path)

    actual_size = os.path.getsize(path)
    if expected_size is not None and actual_size != expected_size:
        raise FLRuntimeError(
            'invalid_configuration',
            'fixture %s 大小错误：期望 %d 字节，实际 %d 字节' % (
                path, expected_size, actual_size))

    digest = file_sha256(path)
    if expected_sha256 is not None and digest != expected_sha256:
        raise FLRuntimeError(
            'invalid_configuration',
            'fixture %s SHA-256 不匹配：期望 %s，实际 %s' % (
                path, expected_sha256, digest))

    return digest


def main(argv=None):
    parser = argparse.ArgumentParser(
        description='确定性生成或校验 Issue 41 验收 fixture (4 MiB)')
    subparsers = parser.add_subparsers(dest='subcommand', required=True)

    gen_parser = subparsers.add_parser('generate', help='生成 fixture 文件')
    gen_parser.add_argument(
        '--dest-dir', default='/var/lib/wfb-ng/issue41-input',
        help='fixture 输出目录')
    gen_parser.add_argument(
        '--role', choices=['model', 'client1', 'client2', 'all'],
        default='all', help='生成目标角色')
    gen_parser.add_argument(
        '--output', default=None,
        help='单个角色生成时的显式输出文件路径')
    gen_parser.add_argument(
        '--size', type=int, default=DEFAULT_ARTIFACT_SIZE_BYTES,
        help='生成文件大小（字节，默认 4 MiB）')

    verify_parser = subparsers.add_parser('verify', help='校验 fixture 文件')
    verify_parser.add_argument('path', help='待校验文件路径')
    verify_parser.add_argument(
        '--size', type=int, default=DEFAULT_ARTIFACT_SIZE_BYTES,
        help='期望文件大小（字节）')
    verify_parser.add_argument(
        '--sha256', default=None, help='期望的 SHA-256 哈希')

    args = parser.parse_args(argv)

    if args.subcommand == 'generate':
        if args.role == 'all':
            results = generate_all_fixtures(args.dest_dir, size_bytes=args.size)
            for role, info in results.items():
                print('%s: path=%s size=%d sha256=%s' % (
                    role, info['path'], info['size_bytes'], info['sha256']))
        else:
            dest = args.output or args.dest_dir
            if args.role == 'model':
                info = generate_model_fixture(dest, size_bytes=args.size)
            elif args.role == 'client1':
                info = generate_client_fixture(dest, 1, size_bytes=args.size)
            elif args.role == 'client2':
                info = generate_client_fixture(dest, 2, size_bytes=args.size)
            print('%s: path=%s size=%d sha256=%s' % (
                args.role, info['path'], info['size_bytes'], info['sha256']))
        return 0

    if args.subcommand == 'verify':
        digest = verify_fixture_file(
            args.path, expected_size=args.size, expected_sha256=args.sha256)
        print('OK: path=%s sha256=%s' % (args.path, digest))
        return 0

    return 1


if __name__ == '__main__':
    sys.exit(main())
