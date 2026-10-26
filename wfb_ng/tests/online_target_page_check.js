// 通过打包页面脚本的 DOM、HTTP 与 SSE 边界核验在线候选。
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const script = fs.readFileSync('wfb_ng/fl/static/console.js', 'utf8');
function element(tag) {
  return {tag, textContent: '', value: '', hidden: false, disabled: false, checked: false,
    children: [], listeners: {}, append(...nodes) {this.children.push(...nodes);},
    replaceChildren(...nodes) {this.children = nodes;},
    addEventListener(name, callback) {this.listeners[name] = callback;}};
}
function text(node) {return [node.textContent, ...node.children.map(text)].join(' ');}
const client = (id, extra = {}) => ({node_id: id, online: true, state: 'idle', readiness: 'READY',
  error_code: null, can_select: true, selection_blockers: [], ...extra});
const snapshot = (extra = {}) => ({instance_id: 'one', state_version: 1, generated_at: 'now',
  server: {state: 'idle', management_web: {status: 'ready'}, radio: null,
    can_start_job: true, start_blockers: [], tun: {is_active: true}, link_process: {running: true}},
  nodes: [client(3), client(1), client(2, {can_select: false, state: 'hunting', readiness: 'DISCOVERED',
    selection_blockers: [{code: 'NODE_NOT_IDLE', message: '节点正在寻频'}]}),
    client(4, {online: false, can_select: false})],
  job: null, current_job: null, recent_job: null, events: [], ...extra});
