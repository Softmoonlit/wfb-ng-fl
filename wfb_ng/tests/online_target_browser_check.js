// 对已启动的本地控制台执行真实 Chromium 页面检查；仅使用 Node 内建 CDP 通路。
const assert = require('node:assert/strict');
(async () => {
  const [debugUrl, consoleUrl] = process.argv.slice(2);
  const targets = await (await fetch(`${debugUrl}/json/list`)).json();
  const target = targets.find(entry => entry.type === 'page');
  const socket = new WebSocket(target.webSocketDebuggerUrl);
  await new Promise(resolve => socket.addEventListener('open', resolve, {once: true}));
  let sequence = 0;
  const pending = new Map();
  socket.addEventListener('message', event => {
    const message = JSON.parse(event.data);
    if (pending.has(message.id)) {
      const {resolve, reject} = pending.get(message.id);
      pending.delete(message.id);
      if (message.error) reject(new Error(JSON.stringify(message.error)));
      else resolve(message.result);
    }
  });
  const call = (method, params = {}) => new Promise((resolve, reject) => {
    const id = ++sequence; pending.set(id, {resolve, reject});
    socket.send(JSON.stringify({id, method, params}));
  });
  const evaluate = async expression => {
    const result = await call('Runtime.evaluate', {expression, returnByValue: true, awaitPromise: true});
    if (result.exceptionDetails) throw new Error(JSON.stringify(result.exceptionDetails));
    return result.result.value;
  };
  try {
    await call('Page.navigate', {url: consoleUrl});
    const waitFor = async expression => {
      for (let attempt = 0; attempt < 100; attempt++) {
        if (await evaluate(expression)) return;
        await new Promise(resolve => setImmediate(resolve));
      }
      throw new Error(`页面条件未满足：${expression}`);
    };
    await waitFor("document.querySelectorAll('#job-target-nodes input').length === 2");
    assert.deepEqual(await evaluate("[...document.querySelectorAll('#job-target-nodes input')].map(n => [n.type, n.value, n.checked, n.disabled])"),
      [['checkbox', '1', false, false], ['checkbox', '2', false, true]]);
    assert.equal(await evaluate("document.querySelector('#start-job').disabled"), true);
    assert.equal(await evaluate("document.querySelector('input[type=text][name=target_nodes]') === null"), true);
    assert.equal(await evaluate("document.querySelector('#job-rounds-input').value"), '5');
    await evaluate("document.querySelector('#job-target-nodes input').click()");
    assert.equal(await evaluate("document.querySelector('#start-job').disabled"), false);
    const stateRequestsBeforeRefresh = await evaluate("performance.getEntriesByType('resource').filter(entry => entry.name.endsWith('/api/v1/state')).length");
    await evaluate("document.querySelector('#refresh').click()");
    await waitFor(`performance.getEntriesByType('resource').filter(entry => entry.name.endsWith('/api/v1/state')).length > ${stateRequestsBeforeRefresh}`);
    await waitFor("document.querySelector('#job-target-nodes input').checked");
    await evaluate("document.querySelector('#job-form').requestSubmit()");
    await waitFor("document.querySelector('#job-form-error').textContent.includes('节点 1')");
    assert.match(await evaluate("document.querySelector('#job-form-error').textContent"), /目标客户端不可用.*节点 1.*未就绪/);
    assert.equal(await evaluate("document.querySelector('#job-id').textContent"), '暂无作业');
    await call('Page.reload');
    await waitFor("document.querySelectorAll('#job-target-nodes input').length === 2");
    assert.equal(await evaluate("document.querySelector('#job-target-nodes input').checked"), false);
    console.log('Chromium 真实页面：在线过滤、禁选、初始空选、明确勾选、刷新保留、HTTP 整体拒绝、重载清空通过');
  } finally {socket.close();}
})().catch(error => {console.error(error); process.exitCode = 1;});
