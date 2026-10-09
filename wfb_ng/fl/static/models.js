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
      operation.append(remove);
      row.append(item('td', model.filename), digest, item('td', String(model.size_bytes)),
        item('td', model.created_at), item('td', model.referenced ? '已引用 · 禁止删除' : '未引用'), operation);
      return row;
    }));
  }
  async function refresh() {
    const version = ++listVersion;
    byId('list-status').textContent = hasList ? '正在刷新清单，当前清单可能已过时…' : '正在读取清单…';
    try {
      const body = await request('/api/v1/models', {cache:'no-store'});
      if (version !== listVersion) return;
      // Preserve the authoritative ordering returned by the server.
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
    ++listVersion; // Invalidate reads that started before this write.
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
  byId('refresh-models').addEventListener('click', refresh);
  refresh();
})();