async function flush() {for (let i = 0; i < 20; i++) await Promise.resolve();}
function openPage(initial = snapshot()) {
  const elements = {'job-rounds-input': element('input')};
  const requests = [];
  let state = initial;
  let stateError = null;
  let postResult = null;
  let stream;
  const intervals = {};
  function EventSource() {stream = this; this.listeners = {};}
  EventSource.prototype.addEventListener = function(name, fn) {this.listeners[name] = fn;};
  const context = {document: {getElementById(id) {return elements[id] ||= element('div');}, createElement: element},
    fetch: async (url, opts = {}) => {
      if (url === '/api/v1/models') return {ok: true, json: async () => ({models: []})};
      if (url === '/api/v1/state') {
        if (stateError) throw stateError;
        return {ok: true, json: async () => state};
      }
      requests.push(JSON.parse(opts.body));
      return postResult || {ok: true, json: async () => ({job_id: 'accepted', status: 'accepted'})};
    }, EventSource, setInterval(fn, delay) {intervals[delay] = fn;},
    crypto: require('node:crypto').webcrypto, console};
  vm.runInNewContext(script, context);
  return {elements, requests, intervals, get stream() {return stream;},
    refresh: async next => {if (next) state = next; await elements.refresh.listeners.click();},
    fail: error => {stateError = error;}, recover: () => {stateError = null;},
    respond: result => {postResult = result;},
    push(next) {stream.listeners.snapshot({data: JSON.stringify(next)});},
    select(id) {
      const label = elements['job-target-nodes'].children.find(n => n.children[0]?.value === String(id));
      const input = label.children[0]; input.checked = true; input.listeners.change();
    },
    submit: async () => {elements['job-model'].value = 'a'.repeat(64);
      elements['job-rounds-input'].value = '5';
      await elements['job-form'].listeners.submit({preventDefault() {}});}};
}
(async () => {
  const page = openPage(); await flush();
  assert.equal(page.elements.nodes.children.length, 3, '只显示在线客户端');
  assert.deepEqual(page.elements.nodes.children.map(row => row.children[0].textContent), ['1', '2', '3']);
  const choices = page.elements['job-target-nodes'].children;
  assert.equal(choices.length, 3);
  assert.ok(choices.every(label => !label.children[0].checked), '初始为空');
  assert.equal(choices[1].children[0].disabled, true);
  assert.match(text(choices[1]), /寻频/);
  assert.equal(page.elements['start-job'].disabled, true);
  await page.submit(); assert.equal(page.requests.length, 0, '空集合不能提交');
  page.select(1); assert.equal(page.elements['start-job'].disabled, false);
  console.log('在线候选与空选门禁通过');

  await page.refresh(snapshot({nodes: [client(1), client(5)]}));
  assert.equal(page.elements['job-target-nodes'].children[0].children[0].checked, true);
  assert.equal(page.elements['job-target-nodes'].children[1].children[0].checked, false, '新上线不自动加入');
  await page.refresh(snapshot({nodes: [client(1, {can_select: false, selection_blockers: [
    {code: 'NODE_ERROR', message: '链路未就绪'}]}), client(5)]}));
  assert.equal(page.elements['start-job'].disabled, true);
  assert.match(page.elements['selection-notice'].textContent, /节点 1.*链路未就绪/);
  await page.refresh(snapshot({nodes: [client(1), client(5)]}));
  assert.ok(page.elements['job-target-nodes'].children.every(n => !n.children[0].checked));
  page.select(1);
  await page.refresh(snapshot({nodes: [client(5)]}));
  assert.match(page.elements['selection-notice'].textContent, /离线/);
  await page.refresh(); page.select(5);
  page.fail(new Error('management disconnected')); await page.refresh();
  assert.equal(page.elements['start-job'].disabled, true, '状态过时暂停启动');
  assert.equal(page.elements.nodes.children.length, 1, '保留最后节点画面');
  assert.match(page.elements['connection-error'].textContent, /过时/);
  await page.submit(); assert.equal(page.requests.length, 0);
  page.push(snapshot({nodes: [client(5)]}));
  assert.equal(page.elements['start-job'].disabled, false, 'SSE 单路可用可恢复状态');
  page.stream.onerror(); assert.equal(page.elements['start-job'].disabled, true);
  page.recover(); await page.refresh();
  assert.equal(page.elements['start-job'].disabled, false, 'HTTP 单路有效恢复操作');
  page.stream.onerror(); assert.equal(page.elements['start-job'].disabled, false);
  await page.refresh(snapshot({instance_id: 'two', nodes: [client(5)]}));
  assert.equal(page.elements['job-target-nodes'].children[0].children[0].checked, false);
  assert.equal(page.elements['start-job'].disabled, true, '实例变化清空草稿');
  const reloaded = openPage(snapshot({instance_id: 'two', nodes: [client(5)]})); await flush();
  assert.equal(reloaded.elements['start-job'].disabled, true, '整页重开初始为空');
  reloaded.select(5);
  reloaded.respond({ok: true, json: async () => ({job_id: 'accepted-job', status: 'accepted'})});
  reloaded.fail(new Error('状态快照不可用'));
  await reloaded.submit();
  assert.match(reloaded.elements['job-form-status'].textContent, /已受理/);
  assert.equal(reloaded.elements['job-form-error'].hidden, true, '已受理不能显示未受理错误');
  assert.equal(reloaded.elements['start-job'].disabled, true, '已受理但状态过时仍暂停启动');
  console.log('草稿失效、管理状态和双通路恢复通过');
  console.log('已受理与状态快照失败边界通过');

  page.select(5);
  page.respond({ok: false, json: async () => ({error: {code: 'preflight_target_nodes_not_ready',
    message: '整体拒绝', details: {unready_nodes: {'5': {reason: '节点已离线'}, '8': {reason: '基础资源错误'}}}}})});
  await page.submit();
  assert.equal(page.requests.length, 1, '拒绝不能自动重试');
  assert.deepEqual(page.requests[0].target_nodes, [5]);
  assert.match(page.elements['job-form-error'].textContent, /节点 5：节点已离线.*节点 8：基础资源错误/);
  const accepted = {job_id: 'job-fixed', target_nodes: [1, 5], rounds: 5,
    participants: [{node_id: 1, online: false, state: 'offline', readiness: 'OFFLINE',
      reported_state: null, error_code: null, can_select: false,
      selection_blockers: [{code: 'NODE_OFFLINE', message: '节点未上线或心跳已超时'}],
      disconnection_events: [{type: 'NODE_OFFLINE', message: '节点 1 离线'}]},
      {node_id: 5, online: true, state: 'idle', readiness: 'READY', reported_state: 'IDLE',
        error_code: null, can_select: true, selection_blockers: []}],
    execution_result: 'failed', recovery_state: 'blocked', error: 'client_startup_timeout'};
  page.respond(null);
  await page.refresh(snapshot({instance_id: 'two', nodes: [client(5)], job: accepted, recent_job: accepted}));
  assert.match(page.elements['job-config'].textContent, /节点 1, 5/, '离线过滤不能改变作业参与者');
  assert.match(page.elements['job-participants'].textContent, /节点 1 · 离线/, '作业视图保留离线参与者事实');
  assert.match(page.elements['job-participants'].textContent, /失联事件：节点 1 离线/, '作业视图保留失联事件');
  assert.match(page.elements['job-error'].textContent, /client_startup_timeout/);
  const resumed = openPage(snapshot({job: accepted, recent_job: accepted})); await flush();
  assert.equal(resumed.requests.length, 0, '快照恢复作业不得重发启动');
  assert.equal(resumed.elements['job-id'].textContent, 'job-fixed');
  console.log('整体拒绝原因、固定参与者与作业恢复通过');
})().catch(error => {console.error(error); process.exitCode = 1;});
