(() => {
  'use strict';
  const byId = id => document.getElementById(id);
  const states = {starting:'启动中', idle:'待命', preparing:'准备中', running:'运行中',
    aborting:'中止 / 恢复中', radio_error:'射频错误', unavailable:'不可用', offline:'离线',
    hunting:'寻频中', error:'错误', ready:'可用', stopped:'已停止',
    management_web_unavailable:'管理 Web 不可用'};
  const results = {none:'未执行', running:'执行中', succeeded:'成功', failed:'失败', aborted:'已急停'};
  const recovery = {not_required:'无需恢复', recovering:'正在恢复待命', ready:'已恢复待命', blocked:'恢复受阻'};
  let fetching = false;
  let instanceId = sessionStorage.getItem('wfb-console-instance');
  const item = (tag, text) => {const node=document.createElement(tag); node.textContent=text; return node;};
  function render(state) {
    if (instanceId && instanceId !== state.instance_id) byId('instance-notice').hidden = false;
    instanceId = state.instance_id;
    sessionStorage.setItem('wfb-console-instance', instanceId);
    const server = state.server;
    byId('server-state').textContent = states[server.state] || server.state;
    byId('web-state').textContent = (states[server.management_web.status] || server.management_web.status) +
      (server.management_web.error ? ` · ${server.management_web.error}` : '');
    byId('resources').textContent = `TUN ${server.tun.is_active ? '就绪' : '未就绪'} · 链路 ${server.link_process.running ? '运行' : '未运行'}`;
    byId('snapshot-time').textContent = `状态版本 ${state.state_version} · 更新于 ${state.generated_at}`;
    byId('start-blockers').replaceChildren(...(server.start_blockers.length ?
      server.start_blockers.map(blocker => item('li', `${blocker.message} (${blocker.code})`)) :
      [item('li', '当前基础启动条件满足；创建作业时仍须校验目标节点与模型。')]));
    const job=state.job;
    byId('job-id').textContent = job ? job.job_id : '暂无作业';
    byId('job-result').textContent = results[job ? job.execution_result : 'none'];
    byId('job-recovery').textContent = recovery[job ? job.recovery_state : 'not_required'];
    byId('job-phase').textContent = job ? (job.server_phase || 'preparing') : '—';
    byId('job-rounds').textContent = job ? `${job.current_round || 0} / ${job.rounds}（已完成 ${job.rounds_completed || 0}）` : '—';
    byId('job-config').textContent = job ? `${job.rounds || '未知'} 轮 · 节点 ${(job.target_nodes || []).join(', ')} · 模型 ${job.model_sha256 || '未知'}` : '—';
    byId('job-error').textContent = job ? [job.error, job.recovery_error, job.reason].filter(Boolean).join(' · ') : '';
    const radio=server.radio;
    byId('radio').textContent = radio ? `信道 ${radio.channel} · ${radio.radio_txpower_dbm} dBm · 下行 MCS ${radio.downlink_mcs} · 上行 MCS ${radio.uplink_mcs}` : '射频配置未确认';
    byId('nodes').replaceChildren(...state.nodes.map(node => {
      const row=document.createElement('tr');
      row.append(...[node.node_id, node.online ? '在线' : '离线', states[node.state] || node.state,
        node.readiness, node.error_code || '—'].map(value=>item('td', String(value))));
      return row;
    }));
    byId('events').replaceChildren(...(state.events.length ? state.events.map(event =>
      item('li', `${event.timestamp} · ${event.message || event.type}`)) : [item('li', '暂无关键事件')]));
  }
  async function refresh() {
    if (fetching) return;
    fetching = true;
    try {
      const response = await fetch('/api/v1/state', {cache:'no-store'});
      const state = await response.json();
      if (!response.ok) throw new Error(`${state.error.code}: ${state.error.message}`);
      render(state);
      byId('connection-error').hidden = true;
    } catch (error) {
      byId('connection-error').textContent = `无法刷新，状态可能已过时。${error.message}。请重试刷新状态。`;
      byId('connection-error').hidden = false;
    } finally {
      fetching = false;
    }
  }
  byId('refresh').addEventListener('click', refresh);
  refresh();
  // Issue 01 uses complete snapshots; the later SSE slice owns streaming.
  setInterval(refresh, 3000);
})();
