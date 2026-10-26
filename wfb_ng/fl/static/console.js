(() => {
  'use strict';
  const byId = id => document.getElementById(id);
  const states = {
    starting: '启动中', idle: '待命', preparing: '准备中', running: '运行中',
    aborting: '中止 / 恢复中', radio_error: '射频错误', unavailable: '不可用', offline: '离线',
    hunting: '寻频中', error: '错误', ready: '可用', stopped: '已停止',
    management_web_unavailable: '管理 Web 不可用'
  };
  const results = {none: '未执行', running: '执行中', succeeded: '成功', failed: '失败', aborted: '已急停'};
  const recovery = {not_required: '无需恢复', recovering: '正在恢复待命', ready: '已恢复待命', blocked: '恢复受阻'};
  const phases = {
    preparing: '准备任务', publishing_model: '下发模型', waiting_updates: '等待 update',
    recovering: '恢复待命', ready: '已待命', recovery_blocked: '恢复受阻'
  };

  let fetching = false;
  let lastState = null;
  let sseConnected = false;
  let httpConnected = false;
  let stateFresh = false;
  let startSubmitting = false;
  let pendingJobId = null;
  let instanceId = null;
  const selectedNodes = new Set();
  const item = (tag, text) => { const node = document.createElement(tag); node.textContent = text; return node; };

  function canStart() {
    return Boolean(lastState && stateFresh && !pendingJobId && (httpConnected || sseConnected) && selectedNodes.size &&
      lastState.server.can_start_job && lastState.server.state === 'idle' && !lastState.current_job);
  }

  function updateStartButton() {
    const button = byId('start-job');
    if (button) button.disabled = startSubmitting || !canStart();
  }

  function renderTargets(state) {
    const online = state.nodes.filter(node => node.online).sort((a, b) => a.node_id - b.node_id);
    const removed = [];
    for (const id of selectedNodes) {
      const node = online.find(candidate => candidate.node_id === id);
      if (!node || !node.can_select) {
        selectedNodes.delete(id);
        const reason = node ? node.selection_blockers.map(blocker => blocker.message).join('；') : '节点已离线';
        removed.push(`节点 ${id}：${reason}`);
      }
    }
    if (removed.length) {
      byId('selection-notice').textContent = `已取消失效选择，请重新确认：${removed.join('；')}`;
      byId('selection-notice').hidden = false;
    }
    byId('job-target-nodes').replaceChildren(...(online.length ? online.map(node => {
      const label = document.createElement('label');
      const checkbox = document.createElement('input');
      checkbox.type = 'checkbox';
      checkbox.name = 'target_nodes';
      checkbox.value = String(node.node_id);
      checkbox.checked = selectedNodes.has(node.node_id);
      checkbox.disabled = !node.can_select || Boolean(state.current_job) || startSubmitting;
      checkbox.addEventListener('change', () => {
        if (checkbox.disabled) return;
        if (checkbox.checked) selectedNodes.add(node.node_id);
        else selectedNodes.delete(node.node_id);
        updateStartButton();
      });
      const reasons = node.selection_blockers.map(blocker => blocker.message).join('；');
      label.append(checkbox, item('span', `节点 ${node.node_id} · ${states[node.state] || node.state} · 基础 ${node.readiness}${reasons ? ` · ${reasons}` : ''}`));
      return label;
    }) : [item('p', '暂无在线客户端')]));
    return online;
  }

  function formatSeconds(sec) {
    if (isNaN(sec) || sec < 0) return '00:00';
    const total = Math.floor(sec);
    const m = Math.floor(total / 60);
    const s = total % 60;
    return `${String(m).padStart(2, '0')}:${String(s).padStart(2, '0')}`;
  }

  function updateTimers() {
    if ((!sseConnected && !httpConnected) || !lastState) return;
    const job = lastState.current_job || (lastState.job && lastState.job.execution_result === 'running' ? lastState.job : null);
    if (job && lastState.server && lastState.server.state === 'running') {
      const now = Date.now() / 1000;
      const jobElapsedSeconds = job.started_at ? Math.max(0, now - job.started_at) : 0;
      const roundElapsedSeconds = job.round_started_at ? Math.max(0, now - job.round_started_at) : 0;
      const phaseElapsedSeconds = job.phase_started_at ? Math.max(0, now - job.phase_started_at) : (job.round_started_at ? Math.max(0, now - job.round_started_at) : jobElapsedSeconds);
      const jobElapsedStr = formatSeconds(jobElapsedSeconds);
      const roundElapsedStr = formatSeconds(roundElapsedSeconds);
      const phaseElapsedStr = formatSeconds(phaseElapsedSeconds);
      if (byId('job-elapsed')) byId('job-elapsed').textContent = jobElapsedStr;
      if (byId('round-elapsed')) byId('round-elapsed').textContent = roundElapsedStr;
      if (byId('phase-elapsed')) byId('phase-elapsed').textContent = phaseElapsedStr;
      if (byId('hud-job')) {
        const phText = phases[job.server_phase] || job.server_phase || '准备任务';
        byId('hud-job').textContent = `轮次 ${job.current_round || 0}/${job.rounds} · ${phText} · ${phaseElapsedStr}`;
      }
    } else {
      if (byId('job-elapsed')) byId('job-elapsed').textContent = '00:00';
      if (byId('round-elapsed')) byId('round-elapsed').textContent = '00:00';
      if (byId('phase-elapsed')) byId('phase-elapsed').textContent = '00:00';
      if (byId('hud-job')) {
        const recent = lastState.recent_job || lastState.job;
        if (recent && recent.execution_result === 'aborted') {
          byId('hud-job').textContent = recent.recovery_state === 'ready' ? '作业已急停 · 链路已就绪' : '作业已急停 · 恢复待命中';
        } else if (recent && recent.execution_result === 'failed') {
          byId('hud-job').textContent = recent.recovery_state === 'ready' ? '作业失败 · 链路已就绪' : '作业失败 · 恢复待命中';
        } else if (recent && recent.execution_result === 'succeeded') {
          byId('hud-job').textContent = '作业成功 · 已完成';
        } else {
          byId('hud-job').textContent = '空闲待命';
        }
      }
    }
  }

  function render(state, fresh = true) {
    if (lastState && instanceId === state.instance_id && state.state_version < lastState.state_version) return;
    if (fresh) stateFresh = true;
    if (instanceId && instanceId !== state.instance_id) {
      byId('instance-notice').hidden = false;
      selectedNodes.clear();
      byId('selection-notice').textContent = '服务实例已变化，目标选择已清空，请重新勾选。';
      byId('selection-notice').hidden = false;
    }
    instanceId = state.instance_id;
    lastState = state;
    if (pendingJobId && [state.current_job, state.recent_job, state.job]
        .some(job => job && job.job_id === pendingJobId)) {
      pendingJobId = null;
    }

    const server = state.server;
    const serverStateText = states[server.state] || server.state;
    byId('server-state').textContent = serverStateText;
    if (byId('hud-server-state')) {
      byId('hud-server-state').textContent = serverStateText;
      byId('hud-server-state').className = `badge ${server.state === 'idle' ? 'badge-green' : server.state === 'running' ? 'badge-blue' : server.state === 'starting' || server.state === 'preparing' ? 'badge-yellow' : 'badge-red'}`;
    }

    byId('web-state').textContent = (states[server.management_web.status] || server.management_web.status) +
      (server.management_web.error ? ` · ${server.management_web.error}` : '');
    byId('resources').textContent = `TUN ${server.tun.is_active ? '就绪' : '未就绪'} · 链路 ${server.link_process.running ? '运行' : '未运行'}`;
    byId('snapshot-time').textContent = `状态版本 ${state.state_version} · 更新于 ${state.generated_at}`;

    byId('start-blockers').replaceChildren(...(server.start_blockers.length ?
      server.start_blockers.map(blocker => item('li', `${blocker.message} (${blocker.code})`)) :
      [item('li', '当前基础启动条件满足；创建作业时仍须校验目标节点与模型。')]));

    const job = state.job;
    byId('job-id').textContent = job ? job.job_id : '暂无作业';
    byId('job-result').textContent = results[job ? job.execution_result : 'none'];
    if (byId('job-result-badge')) {
      const res = job ? job.execution_result : 'none';
      byId('job-result-badge').textContent = results[res] || '未执行';
      byId('job-result-badge').className = `badge ${res === 'running' ? 'badge-blue' : res === 'succeeded' ? 'badge-green' : res === 'aborted' || res === 'failed' ? 'badge-red' : 'badge-gray'}`;
    }
    byId('job-recovery').textContent = recovery[job ? job.recovery_state : 'not_required'];
    byId('job-phase').textContent = job ? (phases[job.server_phase] || job.server_phase || 'preparing') : '—';
    byId('job-rounds').textContent = job ? `${job.current_round || 0} / ${job.rounds}（已完成 ${job.rounds_completed || 0}）` : '—';
    byId('job-config').textContent = job ? `${job.rounds || '未知'} 轮 · 节点 ${(job.target_nodes || []).join(', ')} · 模型 ${job.model_sha256 || '未知'}` : '—';
    const participants = job ? job.participants : [];
    byId('job-participants').textContent = participants.length ? participants.map(participant => {
      const disconnections = (participant.disconnection_events || []).map(event => event.message).join('；');
      const fact = participant.online ? (states[participant.state] || participant.state) : '离线';
      return `节点 ${participant.node_id} · ${fact}${participant.error_code ? ` · ${participant.error_code}` : ''}${disconnections ? ` · 失联事件：${disconnections}` : ''}`;
    }).join('；') : '—';
    byId('job-error').textContent = job ? [job.error, job.recovery_error, job.reason].filter(Boolean).join(' · ') : '';

    const abortButton = byId('abort-job');
    if (abortButton) abortButton.disabled = !state.current_job || server.state !== 'running';

    const radio = server.radio;
    byId('radio').textContent = radio ? `信道 ${radio.channel} · ${radio.radio_txpower_dbm} dBm · 下行 MCS ${radio.downlink_mcs} · 上行 MCS ${radio.uplink_mcs}` : '射频配置未确认';
    if (byId('hud-radio')) {
      byId('hud-radio').textContent = radio ? `CH${radio.channel} · ${radio.radio_txpower_dbm}dBm · MCS${radio.downlink_mcs}` : '未确认';
    }

    const onlineNodes = renderTargets(state);
    byId('nodes').replaceChildren(...onlineNodes.map(node => {
      const row = document.createElement('tr');
      row.append(...[node.node_id, node.online ? '在线' : '离线', states[node.state] || node.state,
        node.readiness, node.error_code || '—'].map(value => item('td', String(value))));
      return row;
    }));

    byId('events').replaceChildren(...(state.events.length ? state.events.map(event =>
      item('li', `${event.timestamp} · ${event.message || event.type}`)) : [item('li', '暂无关键事件')]));

    // 启动门禁同时依赖最新管理状态和操作者明确勾选。
    updateStartButton();

    const formStatus = byId('job-form-status');
    if (formStatus && !startSubmitting) {
      if (state.current_job) {
        formStatus.textContent = '当前作业正在运行中，冲突操作已禁用。';
      } else if (server.start_blockers.length) {
        formStatus.textContent = '启动门禁未满足：' + server.start_blockers.map(b => b.message).join('；');
      } else {
        formStatus.textContent = selectedNodes.size ? '已选择目标，可以提交；数据与 GPU 将在受理后检查。' : '请勾选目标客户端；基础就绪不代表本次训练准备通过。';
      }
    }

    updateTimers();
  }

  async function loadModels() {
    const select = byId('job-model');
    if (!select) return;

    let targetDigest = '';
    if (typeof window !== 'undefined' && window.location && window.location.search) {
      const params = new URLSearchParams(window.location.search);
      targetDigest = (params.get('model') || '').trim();
    }

    try {
      const response = await fetch('/api/v1/models', {cache: 'no-store'});
      if (!response.ok) return;
      const data = await response.json();
      const models = data.models || [];
      select.replaceChildren(
        item('option', '-- 请选择模型制品 --'),
        ...models.map(m => {
          const opt = document.createElement('option');
          opt.value = m.sha256;
          opt.textContent = `${m.filename} (${m.sha256.substring(0, 10)}... · ${m.size_bytes} 字节)`;
          if (m.sha256 === targetDigest) opt.selected = true;
          return opt;
        })
      );
      select.value = targetDigest || (models.length > 0 ? models[0].sha256 : '');
    } catch {
      // In isolated tests where models endpoint is unmocked
    }
  }

  async function refresh() {
    if (fetching) return;
    fetching = true;
    try {
      const response = await fetch('/api/v1/state', {cache: 'no-store'});
      const state = await response.json();
      if (!response.ok) throw new Error(`${state.error.code}: ${state.error.message}`);
      httpConnected = true;
      render(state);
      byId('connection-error').hidden = true;
    } catch (error) {
      httpConnected = false;
      if (!sseConnected) stateFresh = false;
      byId('connection-error').textContent = sseConnected ?
        `HTTP 刷新失败，继续使用实时状态。${error.message}` :
        `无法刷新，状态可能已过时，已暂停启动。${error.message}。请重试刷新状态。`;
      byId('connection-error').hidden = false;
      updateStartButton();
    } finally {
      fetching = false;
    }
  }

  // Bind refresh
  const refreshBtn = byId('refresh');
  if (refreshBtn) refreshBtn.addEventListener('click', refresh);

  // Bind abort button
  const abortButton = byId('abort-job');
  if (abortButton) abortButton.addEventListener('click', async () => {
    const job = lastState && lastState.current_job;
    if (!job) return;
    if (typeof confirm === 'function' && !confirm('确认急停当前作业？这将立即中止当前传输并启动资源恢复。')) {
      return;
    }
    abortButton.disabled = true;
    try {
      const response = await fetch(`/api/v1/jobs/${encodeURIComponent(job.job_id)}/abort`, {
        method: 'POST', headers: {'Content-Type': 'application/json'},
        body: JSON.stringify({reason: 'operator_requested'})
      });
      const result = await response.json();
      if (!response.ok) throw new Error(`${result.error.code}: ${result.error.message}`);
      byId('connection-error').hidden = true;
      if (byId('job-error')) byId('job-error').textContent = '急停请求已受理，正在等待 Client 与链路资源恢复待命…';
      await refresh();
    } catch (error) {
      byId('connection-error').textContent = `急停未受理。${error.message}`;
      byId('connection-error').hidden = false;
    }
  });

  // Bind job creation form
  const jobForm = byId('job-form');
  if (jobForm) {
    jobForm.addEventListener('submit', async event => {
      event.preventDefault();
      if (startSubmitting) return;

      const modelSelect = byId('job-model');
      const roundsInput = byId('job-rounds-input');
      const errorBox = byId('job-form-error');
      const statusBox = byId('job-form-status');

      if (errorBox) errorBox.hidden = true;

      const modelSha256 = (modelSelect ? modelSelect.value : '').trim();
      if (!modelSha256) {
        if (errorBox) { errorBox.textContent = '请选择一个模型制品'; errorBox.hidden = false; }
        return;
      }

      const targetNodes = [...selectedNodes].sort((a, b) => a - b);
      if (!canStart()) {
        if (errorBox) { errorBox.textContent = '作业未提交：请确认管理状态有效、启动门禁满足且已勾选目标客户端。'; errorBox.hidden = false; }
        return;
      }

      const rounds = Number(roundsInput ? roundsInput.value : '5');
      if (!Number.isInteger(rounds) || rounds < 1) {
        if (errorBox) { errorBox.textContent = '同步轮数必须为大于 0 的整数'; errorBox.hidden = false; }
        return;
      }

      startSubmitting = true;
      if (byId('start-job')) byId('start-job').disabled = true;
      if (statusBox) statusBox.textContent = '正在提交并启动同步作业…';

      let requestAccepted = false;
      let acceptedStatus = '';
      try {
        const idempotencyKey = Array.from(crypto.getRandomValues(new Uint8Array(16)),
          value => value.toString(16).padStart(2, '0')).join('');
        const response = await fetch('/api/v1/jobs', {
          method: 'POST',
          headers: {
            'Content-Type': 'application/json',
            'Idempotency-Key': idempotencyKey
          },
          body: JSON.stringify({
            model_sha256: modelSha256,
            target_nodes: targetNodes,
            rounds: rounds
          })
        });
        const result = await response.json();
        if (!response.ok) {
          const reasons = Object.entries(result.error.details?.unready_nodes || {})
            .map(([id, detail]) => `节点 ${id}：${detail.reason}`).join('；');
          throw new Error(`${result.error.code}: ${result.error.message}${reasons ? `；${reasons}` : ''}`);
        }
        requestAccepted = true;
        pendingJobId = result.job_id;
        stateFresh = false;
        acceptedStatus = `作业 ${result.job_id} 已受理，准备结果请查看作业详情。`;
        if (statusBox) statusBox.textContent = acceptedStatus;
        await refresh();
      } catch (err) {
        if (requestAccepted) {
          acceptedStatus = '作业已受理，但状态快照暂不可用；请刷新核对当前作业。';
          if (statusBox) statusBox.textContent = acceptedStatus;
        } else {
          if (errorBox) { errorBox.textContent = `提交未获受理确认，请刷新核对作业状态。${err.message}`; errorBox.hidden = false; }
          if (statusBox) statusBox.textContent = '';
          await refresh();
        }
      } finally {
        startSubmitting = false;
        if (lastState) render(lastState, false);
        if (acceptedStatus && statusBox) statusBox.textContent = acceptedStatus;
        updateStartButton();
      }
    });
  }

  // Initial load
  loadModels();
  refresh();

  // SSE stream
  if (typeof EventSource !== 'undefined') {
    const stream = new EventSource('/api/v1/events');
    stream.addEventListener('snapshot', event => {
      try {
        const state = JSON.parse(event.data);
        sseConnected = true;
        render(state);
        byId('connection-error').hidden = true;
      } catch (error) {
        sseConnected = false;
        updateStartButton();
        byId('connection-error').textContent = `状态帧非法。${error.message}`;
        byId('connection-error').hidden = false;
      }
    });
    stream.addEventListener('operator-event', event => {
      if (event.lastEventId) byId('snapshot-time').textContent = `事件帧 ${event.lastEventId} · 等待状态快照`;
    });
    stream.onerror = () => {
      sseConnected = false;
      byId('connection-error').textContent = httpConnected ?
        '实时连接中断，继续使用 HTTP 轮询的最新状态。' :
        '管理连接中断，状态可能已过时，已暂停启动；正在重连。';
      byId('connection-error').hidden = false;
      updateStartButton();
    };
  }

  // Periodic polling and 1s timer ticks
  setInterval(updateTimers, 1000);
  setInterval(refresh, 3000);
})();
