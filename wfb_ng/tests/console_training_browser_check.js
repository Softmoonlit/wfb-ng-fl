// 工单07真实 Chromium 页面：评估表、固定参与者摘要、快照恢复，不触碰真实无线控制面。
const assert = require('node:assert/strict');

(async () => {
  const [debugUrl, consoleUrl] = process.argv.slice(2);
  const targets = await (await fetch(`${debugUrl}/json/list`)).json();
  const target = targets.find(entry => entry.type === 'page');
  assert.ok(target, 'Chromium CDP page target is required');
  const socket = new WebSocket(target.webSocketDebuggerUrl);
  await new Promise(resolve => socket.addEventListener('open', resolve, {once: true}));
  let sequence = 0;
  const pending = new Map();
  const eventWaiters = new Map();
  socket.addEventListener('message', event => {
    const message = JSON.parse(event.data);
    if (message.method && eventWaiters.has(message.method)) {
      const waiter = eventWaiters.get(message.method);
      eventWaiters.delete(message.method);
      waiter(message.params || {});
      return;
    }
    if (!pending.has(message.id)) return;
    const {resolve, reject} = pending.get(message.id);
    pending.delete(message.id);
    if (message.error) reject(new Error(JSON.stringify(message.error)));
    else resolve(message.result);
  });
  const call = (method, params = {}) => new Promise((resolve, reject) => {
    const id = ++sequence;
    pending.set(id, {resolve, reject});
    socket.send(JSON.stringify({id, method, params}));
  });
  const waitForEvent = method => new Promise(resolve => eventWaiters.set(method, resolve));
  const evaluate = async expression => {
    const result = await call('Runtime.evaluate', {
      expression, returnByValue: true, awaitPromise: true,
    });
    if (result.exceptionDetails) throw new Error(JSON.stringify(result.exceptionDetails));
    return result.result.value;
  };
  const waitFor = async expression => {
    const deadline = performance.now() + 5000;
    while (performance.now() < deadline) {
      if (await evaluate(expression)) return;
      await new Promise(resolve => setImmediate(resolve));
    }
    throw new Error(`页面条件未满足：${expression}`);
  };

  try {
    await call('Page.enable');
    await call('Network.enable');
    await call('Network.setBlockedURLs', {urls: ['*://*/api/v1/events*']});
    await call('Page.addScriptToEvaluateOnNewDocument', {source: "try { Object.defineProperty(window, 'EventSource', {configurable: true, value: undefined}); } catch (_) { window.EventSource = undefined; }"});
    const initialLoad = waitForEvent('Page.loadEventFired');
    await call('Page.navigate', {url: consoleUrl});
    await initialLoad;
    await waitFor("document.querySelector('#job-id') && document.querySelector('#job-id').textContent === 'job-07-browser'");
    await waitFor("document.querySelectorAll('#job-evaluation-rows tr').length === 3 && document.querySelectorAll('#job-participant-rows tr').length === 2");
    const snapshotJob = await evaluate("fetch('/api/v1/state').then(response => response.json()).then(state => state.job)");
    assert.equal(snapshotJob.recovery.state, 'unconfirmed');
    assert.equal(snapshotJob.recovery.recovery_state, 'unconfirmed');
    assert.equal(await evaluate("document.querySelector('#job-recovery').textContent"), '恢复未确认');
    const evaluationRows = await evaluate("[...document.querySelectorAll('#job-evaluation-rows tr')].map(row => [...row.children].map(cell => cell.textContent))");
    assert.deepEqual(evaluationRows.map(row => [row[0], row[1], row[2], row[3], row[4], row[5]]), [
      ['M0 初始', 'M0 · aaaaaaaaaaaa…', '2', '0', '未判定', '已取得'],
      ['M1', 'M1 · bbbbbbbbbbbb…', '0', '0', '未达标', '未确认'],
      ['M2', 'M2 · cccccccccccc…', '1.25', '0.75', '未达标', '已完成'],
    ]);
    assert.match(await evaluate("document.querySelector('#job-evaluation-note').textContent"), /^最终模型 c{12}…$/);
    assert.match(await evaluate("document.querySelector('#job-effect-result').textContent"), /未达标/);

    assert.equal(await evaluate("document.querySelectorAll('#job-participant-rows tr').length"), 2);
    const participantText = await evaluate("document.querySelector('#job-participant-rows').textContent");
    assert.match(participantText, /60000/);
    assert.match(participantText, /938/);
    assert.match(await evaluate("document.querySelector('#job-recovery-facts').textContent"), /资源恢复未确认/);
    assert.match(await evaluate("document.querySelector('#job-recovery-facts').textContent"), /Server 资源恢复尚未完成/);
    assert.match(await evaluate("document.querySelector('#job-recovery-facts').textContent"), /未确认恢复节点：2/);
    const jobsBeforeReload = await evaluate("performance.getEntriesByType('resource').filter(entry => new URL(entry.name).pathname.startsWith('/api/v1/jobs')).length");
    assert.equal(jobsBeforeReload, 0);
    assert.ok(await evaluate("performance.getEntriesByType('resource').some(entry => entry.name.endsWith('/api/v1/state'))"));
    await evaluate("document.body.dataset.wfbReloadSentinel = 'old'");
    const reloadLoad = waitForEvent('Page.loadEventFired');
    await call('Page.reload', {ignoreCache: true});
    await reloadLoad;
    await waitFor("document.body.dataset.wfbReloadSentinel !== 'old' && document.querySelector('#job-id') && document.querySelector('#job-id').textContent === 'job-07-browser'");
    await waitFor("document.querySelectorAll('#job-evaluation-rows tr').length === 3");
    assert.equal(await evaluate("performance.getEntriesByType('resource').filter(entry => new URL(entry.name).pathname.startsWith('/api/v1/jobs')).length"), 0);

    console.log('Chromium 真实页面：评估表、固定参与者摘要、未判定事实与重载快照恢复通过');
  } finally {
    socket.close();
  }
})().catch(error => {
  console.error(error);
  process.exitCode = 1;
});
