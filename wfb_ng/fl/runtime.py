#!/usr/bin/env python
# -*- coding: utf-8 -*-

import errno
import fcntl
import os
import threading
import uuid
from types import MappingProxyType

from .artifacts import (
    archive_file,
    inspect_artifact,
    read_json,
    validate_artifact,
    write_json_atomic,
)
from .errors import FLRuntimeError


class WorkDirLock(object):
    def __init__(self, work_dir):
        os.makedirs(work_dir, exist_ok=True)
        lock_path = os.path.join(work_dir, 'runtime.lock')
        self._lock_file = None
        try:
            self._lock_file = open(lock_path, 'a+b')
        except OSError as exc:
            raise FLRuntimeError(
                'work_dir_unavailable', '无法打开 Runtime 工作目录锁') from exc
        try:
            fcntl.flock(
                self._lock_file.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError as exc:
            self._lock_file.close()
            self._lock_file = None
            if exc.errno in (errno.EACCES, errno.EAGAIN):
                raise FLRuntimeError(
                    'work_dir_locked',
                    '工作目录已被其他 Runtime 实例占用') from exc
            raise FLRuntimeError(
                'work_dir_unavailable', '无法取得 Runtime 工作目录锁') from exc

    def close(self):
        if self._lock_file is None:
            return
        fcntl.flock(self._lock_file.fileno(), fcntl.LOCK_UN)
        self._lock_file.close()
        self._lock_file = None

    def __del__(self):
        self.close()


def validate_round_state(state, round_id, role, active_states):
    terminal_states = ('succeeded', 'failed')
    if (not isinstance(state, dict) or
            type(state.get('schema_version')) is not int or
            state.get('schema_version') != 1 or
            state.get('round_id') != round_id or
            state.get('role') != role or
            state.get('state') not in active_states + terminal_states):
        raise FLRuntimeError('round_state_corrupted', '轮次状态结构无效')
    if state['state'] == 'succeeded' and role == 'server':
        participant_node_ids = state.get('participant_node_ids')
        committed_node_ids = state.get('committed_update_node_ids')
        if (not isinstance(participant_node_ids, list) or
                any(type(node_id) is not int or node_id <= 0
                    for node_id in participant_node_ids) or
                participant_node_ids != sorted(set(participant_node_ids)) or
                committed_node_ids != participant_node_ids):
            raise FLRuntimeError('round_state_corrupted', '成功轮次参与集合无效')
    if state['state'] == 'failed':
        if (not isinstance(state.get('error_code'), str) or
                not state['error_code'] or
                not isinstance(state.get('error_message'), str)):
            raise FLRuntimeError('round_state_corrupted', '失败轮次错误无效')
        node_id = state.get('node_id')
        if node_id is not None and (type(node_id) is not int or node_id <= 0):
            raise FLRuntimeError('round_state_corrupted', '失败轮次节点标识无效')


def recover_round_states(work_dir, role):
    rounds_dir = os.path.join(work_dir, 'rounds')
    try:
        names = os.listdir(rounds_dir)
    except FileNotFoundError:
        return None
    except OSError as exc:
        raise FLRuntimeError(
            'round_state_corrupted', '无法扫描轮次状态') from exc

    active_states = {
        'server': ('publishing_model', 'waiting_for_updates'),
        'client': ('model_received', 'submitting_update'),
    }
    terminal_states = ('succeeded', 'failed')
    terminal_by_id = {}
    restarted = []
    for name in names:
        round_dir = os.path.join(rounds_dir, name)
        if not os.path.isdir(round_dir):
            raise FLRuntimeError('round_state_corrupted', '轮次目录结构无效')
        try:
            round_id = str(uuid.UUID(name))
        except (ValueError, TypeError, AttributeError) as exc:
            raise FLRuntimeError('round_state_corrupted', '轮次目录标识无效') from exc
        if round_id != name:
            raise FLRuntimeError('round_state_corrupted', '轮次目录标识无效')
        state_path = os.path.join(round_dir, 'round-state.json')
        try:
            state = read_json(state_path)
        except FLRuntimeError as exc:
            raise FLRuntimeError('round_state_corrupted', '无法读取轮次状态') from exc
        validate_round_state(state, round_id, role, active_states[role])
        if state['state'] in terminal_states:
            terminal_by_id[round_id] = state
            continue
        previous_state = state['state']
        state.update({
            'state': 'failed',
            'error_code': 'runtime_restarted',
            'error_message': 'Runtime 重启终结了未完成轮次',
            'restart_previous_state': previous_state,
        })
        write_json_atomic(state_path, state)
        terminal_by_id[round_id] = state
        restarted.append(state)

    current_path = os.path.join(work_dir, 'current-round.json')
    if not os.path.exists(current_path):
        if len(restarted) == 1:
            return restarted[0]
        if len(restarted) > 1:
            raise FLRuntimeError(
                'round_state_corrupted', '无法确定重启前当前轮次')
        return None
    try:
        current = read_json(current_path)
        round_id = str(uuid.UUID(current['round_id']))
    except (FLRuntimeError, KeyError, ValueError, TypeError, AttributeError) as exc:
        raise FLRuntimeError('round_state_corrupted', '当前轮次指针无效') from exc
    if current != {
            'schema_version': 1, 'role': role, 'round_id': round_id}:
        raise FLRuntimeError('round_state_corrupted', '当前轮次指针无效')
    try:
        return terminal_by_id[round_id]
    except KeyError as exc:
        raise FLRuntimeError('round_state_corrupted', '当前轮次状态不存在') from exc


def write_current_round(work_dir, role, round_id):
    write_json_atomic(os.path.join(work_dir, 'current-round.json'), {
        'schema_version': 1,
        'role': role,
        'round_id': round_id,
    })


class ServerRuntime(object):
    def __init__(self, work_dir, participant_node_ids, max_update_size_bytes, transport):
        self.work_dir = os.path.abspath(work_dir)
        if isinstance(participant_node_ids, int):
            participant_node_ids = (participant_node_ids,)
        participant_node_ids = tuple(participant_node_ids)
        if (not participant_node_ids or
                any(not isinstance(node_id, int) or isinstance(node_id, bool) or
                    node_id <= 0 for node_id in participant_node_ids) or
                len(set(participant_node_ids)) != len(participant_node_ids)):
            raise FLRuntimeError('invalid_configuration', '参与节点集合无效')
        self.participant_node_ids = tuple(sorted(participant_node_ids))
        self.max_update_size_bytes = max_update_size_bytes
        self.transport = transport
        self._owner = WorkDirLock(self.work_dir)
        try:
            recovered_state = recover_round_states(self.work_dir, 'server')
        except Exception:
            self._owner.close()
            raise
        self._condition = threading.Condition()
        self._round_id = None
        self._round_dir = None
        self._downlink_completed = False
        self._failure = None
        self._state = None
        self._recovered_participant_node_ids = None
        self._round_result_consumed = False
        self._publish_active = False
        self._wait_for_updates_active = False
        if recovered_state is not None:
            self._restore_terminal_state(recovered_state)

    def close(self):
        self._owner.close()

    def _restore_terminal_state(self, state):
        round_id = state['round_id']
        if state['state'] == 'succeeded':
            participant_node_ids = state.get('participant_node_ids')
            committed_node_ids = state.get('committed_update_node_ids')
            if (not isinstance(participant_node_ids, list) or
                    any(type(node_id) is not int or node_id <= 0
                        for node_id in participant_node_ids) or
                    participant_node_ids != sorted(set(participant_node_ids)) or
                    committed_node_ids != participant_node_ids):
                self._owner.close()
                raise FLRuntimeError(
                    'round_state_corrupted', '成功轮次参与集合无效')
            self._recovered_participant_node_ids = tuple(participant_node_ids)
            self._downlink_completed = True
        elif state['state'] == 'failed':
            if (not isinstance(state.get('error_code'), str) or
                    not isinstance(state.get('error_message'), str)):
                self._owner.close()
                raise FLRuntimeError('round_state_corrupted', '失败轮次错误无效')
            self._failure = FLRuntimeError(
                state['error_code'], state['error_message'],
                round_id=round_id, node_id=state.get('node_id'))
        else:
            self._owner.close()
            raise FLRuntimeError('round_state_corrupted', '当前轮次不是终态')
        self._round_id = round_id
        self._round_dir = os.path.join(self.work_dir, 'rounds', round_id)
        self._state = state['state']

    def publish_model(self, model_path):
        self._require_ready()
        with self._condition:
            if self._publish_active:
                raise FLRuntimeError('operation_in_progress', '已有模型发布正在进行')
            if self._round_id is not None:
                if self._state in ('succeeded', 'failed'):
                    if not self._round_result_consumed:
                        raise FLRuntimeError('round_result_pending', '轮次结果尚未消费')
                else:
                    raise FLRuntimeError('round_in_progress', '已有活动轮次')
            self._publish_active = True

        try:
            return self._publish_model(model_path)
        finally:
            with self._condition:
                self._publish_active = False

    def _publish_model(self, model_path):
        round_id = str(uuid.uuid4())
        round_dir = os.path.join(self.work_dir, 'rounds', round_id)
        model_target = os.path.join(round_dir, 'model.bin')
        manifest_path = os.path.join(round_dir, 'model.manifest.json')

        with self._condition:
            os.makedirs(round_dir)
            self._round_id = round_id
            self._round_dir = round_dir
            self._downlink_completed = False
            self._failure = None
            self._round_result_consumed = False
            self._write_state('publishing_model')

        try:
            try:
                write_current_round(self.work_dir, 'server', round_id)
            except Exception as exc:
                self._fail_round(
                    'state_persistence_failed', '当前轮次指针持久化失败')
                raise self._failure from exc
            size_bytes, digest = archive_file(model_path, model_target)
            manifest = {
                'schema_version': 1,
                'artifact_type': 'model',
                'round_id': round_id,
                'size_bytes': size_bytes,
                'sha256': digest,
                'participant_node_ids': list(self.participant_node_ids),
            }
            write_json_atomic(manifest_path, manifest)
            self.transport.install_round(
                round_id=round_id,
                participant_node_ids=self.participant_node_ids,
                round_dir=round_dir,
                max_update_size_bytes=self.max_update_size_bytes,
                failure_callback=self.report_update_failure,
            )
            self.transport.publish_model(round_id, model_target, manifest_path)
        except FLRuntimeError as exc:
            if self._failure is None:
                self._fail_round('model_publish_failed', '模型发布失败')
            with self._condition:
                self._round_result_consumed = True
            raise self._failure or exc
        except Exception as exc:
            self._fail_round('transport_failed', 'UFTP 下行失败')
            with self._condition:
                self._round_result_consumed = True
            raise self._failure from exc

        with self._condition:
            if self._failure is not None:
                self._round_result_consumed = True
                raise self._failure
            self._downlink_completed = True
            self._write_state('waiting_for_updates')
            self._condition.notify_all()
        self._complete_round_if_ready(round_id, round_dir)
        return None

    def wait_for_updates(self):
        self._require_ready()
        with self._condition:
            if self._wait_for_updates_active:
                raise FLRuntimeError('operation_in_progress', '已有 update 等待正在进行')
            if self._round_id is None:
                raise FLRuntimeError('no_active_round', '没有活动轮次')
            self._wait_for_updates_active = True
            round_id = self._round_id
            round_dir = self._round_dir

        try:
            if (self._state == 'succeeded' and
                    self._recovered_participant_node_ids is not None):
                result = self._rebuild_terminal_updates(round_id, round_dir)
            else:
                result = self._wait_for_complete_updates(round_id, round_dir)
            with self._condition:
                self._round_result_consumed = True
            return result
        except FLRuntimeError:
            with self._condition:
                self._round_result_consumed = True
            raise
        finally:
            with self._condition:
                self._wait_for_updates_active = False

    def _wait_for_complete_updates(self, round_id, round_dir):
        while True:
            with self._condition:
                if self._failure is not None:
                    raise self._failure
            update_paths = self._complete_round_if_ready(round_id, round_dir)
            if update_paths is not None:
                return MappingProxyType(update_paths)
            self.transport.wait_for_update(0.1)

    def _rebuild_terminal_updates(self, round_id, round_dir):
        update_paths = {}
        node_ids = self._recovered_participant_node_ids
        for node_id in node_ids:
            update_dir = os.path.join(round_dir, 'updates', str(node_id))
            manifest_path = os.path.join(update_dir, 'update.manifest.json')
            update_path = os.path.abspath(os.path.join(update_dir, 'update.bin'))
            if not os.path.isfile(manifest_path) or not os.path.isfile(update_path):
                raise FLRuntimeError(
                    'round_artifacts_removed', '轮次托管交付物已被移除',
                    round_id=round_id)
            try:
                manifest = read_json(manifest_path)
                self._validate_update_manifest(manifest, round_id, node_id)
                if os.path.getsize(update_path) != manifest['size_bytes']:
                    raise OSError('update size mismatch')
            except (FLRuntimeError, OSError) as exc:
                raise FLRuntimeError(
                    'round_artifacts_corrupted', '轮次托管交付物已损坏',
                    round_id=round_id, node_id=node_id) from exc
            update_paths[node_id] = update_path
        return MappingProxyType(update_paths)

    def _complete_round_if_ready(self, round_id, round_dir):
        with self._condition:
            if not self._downlink_completed:
                return None
        update_paths = {}
        for node_id in self.participant_node_ids:
            update_dir = os.path.join(round_dir, 'updates', str(node_id))
            manifest_path = os.path.join(update_dir, 'update.manifest.json')
            if not os.path.isfile(manifest_path):
                return None
            update_paths[node_id] = os.path.abspath(
                os.path.join(update_dir, 'update.bin'))

        try:
            for node_id, update_path in update_paths.items():
                manifest_path = os.path.join(
                    round_dir, 'updates', str(node_id), 'update.manifest.json')
                manifest = read_json(manifest_path)
                self._validate_update_manifest(manifest, round_id, node_id)
                validate_artifact(update_path, manifest['size_bytes'], manifest['sha256'])
        except FLRuntimeError as exc:
            self._fail_round(exc.error_code, exc.error_message)
            raise
        with self._condition:
            if self._failure is not None:
                raise self._failure
            node_ids = list(self.participant_node_ids)
            self._write_state(
                'succeeded',
                participant_node_ids=node_ids,
                committed_update_node_ids=node_ids,
            )
        return update_paths

    def report_update_failure(self, round_id, node_id, error_code, error_message):
        with self._condition:
            if (round_id != self._round_id or
                    node_id not in self.participant_node_ids or
                    self._state in ('succeeded', 'failed') or
                    self._failure is not None):
                return
            self._failure = FLRuntimeError(
                error_code, error_message, round_id=round_id, node_id=node_id)
            self._write_state(
                'failed', error_code=error_code, error_message=error_message,
                node_id=node_id)
            self._condition.notify_all()
        if self._publish_active:
            self.transport.cancel_downlink()

    def _validate_update_manifest(self, manifest, round_id, node_id):
        expected = {
            'schema_version': 1,
            'artifact_type': 'update',
            'round_id': round_id,
            'node_id': node_id,
        }
        if any(manifest.get(key) != value for key, value in expected.items()):
            raise FLRuntimeError('invalid_manifest', 'update manifest 身份无效')
        if type(manifest.get('size_bytes')) is not int or manifest['size_bytes'] < 0:
            raise FLRuntimeError('invalid_manifest', 'update manifest 大小无效')
        if (not isinstance(manifest.get('sha256'), str) or
                len(manifest['sha256']) != 64):
            raise FLRuntimeError('invalid_manifest', 'update manifest 摘要无效')

    def _require_ready(self):
        if not self.transport.ready:
            raise FLRuntimeError('transport_not_ready', 'Transport 尚未 ready')

    def _write_state(self, state, **extra):
        self._state = state
        value = {
            'schema_version': 1,
            'round_id': self._round_id,
            'role': 'server',
            'state': state,
        }
        value.update(extra)
        write_json_atomic(os.path.join(self._round_dir, 'round-state.json'), value)

    def _fail_round(self, error_code, error_message):
        with self._condition:
            if (self._round_dir is None or self._failure is not None or
                    self._state == 'succeeded'):
                return
            self._failure = FLRuntimeError(
                error_code, error_message, round_id=self._round_id)
            self._write_state('failed', error_code=error_code, error_message=error_message)
            self._condition.notify_all()


class ClientRuntime(object):
    def __init__(self, work_dir, node_id, max_update_size_bytes, transport):
        self.work_dir = os.path.abspath(work_dir)
        if not isinstance(node_id, int) or isinstance(node_id, bool) or node_id <= 0:
            raise FLRuntimeError('invalid_configuration', '本机节点标识无效')
        self.node_id = node_id
        self.max_update_size_bytes = max_update_size_bytes
        self.transport = transport
        self._owner = WorkDirLock(self.work_dir)
        try:
            recover_round_states(self.work_dir, 'client')
        except Exception:
            self._owner.close()
            raise
        self._condition = threading.Condition()
        self._round_id = None
        self._round_dir = None
        self._state = None
        self._wait_for_model_active = False
        self._submit_update_active = False

    def close(self):
        self._owner.close()

    def wait_for_model(self):
        self._require_ready()
        with self._condition:
            if self._wait_for_model_active:
                raise FLRuntimeError('operation_in_progress', '已有模型等待正在进行')
            if self._round_id is not None and self._state not in ('succeeded', 'failed'):
                raise FLRuntimeError('round_in_progress', '已有活动轮次')
            self._wait_for_model_active = True
        try:
            return self._wait_for_model()
        finally:
            with self._condition:
                self._wait_for_model_active = False

    def _wait_for_model(self):
        candidate_dir = self.transport.wait_for_model_candidate()
        manifest = read_json(os.path.join(candidate_dir, 'model.manifest.json'))
        self._validate_model_manifest(manifest)
        source_model = os.path.join(candidate_dir, 'model.bin')
        validate_artifact(source_model, manifest['size_bytes'], manifest['sha256'])

        round_id = manifest['round_id']
        round_dir = os.path.join(self.work_dir, 'rounds', round_id)
        model_path = os.path.abspath(os.path.join(round_dir, 'model.bin'))
        size_bytes, digest = archive_file(source_model, model_path)
        if size_bytes != manifest['size_bytes'] or digest != manifest['sha256']:
            raise FLRuntimeError('artifact_integrity_failed', '模型归档校验失败')
        write_json_atomic(os.path.join(round_dir, 'model.manifest.json'), manifest)

        self._round_id = round_id
        self._round_dir = round_dir
        self._write_state('model_received')
        try:
            write_current_round(self.work_dir, 'client', round_id)
        except Exception as exc:
            self._write_failed_state(
                'state_persistence_failed', '当前轮次指针持久化失败')
            raise FLRuntimeError(
                'state_persistence_failed', '当前轮次指针持久化失败',
                round_id=round_id, node_id=self.node_id) from exc
        return model_path

    def submit_update(self, update_path):
        self._require_ready()
        with self._condition:
            if self._submit_update_active:
                raise FLRuntimeError('operation_in_progress', '已有 update 提交正在进行')
            if self._round_id is None:
                raise FLRuntimeError('no_active_round', '没有活动轮次')
            if self._state == 'succeeded':
                raise FLRuntimeError('round_already_succeeded', '轮次已成功')
            if self._state == 'failed':
                raise FLRuntimeError('round_already_failed', '轮次已失败')
            if self._state != 'model_received':
                raise FLRuntimeError('round_in_progress', '轮次正在提交 update')
            self._submit_update_active = True
        try:
            update_path = inspect_artifact(
                update_path, self.max_update_size_bytes)
            self._write_state('submitting_update')
            return self._submit_update(update_path)
        except FLRuntimeError as exc:
            if self._state == 'submitting_update':
                self._write_failed_state(exc.error_code, exc.error_message)
            raise
        except Exception as exc:
            error = FLRuntimeError(
                'update_submit_failed', 'update 提交失败',
                round_id=self._round_id, node_id=self.node_id)
            self._write_failed_state(error.error_code, error.error_message)
            raise error from exc
        finally:
            with self._condition:
                self._submit_update_active = False

    def _submit_update(self, update_path):
        managed_update = os.path.join(self._round_dir, 'update.bin')
        size_bytes, digest = archive_file(
            update_path, managed_update, self.max_update_size_bytes)
        manifest = {
            'schema_version': 1,
            'artifact_type': 'update',
            'round_id': self._round_id,
            'node_id': self.node_id,
            'size_bytes': size_bytes,
            'sha256': digest,
        }
        write_json_atomic(os.path.join(self._round_dir, 'update.manifest.json'), manifest)
        self.transport.submit_update(
            self._round_id, self.node_id, managed_update, size_bytes, digest)
        self._write_state('succeeded')
        return None

    def _validate_model_manifest(self, manifest):
        try:
            parsed = uuid.UUID(manifest['round_id'])
        except (KeyError, ValueError, TypeError, AttributeError) as exc:
            raise FLRuntimeError('invalid_manifest', '模型轮次标识无效') from exc
        expected = {
            'schema_version': 1,
            'artifact_type': 'model',
            'round_id': str(parsed),
        }
        if any(manifest.get(key) != value for key, value in expected.items()):
            raise FLRuntimeError('invalid_manifest', '模型 manifest 身份无效')
        participant_node_ids = manifest.get('participant_node_ids')
        if (not isinstance(participant_node_ids, list) or
                any(not isinstance(node_id, int) or isinstance(node_id, bool)
                    for node_id in participant_node_ids) or
                len(set(participant_node_ids)) != len(participant_node_ids)):
            raise FLRuntimeError('invalid_manifest', '模型 manifest 参与集合无效')
        if self.node_id not in participant_node_ids:
            raise FLRuntimeError('model_not_for_this_node', '模型不属于本节点')
        if type(manifest.get('size_bytes')) is not int or manifest['size_bytes'] < 0:
            raise FLRuntimeError('invalid_manifest', '模型 manifest 大小无效')
        if (not isinstance(manifest.get('sha256'), str) or
                len(manifest['sha256']) != 64):
            raise FLRuntimeError('invalid_manifest', '模型 manifest 摘要无效')

    def _require_ready(self):
        if not self.transport.ready:
            raise FLRuntimeError('transport_not_ready', 'Transport 尚未 ready')

    def _write_state(self, state, **extra):
        self._state = state
        value = {
            'schema_version': 1,
            'round_id': self._round_id,
            'role': 'client',
            'state': state,
        }
        value.update(extra)
        write_json_atomic(os.path.join(self._round_dir, 'round-state.json'), value)

    def _write_failed_state(self, error_code, error_message):
        self._write_state(
            'failed', error_code=error_code, error_message=error_message,
            node_id=self.node_id)
