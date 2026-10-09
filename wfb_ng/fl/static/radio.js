(() => {
  'use strict';
  const byId = id => document.getElementById(id);
  const fields = ['channel', 'radio_txpower_dbm', 'downlink_mcs', 'uplink_mcs', 'uftp_rate_kbps'];
  const states = {idle:'待命', starting:'启动中', preparing:'准备中', running:'运行中', aborting:'中止 / 恢复中', radio_error:'射频错误 RADIO_ERROR', unavailable:'不可用'};
  const recoveries = {not_required:'无需恢复', recovering:'正在恢复待命', ready:'已恢复待命', blocked:'恢复受阻'};
  const results = {none:'未执行', running:'执行中', succeeded:'成功', failed:'失败', aborted:'已急停'};
  let snapshot = null, rateBounds = {}, initialized = false, connected = false;
  let confirmation = null, busy = false, stopping = false, fetching = false;
  let readVersion = 0, editVersion = 0, lastRefresh = 0;

  function describe(config) {
    return config ? `信道 ${config.channel} · ${config.radio_txpower_dbm} dBm · 下行 MCS ${config.downlink_mcs} · 上行 MCS ${config.uplink_mcs} · UFTP ${config.uftp_rate_kbps} kbps` : '射频配置未确认';
  }
  function showError(id, message) {
    byId(id).textContent = message;
    byId(id).hidden = false;
  }
  async function request(path, body) {
    const options = body === undefined ? {cache:'no-store'} : {
      method:'POST', headers:{'Content-Type':'application/json'}, body:JSON.stringify(body)};
    const response = await fetch(path, options);
    const data = await response.json();
    if (!response.ok) throw new Error(`${data.error.code}: ${data.error.message}`);
    return data;
  }
  function clearConfirmation(message = '请校验配置，再明确点击应用。') {
    confirmation = null;
    byId('confirm-risk').checked = false;
    byId('validated-config').textContent = message;
    byId('confirmation-status').textContent = '';
  }
  function activeJob() {
    if (!snapshot) return null;
    return snapshot.current_job || (snapshot.job && snapshot.job.execution_result === 'running' ? snapshot.job : null);
  }
  function blockers() {
    if (!connected || !snapshot) return ['状态尚未连接或可能已过时，请刷新状态。'];
    const reasons = [];
    if (snapshot.server.state !== 'idle') reasons.push('Server 尚未待命，禁止应用射频。');
    if (activeJob()) reasons.push('作业正在运行，禁止应用射频。');
    const recovery = snapshot.recovery_state || (snapshot.job && snapshot.job.recovery_state);
    if (recovery && !['ready', 'not_required'].includes(recovery)) reasons.push('资源尚未恢复待命，禁止应用射频。');
    for (const blocker of snapshot.server.start_blockers || []) {
      if (blocker.code !== 'RADIO_NOT_READY') reasons.push(`${blocker.message} (${blocker.code})`);
    }
    return reasons;
  }
  function outside(config, bounds) {
    return bounds && (config.uftp_rate_kbps < bounds.min_rate_kbps || config.uftp_rate_kbps > bounds.max_rate_kbps);
  }
  function render() {
    if (confirmation && Date.now() >= confirmation.expiresAt) clearConfirmation('确认已过期，请重新校验配置。');
    const bounds = rateBounds[byId('downlink_mcs').value];
    byId('rate-range').textContent = bounds ? `安全范围 ${bounds.min_rate_kbps}–${bounds.max_rate_kbps} kbps · 推荐 ${bounds.default_rate_kbps} kbps` : '该 MCS 的速率范围尚不可用，请刷新状态。';
    const localRisk = outside({uftp_rate_kbps:Number(byId('uftp_rate_kbps').value)}, bounds);
    byId('rate-warning').textContent = confirmation && confirmation.warning ? confirmation.warning :
      localRisk ? '强告警：UFTP 速率超出安全范围，可能造成丢包；校验后必须勾选风险确认。' : '';
    byId('rate-warning').hidden = !byId('rate-warning').textContent;
    byId('risk-confirmation').hidden = !confirmation || !confirmation.risky;
    byId('confirm-risk').disabled = busy || !confirmation || !confirmation.risky;
    const reasons = blockers();
    byId('apply-blockers').textContent = reasons.length ? reasons.join(' · ') : '集群空闲；应用仍由 Server 校验最新状态并协调节点。';
    byId('validate').disabled = busy || !!reasons.length;
    byId('apply').disabled = busy || !!reasons.length || !confirmation || (confirmation.risky && !byId('confirm-risk').checked);
    byId('emergency-stop').disabled = stopping || !activeJob();
    if (confirmation) byId('confirmation-status').textContent = `已校验 · 确认剩余 ${Math.max(0, Math.ceil((confirmation.expiresAt - Date.now()) / 1000))} 秒`;
  }
  function renderSnapshot(state) {
    if (snapshot && snapshot.instance_id !== state.instance_id) {
      byId('instance-notice').hidden = false;
      byId('instance-notice').textContent = '服务已重启，前次作业状态不可用；请重新校验配置。';
    }
    if (confirmation && (confirmation.instance_id !== state.instance_id || confirmation.state_version !== state.state_version)) {
      clearConfirmation('Server 状态已变化，请重新校验配置。');
    }
    snapshot = state;
    const job = state.job;
    byId('server-state').textContent = states[state.server.state] || state.server.state;
    byId('resources').textContent = `TUN ${state.server.tun.is_active ? '就绪' : '未就绪'} · 链路 ${state.server.link_process.running ? '运行' : '未运行'}`;
    byId('job-result').textContent = `${job ? job.job_id + ' · ' : ''}${results[job ? job.execution_result : 'none'] || '未知'}`;
    const recovery = state.recovery_state || (job && job.recovery_state) || 'not_required';
    byId('recovery-state').textContent = recoveries[recovery] || recovery;
    byId('job-error').textContent = job ? [job.error, job.recovery_error, job.reason].filter(Boolean).join(' · ') : '';
    byId('snapshot-time').textContent = `状态版本 ${state.state_version} · 更新于 ${state.generated_at}`;
    byId('nodes').replaceChildren(...state.nodes.map(node => {
      const row = document.createElement('li');
      row.textContent = `节点 ${node.node_id} · ${node.online ? '在线' : '离线'} · ${node.state} · ${node.readiness}${node.error_code ? ' · ' + node.error_code : ''}`;
      return row;
    }));
  }
  async function refresh() {
    if (busy) return;
    const version = ++readVersion;
    fetching = true;
    lastRefresh = Date.now();
    try {
      const body = await request('/api/v1/radio/config');
      if (version !== readVersion) return;
      rateBounds = body.rate_bounds;
      renderSnapshot(body.snapshot);
      byId('current-config').textContent = describe(body.config);
      if (!initialized) {
        if (body.config && editVersion === 0) fields.forEach(field => {byId(field).value = String(body.config[field]);});
        initialized = true;
      }
      connected = true;
      byId('connection-error').hidden = true;
    } catch (error) {
      if (version !== readVersion) return;
      connected = false;
      clearConfirmation('连接中断，确认已清除；重连后请重新校验。');
      showError('connection-error', `状态可能已过时。${error.message}。请重试刷新状态。`);
    } finally {
      if (version === readVersion) fetching = false;
      render();
    }
  }
  function readConfig() {
    const config = {};
    fields.forEach(field => {
      const value = byId(field).value.trim();
      if (!/^\d+$/.test(value) || !Number.isSafeInteger(Number(value))) throw new Error('请完整填写五项整数参数。');
      config[field] = Number(value);
    });
    if (![149,153,157,165].includes(config.channel)) throw new Error('仅允许信道 149、153、157、165；禁止 Channel 161。');
    if (config.radio_txpower_dbm < 10 || config.radio_txpower_dbm > 20) throw new Error('发射功率必须为 10–20 dBm。');
    if (![3,4,5,6].includes(config.downlink_mcs) || ![3,4,5,6].includes(config.uplink_mcs)) throw new Error('上下行 MCS 必须为 3–6。');
    if (config.uftp_rate_kbps <= 0) throw new Error('UFTP 速率必须为正整数。');
    if (!rateBounds[config.downlink_mcs]) throw new Error('当前 MCS 范围不可用，请刷新状态。');
    return config;
  }
  byId('radio-form').addEventListener('submit', async event => {
    event.preventDefault();
    if (busy || blockers().length) return;
    clearConfirmation();
    byId('action-error').hidden = true;
    let config;
    try {config = readConfig();} catch (error) {showError('action-error', error.message);render();return;}
    const revision = editVersion;
    const instance = snapshot.instance_id, stateVersion = snapshot.state_version;
    busy = true;
    ++readVersion;
    fetching = false;
    byId('action-status').textContent = '正在校验配置…';
    render();
    try {
      const body = await request('/api/v1/radio/config/validate', {config});
      if (revision !== editVersion) {byId('action-status').textContent = '输入已变化，请重新校验。';return;}
      if (body.confirmation.instance_id !== instance || body.confirmation.state_version !== stateVersion) {
        throw new Error('Server 状态已变化，请刷新后重新校验。');
      }
      confirmation = {...body.confirmation, expiresAt:Date.now() + body.confirmation.expires_in_seconds * 1000,
        risky:!!body.warning || !!outside(body.config, body.rate_bounds), warning:body.warning};
      byId('validated-config').textContent = describe(body.config);
      byId('action-status').textContent = '校验完成。请核对规范配置，再明确点击应用。';
    } catch (error) {
      clearConfirmation();
      byId('action-status').textContent = '';
      showError('action-error', `校验失败。${error.message}`);
    } finally {busy = false;render();}
  });
  byId('apply').addEventListener('click', async () => {
    render();
    if (byId('apply').disabled) return;
    const token = confirmation.token, confirmRisk = confirmation.risky && byId('confirm-risk').checked;
    clearConfirmation('应用请求已发出；再次应用需要重新校验。');
    busy = true;
    ++readVersion;
    fetching = false;
    byId('action-error').hidden = true;
    byId('action-status').textContent = '正在应用射频，请等待事务结果…';
    render();
    try {
      const result = await request('/api/v1/radio/config/apply', {confirmation_token:token, confirm_risk:!!confirmRisk});
      byId('action-status').textContent = `射频事务结果：${result.status || '已返回'}${result.error_message ? ' · ' + result.error_message : ''}`;
      byId('transaction-result').textContent = JSON.stringify(result, null, 2);
      busy = false;
      await refresh();
    } catch (error) {
      byId('action-status').textContent = '未确认应用结果；请刷新状态，重新校验后再操作。';
      showError('action-error', `应用失败。${error.message}`);
      connected = false;
    } finally {busy = false;render();}
  });
  byId('emergency-stop').addEventListener('click', async () => {
    const job = activeJob();
    if (stopping || !job) return;
    stopping = true;
    clearConfirmation('急停已请求，请等待资源恢复并重新校验。');
    byId('stop-error').hidden = true;
    byId('stop-status').textContent = '正在发送急停请求…';
    render();
    try {
      await request(`/api/v1/jobs/${encodeURIComponent(job.job_id)}/abort`, {reason:'operator_abort'});
      byId('stop-status').textContent = '急停请求已受理；资源是否恢复以最新状态为准。';
      await refresh();
    } catch (error) {
      byId('stop-status').textContent = '尚未确认急停受理，可重试。';
      showError('stop-error', `急停请求失败。${error.message}`);
    } finally {stopping = false;render();}
  });
  fields.forEach(field => {
    const changed = () => {++editVersion;clearConfirmation('输入已变化，请重新校验配置。');render();};
    byId(field).addEventListener('input', changed);
    byId(field).addEventListener('change', changed);
  });
  byId('confirm-risk').addEventListener('change', render);
  byId('refresh').addEventListener('click', refresh);
  render();
  refresh();
  setInterval(() => {
    render();
    if (!fetching && !busy && Date.now() - lastRefresh >= 3000) refresh();
  }, 1000);
})();
