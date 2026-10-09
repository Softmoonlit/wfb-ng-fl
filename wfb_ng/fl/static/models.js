(() => {
  'use strict';
  const byId = id => document.getElementById(id);
  const item = (tag, text) => {
    const node = document.createElement(tag);
    node.textContent = text;
    return node;
  };
  let listVersion = 0;
  let mutating = false;
  let models = [];
  let hasList = false;
  let currentActiveJob = null;

  const states = {
    starting: '启动中', idle: '待命', preparing: '准备中', running: '运行中',
    aborting: '中止 / 恢复中', radio_error: '射频错误', unavailable: '不可用', offline: '离线',
    hunting: '寻频中', error: '错误', ready: '可用', stopped: '已停止',
    management_web_unavailable: '管理 Web 不可用'
  };

  function showError(id, message) {
    byId(id).textContent = message;
    byId(id).hidden = false;
  }
  async function request(path, options) {
    const response = await fetch(path, options);
    const body = await response.json();
    if (!response.ok) {
      throw new Error(`${body.error.code}: ${body.error.message}`);
    }
    return body;
  }

  function render() {
    byId('models').replaceChildren(...models.map(model => {
      const row = document.createElement('tr');
      const digest = document.createElement('td');
      digest.append(item('code', model.sha256));
      const operation = document.createElement('td');
      const remove = item('button', '删除');
      remove.type = 'button';
      remove.className = 'btn btn-sm';
      remove.disabled = model.referenced || mutating;
      remove.title = model.referenced ? '模型已被作业引用，禁止删除' : '删除此模型';
      remove.setAttribute('aria-label', `删除模型 ${model.filename} · ${model.sha256}`);
      remove.addEventListener('click', () => {
        if (model.referenced || mutating) return;
        if (!confirm(`确认删除模型「${model.filename}」？\nSHA-256: ${model.sha256}\n删除后需重新上传才能使用。`)) return;
        return mutate(async () => {
          await request(`/api/v1/models/${model.sha256}`, {method:'DELETE'});
          return `已删除模型 ${model.sha256}`;
        });
      });

      const selectBtn = item('a', '选择用于作业');
      selectBtn.href = `/?model=${encodeURIComponent(model.sha256)}`;
      selectBtn.className = 'btn btn-sm btn-subtle';
      if (selectBtn.style) selectBtn.style.marginLeft = '6px';
      selectBtn.title = '选定此模型并在监控页创建同步作业';

      operation.append(remove, selectBtn);
      row.append(item('td', model.filename), digest, item('td', String(model.size_bytes)),
        item('td', model.created_at), item('td', model.referenced ? '已引用 · 禁止删除' : '未引用'), operation);
      return row;
    }));
  }

  async function updateHUD() {
    try {
      const state = await request('/api/v1/state', {cache: 'no-store'});
      const server = state.server;
      currentActiveJob = state.current_job;

      if (byId('hud-server-state')) {
        const text = states[server.state] || server.state;
        byId('hud-server-state').textContent = text;
        byId('hud-server-state').className = `badge ${server.state === 'idle' ? 'badge-green' : server.state === 'running' ? 'badge-blue' : server.state === 'starting' || server.state === 'preparing' ? 'badge-yellow' : 'badge-red'}`;
      }

      if (byId('hud-radio')) {
        const radioConfig = server.radio;
        byId('hud-radio').textContent = radioConfig ? `CH${radioConfig.channel} · ${radioConfig.radio_txpower_dbm}dBm · MCS${radioConfig.downlink_mcs}` : '未确认';
      }

      if (byId('hud-job')) {
        if (currentActiveJob && server.state === 'running') {
          byId('hud-job').textContent = `轮次 ${currentActiveJob.current_round || 0}/${currentActiveJob.rounds} · 运行中`;
        } else {
          const recent = state.recent_job || state.job;
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

      const abortBtn = byId('abort-job');
      if (abortBtn) {
        abortBtn.disabled = !currentActiveJob || server.state !== 'running';
      }
    } catch {
      // In isolated tests where /api/v1/state is not mocked, do not fail
    }
  }

  async function refresh() {
    const version = ++listVersion;
    byId('list-status').textContent = hasList ? '正在刷新清单，当前清单可能已过时…' : '正在读取清单…';
    try {
      const body = await request('/api/v1/models', {cache:'no-store'});
      if (version !== listVersion) return;
      models = body.models;
      hasList = true;
      render();
      byId('list-status').textContent = models.length ? `共 ${models.length} 个模型` : '暂无模型，请上传文件。';
      byId('list-error').hidden = true;
    } catch (error) {
      if (version !== listVersion) return;
      byId('list-status').textContent = hasList ? '显示上次清单。' : '尚未取得模型清单。';
      showError('list-error', `清单可能已过时。${error.message}。请重试刷新清单。`);
    }
  }

  async function mutate(action) {
    if (mutating) return;
    mutating = true;
    ++listVersion;
    byId('upload').disabled = true;
    byId('model-file').disabled = true;
    byId('action-error').hidden = true;
    byId('action-status').textContent = '操作进行中，请等待 Server 返回结果…';
    render();
    try {
      byId('action-status').textContent = await action();
      await refresh();
    } catch (error) {
      byId('action-status').textContent = '';
      byId('list-status').textContent = hasList ? '显示上次清单，可刷新确认当前状态。' : '尚未取得模型清单。';
      showError('action-error', `操作失败。${error.message}。可重试操作或刷新清单确认 Server 状态。`);
    } finally {
      mutating = false;
      byId('upload').disabled = false;
      byId('model-file').disabled = false;
      render();
    }
  }

  byId('upload-form').addEventListener('submit', event => {
    event.preventDefault();
    if (mutating) return;
    const files = byId('model-file').files;
    if (files.length !== 1 || files[0].size === 0 || files[0].size > 1073741824) {
      byId('action-status').textContent = '';
      showError('action-error', '请选择一个非空文件，单文件不能超过 1 GiB。');
      return;
    }
    const file = files[0];
    return mutate(async () => {
      const data = new FormData();
      data.append('file', file);
      const body = await request('/api/v1/models', {method:'POST', body:data});
      byId('model-file').value = '';
      return `${body.deduplicated ? '去重成功，模型已存在' : '上传成功'} · ${body.model.filename} · SHA-256 ${body.model.sha256}`;
    });
  });

  const abortBtn = byId('abort-job');
  if (abortBtn) {
    abortBtn.addEventListener('click', async () => {
      if (!currentActiveJob) return;
      if (typeof confirm === 'function' && !confirm('确认急停当前作业？这将立即中止当前传输并启动资源恢复。')) {
        return;
      }
      abortBtn.disabled = true;
      try {
        await request(`/api/v1/jobs/${encodeURIComponent(currentActiveJob.job_id)}/abort`, {
          method: 'POST',
          headers: {'Content-Type': 'application/json'},
          body: JSON.stringify({reason: 'operator_requested'})
        });
        await updateHUD();
      } catch (err) {
        showError('action-error', `急停未受理。${err.message}`);
      }
    });
  }

  byId('refresh-models').addEventListener('click', refresh);
  refresh();
  if (typeof window !== 'undefined') {
    updateHUD();
    setInterval(updateHUD, 3000);
  }
})();
