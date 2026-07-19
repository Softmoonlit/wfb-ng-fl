#!/usr/bin/env python
# -*- coding: utf-8 -*-

from .runtime import ClientRuntime, ServerRuntime
from .transport import ClientTransport, ServerTransport


def _as_tuple(value):
    if isinstance(value, int):
        return (value,)
    return tuple(value)


class ServerRole(object):
    def __init__(self, work_dir, participant_node_id, participant_uftp_uid,
                 server_uftp_uid, uftp_port, http_port,
                 max_update_size_bytes):
        participant_node_ids = _as_tuple(participant_node_id)
        participant_uftp_uids = _as_tuple(participant_uftp_uid)
        if len(participant_node_ids) != len(participant_uftp_uids):
            raise ValueError('参与节点与 UFTP UID 数量不一致')
        if (not participant_uftp_uids or
                len(set(participant_uftp_uids)) != len(participant_uftp_uids)):
            raise ValueError('UFTP UID 集合无效')
        self.transport = ServerTransport(
            participant_uftp_uids=participant_uftp_uids,
            server_uftp_uid=server_uftp_uid,
            uftp_port=uftp_port,
            http_port=http_port,
        )
        self.runtime = ServerRuntime(
            work_dir=work_dir,
            participant_node_ids=participant_node_ids,
            max_update_size_bytes=max_update_size_bytes,
            transport=self.transport,
        )

    @property
    def http_address(self):
        return self.transport.http_address

    def start(self):
        try:
            self.transport.start()
        except Exception:
            self.close()
            raise
        return self.runtime

    def close(self):
        self.transport.close()

    def __enter__(self):
        self.start()
        return self

    def __exit__(self, exc_type, exc_value, traceback):
        self.close()


class ClientRole(object):
    def __init__(self, work_dir, node_id, uftp_uid, uftp_port,
                 server_http_address, max_update_size_bytes):
        self.transport = ClientTransport(
            work_dir=work_dir,
            uftp_uid=uftp_uid,
            uftp_port=uftp_port,
            server_http_address=server_http_address,
        )
        self.runtime = ClientRuntime(
            work_dir=work_dir,
            node_id=node_id,
            max_update_size_bytes=max_update_size_bytes,
            transport=self.transport,
        )

    def start(self):
        try:
            self.transport.start()
        except Exception:
            self.close()
            raise
        return self.runtime

    def close(self):
        self.transport.close()

    def __enter__(self):
        self.start()
        return self

    def __exit__(self, exc_type, exc_value, traceback):
        self.close()
