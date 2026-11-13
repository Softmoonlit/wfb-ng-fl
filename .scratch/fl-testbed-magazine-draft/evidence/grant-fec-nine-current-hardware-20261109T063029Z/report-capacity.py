"""只读归并当前硬件的容量样本，不连接设备、不执行试验。"""
import csv
import io
import json
import re
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent
EVIDENCE = ROOT.parent
NODES = {1, 2, 3, 5, 6, 7, 8, 9, 10}
SIZE = 44770302
SHA = '4b55cee2f0bdcfbbf610167a505efee0e2c50e3306fed02bda3613807c234e96'


def read(path):
    return json.loads(path.read_text())


def atomic(path, content):
    temporary = path.with_name(path.name + '.tmp')
    temporary.write_text(content)
    temporary.replace(path)


def server_proof(group, grant, guard):
    """只计服务端实际完整文件的尺寸和SHA证据，不将进度或HTTP响应当作校验。"""
    verified, sources, observed = set(), [], False
    for path in group.rglob('*exact-uftp-kernel-files.json'):
        if f'-g{grant}-d{guard}-model-' not in path.name:
            continue
        for row in read(path).get('rows', []):
            if row.get('host') != 'fl00' or row.get('returncode') != 0:
                continue
            observed = True
            sources.append(str(path.relative_to(EVIDENCE)))
            for artifact in row.get('result', {}).get('files', []):
                match = re.search(r'/updates/(\d+)/update\.bin$', artifact['path'])
                if (match and artifact.get('size_bytes') == SIZE
                        and artifact.get('sha256') == SHA):
                    verified.add(int(match.group(1)))
    return verified, sources, observed


def finish(row):
    if row['accepted_capacity_pass']:
        assert row['server_verified_uploads'] == len(NODES)
        assert row['downlink_s'] is not None and row['uplink_s'] is not None
        row['transfer_phase_total_s'] = row['downlink_s'] + row['uplink_s']
        row['aggregate_uplink_mbps'] = len(NODES) * SIZE * 8 / row['uplink_s'] / 1e6
    else:
        row['transfer_phase_total_s'] = None
        row['aggregate_uplink_mbps'] = None
    return row


def main():
    anchor = EVIDENCE / 'server-fl10-repeat-swap-20261109T055853Z/B2-final-summary.json'
    previous = read(anchor)
    model = previous['model']
    recovery = previous['final_independent_recovery']
    anchor_ok = all([model['raw_flags']['transfer_verified'], model['independent_restore_ok'],
                     model['independent_ready_ok'], recovery['exact_cleanup_ok'],
                     model['all_nine_server_upload_files_independently_size_sha_verified']])
    rows = [finish(dict(fec_n=14, grant_ms=30, guard_ms=5,
                        downlink_s=model['DLduration'], uplink_s=model['ULduration'],
                        server_verified_uploads=9, accepted_capacity_pass=anchor_ok,
                        status='一次容量通过' if anchor_ok else '未通过',
                        raw_flags=model['raw_flags'],
                        sources=[str(anchor.relative_to(EVIDENCE))]))]
    for name in ['fec14-remaining/after-storage-models', 'fec12', 'fec14-matched-completion']:
        group = ROOT / name
        matrix_path = group / 'matrix.json'
        continuation = group / 'after-cold-audit-recovery/matrix.json'
        if continuation.exists():
            # 冷切换零传输失败保留原档，容量样本从独立续跑矩阵归并。
            matrix_path = continuation
        if not matrix_path.exists():
            continue
        matrix = read(matrix_path)
        for candidate in matrix['candidates']:
            if candidate['mode'] != 'model':
                continue
            grant, guard = candidate['grant_duration_ms'], candidate['guard_interval_ms']
            verified, sources, observed = server_proof(group, grant, guard)
            restored = all(candidate.get(key) is True for key in [
                'independent_exact_cleanup_ok', 'independent_restore_ok', 'independent_ready_ok'])
            accepted = candidate['transfer_verified'] and restored and verified == NODES
            if accepted:
                status = ('一次容量通过' if candidate.get('restore_ok') is True
                          else '传输完成，原恢复审计未过，独立恢复通过')
            elif candidate.get('outcome') == 'transfer_failed_recovered':
                if (candidate.get('DLduration') is None
                        and candidate.get('currentphase') in ['prepare', 'acquire_radio']):
                    status = '控制准入失败，未发模型，已恢复'
                else:
                    status = '传输失败，已恢复'
            elif candidate['stage'] == 'skipped':
                status = '小文件未过，未执行模型'
            elif candidate['execute_count']:
                status = '停止或尚未完成' if matrix['status'] == 'stopped' else '运行中'
            else:
                status = '待执行'
            downlink_complete = {f'fl{node:02d}' for node in NODES} <= set(
                candidate.get('completed_hosts', {}).get('publish', []))
            rows.append(finish(dict(
                fec_n=candidate['fec_n'], grant_ms=grant, guard_ms=guard,
                downlink_s=candidate.get('DLduration') if downlink_complete else None,
                uplink_s=candidate.get('ULduration'),
                server_verified_uploads=len(verified) if observed else None,
                accepted_capacity_pass=accepted, status=status,
                raw_flags={key: candidate.get(key) for key in [
                    'transfer_verified', 'cleanup_ok', 'restore_ok']},
                sources=[str(matrix_path.relative_to(EVIDENCE)), *sources])))
    result = dict(generated_utc=datetime.now(timezone.utc).isoformat(), participants=sorted(NODES),
                  payload_size=SIZE, payload_sha256=SHA,
                  scope='同硬件、不同时刻的单次容量样本；不等于最终连续两次验收或统计显著最优',
                  excluded_infrastructure_failures='fec14-remaining首批两次UFTP8空间拒绝；原记录保留，不作为吞吐样本',
                  duration_scope='下行及上行阶段，包含相应协议与校验开销，不含整个编排的切频和收尾',
                  upload_proof_scope='运行中或待执行行尚无服务端独立文件快照时留空，不把缺少观测解释为零字节',
                  candidates=rows)
    atomic(ROOT / 'capacity-snapshot.json', json.dumps(result, ensure_ascii=False, indent=2) + '\n')
    columns = ['fec_n', 'grant_ms', 'guard_ms', 'status', 'downlink_s', 'uplink_s',
               'transfer_phase_total_s', 'server_verified_uploads', 'aggregate_uplink_mbps']
    stream = io.StringIO()
    writer = csv.DictWriter(stream, fieldnames=columns, extrasaction='ignore')
    writer.writeheader()
    writer.writerows(rows)
    atomic(ROOT / 'capacity-snapshot.csv', stream.getvalue())
    print(json.dumps(dict(capacity_passes=sum(r['accepted_capacity_pass'] for r in rows),
                          rows=len(rows), statuses=[(r['fec_n'], r['grant_ms'], r['guard_ms'], r['status'])
                                                   for r in rows]), ensure_ascii=False))


if __name__ == '__main__':
    main()
