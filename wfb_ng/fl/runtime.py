#!/usr/bin/env python
# -*- coding: utf-8 -*-

import os
import threading
import uuid
from types import MappingProxyType

from .artifacts import archive_file, read_json, validate_artifact, write_json_atomic
from .errors import FLRuntimeError


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
        self._condition = threading.Condition()
        self._round_id = None
        self._round_dir = None
        self._downlink_completed = False
        self._failure = None
        self._state = None
        self._round_result_consumed = False
        self._publish_active = False
        self._wait_for_updates_active = False

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
        if not isinstance(manifest.get('size_bytes'), int):
            raise FLRuntimeError('invalid_manifest', 'update manifest 大小无效')
        if not isinstance(manifest.get('sha256'), str):
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
        self._condition = threading.Condition()
        self._round_id = None
        self._round_dir = None
        self._state = None
        self._wait_for_model_active = False
        self._submit_update_active = False

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
            return self._submit_update(update_path)
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
        self._write_state('submitting_update')
        try:
            self.transport.submit_update(
                self._round_id, self.node_id, managed_update, size_bytes, digest)
        except FLRuntimeError as exc:
            self._write_failed_state(exc.error_code, exc.error_message)
            raise
        except Exception as exc:
            error = FLRuntimeError(
                'update_submit_failed', 'update 提交失败',
                round_id=self._round_id, node_id=self.node_id)
            self._write_failed_state(error.error_code, error.error_message)
            raise error from exc
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
        if not isinstance(manifest.get('size_bytes'), int):
            raise FLRuntimeError('invalid_manifest', '模型 manifest 大小无效')
        if not isinstance(manifest.get('sha256'), str):
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
