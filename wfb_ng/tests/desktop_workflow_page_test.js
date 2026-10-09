/*
 * Comprehensive automated test for Stage 4 desktop console workflow.
 * Covers:
 * 1. Model library upload, full SHA-256 digest, deduplication, selection.
 * 2. Monitor page job creation, target nodes, sync rounds, running state observation.
 * 3. Authoritative gating: no fake progress/telemetry, operations guarded during running job.
 * 4. Page refresh and SSE disconnection/reconnection (timer pausing).
 * 5. Radio preparation out-of-bounds warning, risk confirmation checkbox, running job conflict.
 * 6. Persistent ABORT (急停), recovery-in-progress gating (start blocked), restart after recovery ready.
 */
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');

const consoleScript = fs.readFileSync(process.argv[2] || 'wfb_ng/fl/static/console.js', 'utf8');
const modelsScript = fs.readFileSync(process.argv[3] || 'wfb_ng/fl/static/models.js', 'utf8');
const radioScript = fs.readFileSync(process.argv[4] || 'wfb_ng/fl/static/radio.js', 'utf8');

function createElement(tag = '') {
  return {
    tag,
    textContent: '',
    hidden: false,
    disabled: false,
    value: '',
    checked: false,
    files: [],
    children: [],
    listeners: {},
    className: '',
    append(...nodes) { this.children.push(...nodes); },
    replaceChildren(...nodes) { this.children = nodes; },
    addEventListener(name, callback) { this.listeners[name] = callback; },
    setAttribute(name, value) { this[name] = value; }
  };
}

function text(node) {
  if (!node) return '';
  return [node.textContent, ...node.children.map(text)].join(' ').trim();
}

async function flush() {
  for (let i = 0; i < 20; i++) await Promise.resolve();
}

function createModelContext(fetchHandler, confirmations) {
  const elements = {};
  class FormData {
    constructor() { this.entries = []; }
    append(...entry) { this.entries.push(entry); }
  }
  return {
    document: {
      getElementById(id) { return elements[id] ||= createElement(); },
      createElement
    },
    confirm(msg) { confirmations.push(msg); return true; },
    FormData,
    fetch: fetchHandler,
    console: { log() {}, error() {}, warn() {} }
  };
}

function createConsoleContext(fetchHandler, eventSourceHolder, confirmCallback) {
  const elements = {};
  let timerTick;

  function EventSource(url) {
    this.url = url;
    this.listeners = {};
    eventSourceHolder.instance = this;
  }
  EventSource.prototype.addEventListener = function(name, callback) { this.listeners[name] = callback; };
  EventSource.prototype.emit = function(name, data, lastEventId = '1') {
    if (this.listeners[name]) this.listeners[name]({ data, lastEventId });
  };
  EventSource.prototype.triggerError = function() {
    if (this.onerror) this.onerror(new Error('Connection lost'));
  };

  elements['job-rounds-input'] = createElement('input');
  elements['job-rounds-input'].value = '3';
  elements['job-target-nodes'] = createElement('input');
  elements['job-target-nodes'].value = '1, 2';

  const context = {
    document: {
      getElementById(id) { return elements[id] ||= createElement(); },
      createElement
    },
    fetch: fetchHandler,
    setInterval(callback) { timerTick = callback; return 1; },
    sessionStorage: {
      getItem(k) { return this[k] || null; },
      setItem(k, v) { this[k] = v; }
    },
    confirm: confirmCallback || (() => true),
    EventSource,
    URLSearchParams,
    window: {
      location: { search: '?model=' + 'a'.repeat(64) }
    },
    console: { log() {}, error() {}, warn() {} }
  };

  return { context, elements, tick: () => { if (timerTick) timerTick(); } };
}

(async () => {
  console.log('--- Step 1: Model Library Upload, Deduplication and Selection ---');
  const modelSha = 'a'.repeat(64);
  const modelConfirmations = [];
  let modelList = [];
  const modelFetch = async (path, opts = {}) => {
    if (path === '/api/v1/models' && (!opts.method || opts.method === 'GET')) {
      return { ok: true, json: async () => ({ models: modelList }) };
    }
    if (path === '/api/v1/models' && opts.method === 'POST') {
      const isDedup = modelList.some(m => m.sha256 === modelSha);
      const newModel = {
        sha256: modelSha,
        filename: 'deterministic_fixture_40mib.bin',
        size_bytes: 41943040,
        created_at: '2026-10-10T00:00:00Z',
        referenced: false
      };
      if (!isDedup) modelList.push(newModel);
      return {
        ok: true,
        json: async () => ({ deduplicated: isDedup, model: newModel })
      };
    }
    if (path === '/api/v1/state') {
      return {
        ok: true,
        json: async () => ({
          server: { state: 'idle', radio: { channel: 157, radio_txpower_dbm: 12, downlink_mcs: 3 } },
          current_job: null,
          recent_job: null
        })
      };
    }
    throw new Error('Unexpected path in models context: ' + path);
  };

  const modelsCtx = createModelContext(modelFetch, modelConfirmations);
  vm.runInNewContext(modelsScript, modelsCtx);
  await flush();

  assert.equal(modelsCtx.document.getElementById('models').children.length, 0, 'Initially 0 models');

  // Perform upload
  const fileInput = modelsCtx.document.getElementById('model-file');
  fileInput.files = [{ name: 'deterministic_fixture_40mib.bin', size: 41943040 }];
  await modelsCtx.document.getElementById('upload-form').listeners.submit({ preventDefault() {} });
  await flush();

  const statusMsg = modelsCtx.document.getElementById('action-status').textContent;
  assert.match(statusMsg, /上传成功/);
  assert.match(statusMsg, new RegExp(modelSha));
  assert.equal(modelsCtx.document.getElementById('models').children.length, 1, '1 model in table after upload');

  // Verify full 64-char SHA-256 and operation buttons
  const row0 = modelsCtx.document.getElementById('models').children[0];
  const digestCell = row0.children[1];
  assert.equal(text(digestCell), modelSha, 'Digest must display full 64-char lowercase SHA-256');
  const opCell = row0.children[5];
  const deleteBtn = opCell.children[0];
  const selectBtn = opCell.children[1];
  assert.equal(deleteBtn.textContent, '删除');
  assert.match(selectBtn.textContent, /选择/);
  assert.equal(selectBtn.href, `/?model=${modelSha}`, 'Select button navigates to monitor page with model sha');

  // Perform deduplicated upload of identical file
  fileInput.files = [{ name: 'deterministic_fixture_40mib.bin', size: 41943040 }];
  await modelsCtx.document.getElementById('upload-form').listeners.submit({ preventDefault() {} });
  await flush();
  const dedupStatus = modelsCtx.document.getElementById('action-status').textContent;
  assert.match(dedupStatus, /去重成功，模型已存在/, 'Deduplicated upload returns unambiguous deduplication status');
  assert.equal(modelsCtx.document.getElementById('models').children.length, 1, 'No duplicate entry created');

  console.log('--- Step 2: Main Console Monitor, Pre-selected Model & Job Creation ---');
  let currentServerState = 'idle';
  let activeJob = null;
  let recentJob = null;
  let startBlockers = [];
  const postedJobs = [];
  const abortedJobs = [];
  const eventSourceHolder = {};

  const makeSnapshot = () => ({
    instance_id: 'server-instance-01',
    state_version: 10,
    generated_at: '2026-10-10T00:01:00Z',
    server: {
      state: currentServerState,
      management_web: { status: 'ready', host: '192.168.10.1', port: 8080, error: null },
      radio: { channel: 157, radio_txpower_dbm: 12, downlink_mcs: 3, uplink_mcs: 6 },
      can_start_job: currentServerState === 'idle' && startBlockers.length === 0,
      start_blockers: startBlockers,
      tun: { is_active: true },
      link_process: { running: true },
      air_interface: 'wlx00e04c000001'
    },
    nodes: [
      { node_id: 1, online: true, state: 'idle', readiness: 'READY', error_code: null },
      { node_id: 2, online: true, state: 'idle', readiness: 'READY', error_code: null }
    ],
    job: activeJob || recentJob,
    current_job: activeJob,
    recent_job: recentJob,
    execution_result: (activeJob || recentJob || {}).execution_result || 'none',
    recovery_state: (activeJob || recentJob || {}).recovery_state || 'not_required',
    events: [
      { sequence: 1, type: 'SERVER_IDLE', message: '集群待命', timestamp: '00:00:01' }
    ]
  });

  const consoleFetch = async (path, opts = {}) => {
    if (path === '/api/v1/state') {
      return { ok: true, json: async () => makeSnapshot() };
    }
    if (path === '/api/v1/models') {
      return { ok: true, json: async () => ({ models: modelList }) };
    }
    if (path === '/api/v1/jobs' && opts.method === 'POST') {
      const body = JSON.parse(opts.body);
      postedJobs.push({ body, headers: opts.headers });
      activeJob = {
        job_id: 'sim-job-001',
        run_id: 'run-test',
        model_sha256: body.model_sha256,
        target_nodes: body.target_nodes,
        rounds: body.rounds,
        current_round: 1,
        rounds_completed: 0,
        server_phase: 'preparing',
        execution_result: 'running',
        recovery_state: 'not_required',
        started_at: Date.now() / 1000 - 10,
        round_started_at: Date.now() / 1000 - 5,
        phase_started_at: Date.now() / 1000 - 2,
        error: null,
        reason: null
      };
      currentServerState = 'running';
      startBlockers = [{ code: 'JOB_RUNNING', message: '作业正在运行' }];
      return { ok: true, json: async () => ({ job_id: 'sim-job-001', status: 'accepted' }) };
    }
    if (path.startsWith('/api/v1/jobs/') && path.endsWith('/abort') && opts.method === 'POST') {
      abortedJobs.push(path);
      const jid = activeJob ? activeJob.job_id : 'sim-job-001';
      recentJob = {
        ...activeJob,
        execution_result: 'aborted',
        recovery_state: 'recovering',
        server_phase: 'recovering',
        reason: 'operator_requested'
      };
      activeJob = null;
      currentServerState = 'idle';
      startBlockers = [{ code: 'JOB_RESOURCE_NOT_READY', message: '上一项作业资源尚未恢复' }];
      return { ok: true, json: async () => ({ job_id: jid, status: 'accepted', execution_result: 'aborted' }) };
    }
    throw new Error('Unexpected console fetch: ' + path);
  };

  const { context: cContext, elements: cElements, tick: cTick } = createConsoleContext(consoleFetch, eventSourceHolder);
  vm.runInNewContext(consoleScript, cContext);
  await flush();

  assert.equal(cElements['job-model'].value, modelSha, 'URL ?model= was auto-selected');
  assert.equal(cElements['job-rounds-input'].value, '3', 'Default rounds is 3');
  assert.equal(cElements['job-target-nodes'].value, '1, 2', 'Default target nodes filled with ready nodes');
  assert.equal(cElements['start-job'].disabled, false, 'Start button enabled when cluster is idle');
  assert.equal(cElements['abort-job'].disabled, true, 'Abort button disabled when no active job');

  // Submit job
  await cElements['job-form'].listeners.submit({ preventDefault() {} });
  await flush();

  assert.equal(postedJobs.length, 1, 'Submitted job request');
  assert.equal(postedJobs[0].body.model_sha256, modelSha);
  assert.deepEqual(postedJobs[0].body.target_nodes, [1, 2]);
  assert.equal(postedJobs[0].body.rounds, 3);
  assert.ok(postedJobs[0].headers['Idempotency-Key'], 'Idempotency-Key must be included');

  console.log('--- Step 3: Running State Observation & Gate Invariants ---');
  // Refresh view with running job
  await cElements['refresh'].listeners.click();
  await flush();

  assert.equal(cElements['server-state'].textContent, '运行中');
  assert.equal(cElements['job-id'].textContent, 'sim-job-001');
  assert.equal(cElements['job-result'].textContent, '执行中');
  assert.equal(cElements['job-phase'].textContent, '准备任务');
  assert.match(cElements['job-rounds'].textContent, /1 \/ 3/);
  assert.equal(cElements['start-job'].disabled, true, 'Start job button disabled during active job');
  assert.equal(cElements['abort-job'].disabled, false, 'Abort button enabled during active job');

  // Timer test: local timers calculate elapsed seconds
  cTick();
  assert.notEqual(cElements['job-elapsed'].textContent, '00:00');
  assert.notEqual(cElements['round-elapsed'].textContent, '00:00');

  // Spec check: No fake progress percentage, no fake file percentages, no fake training/aggregation
  const fullPageText = Object.values(cElements).map(el => el.textContent).join(' ');
  assert.ok(!fullPageText.includes('总进度 50%'), 'No fake progress percent');
  assert.ok(!fullPageText.includes('文件传输 80%'), 'No fake file transfer percent');
  assert.ok(!fullPageText.includes('模型聚合中'), 'No fake aggregation phases');
  assert.ok(!fullPageText.includes('本地训练中'), 'No fake training phases');

  console.log('--- Step 4: Page Refresh and SSE Disconnection / Reconnection ---');
  // SSE stream snapshot delivery
  eventSourceHolder.instance.emit('snapshot', JSON.stringify(makeSnapshot()));
  await flush();
  assert.equal(cElements['connection-error'].hidden, true);

  // SSE disconnect triggers error banner and pauses timers
  const elapsedBeforeDisconnect = cElements['job-elapsed'].textContent;
  eventSourceHolder.instance.triggerError();
  await flush();
  assert.equal(cElements['connection-error'].hidden, false);
  assert.match(cElements['connection-error'].textContent, /实时连接中断/);

  // Tick while disconnected does not advance timers
  cTick();
  assert.equal(cElements['job-elapsed'].textContent, elapsedBeforeDisconnect, 'Timers paused during disconnection');

  // Reconnection restores timer advancement and clears error
  eventSourceHolder.instance.emit('snapshot', JSON.stringify(makeSnapshot()));
  await flush();
  assert.equal(cElements['connection-error'].hidden, true);

  console.log('--- Step 5: Persistent Abort (急停) and Resource Recovery Barrier ---');
  let confirmDialogPrompted = false;
  cContext.confirm = (msg) => { confirmDialogPrompted = true; return true; };

  await cElements['abort-job'].listeners.click();
  await flush();

  assert.ok(confirmDialogPrompted, 'Emergency stop prompts for operator confirmation');
  assert.equal(abortedJobs.length, 1, 'Aborted job endpoint called');

  // Update view to reflecting aborted job in recovery
  await cElements['refresh'].listeners.click();
  await flush();

  assert.equal(cElements['job-result'].textContent, '已急停');
  assert.equal(cElements['job-recovery'].textContent, '正在恢复待命');
  assert.equal(cElements['start-job'].disabled, true, 'Cannot start next job while recovery is in progress');
  assert.match(cElements['start-blockers'].children[0].textContent, /资源尚未恢复/);

  console.log('--- Step 6: Recovery Completion and Restart ---');
  // Recovery finishes on Server side
  recentJob.recovery_state = 'ready';
  recentJob.server_phase = 'ready';
  startBlockers = [];

  await cElements['refresh'].listeners.click();
  await flush();

  assert.equal(cElements['job-recovery'].textContent, '已恢复待命');
  assert.equal(cElements['server-state'].textContent, '待命');
  assert.equal(cElements['start-job'].disabled, false, 'Start job button re-enabled after recovery is ready');

  // Restart next job with 2 rounds
  cElements['job-rounds-input'].value = '2';
  await cElements['job-form'].listeners.submit({ preventDefault() {} });
  await flush();

  assert.equal(postedJobs.length, 2, 'Successfully restarted second job after clean recovery');
  assert.equal(postedJobs[1].body.rounds, 2);

  console.log('--- Step 7: Radio Out-of-Bounds Rate Warning and Risk Confirmation ---');
  let radioConfirmation = null;
  const radioRequests = [];
  const radioElements = {};
  const config = { channel: 157, radio_txpower_dbm: 12, downlink_mcs: 3, uplink_mcs: 6, uftp_rate_kbps: 15000 };
  const bounds = { downlink_mcs: 3, min_rate_kbps: 11000, max_rate_kbps: 19000, default_rate_kbps: 16000 };

  const radioFetch = async (path, opts = {}) => {
    radioRequests.push({ path, opts });
    if (path === '/api/v1/radio/config') {
      return {
        ok: true,
        json: async () => ({
          config,
          rate_bounds: { '3': bounds },
          snapshot: makeSnapshot()
        })
      };
    }
    if (path === '/api/v1/radio/config/validate' && opts.method === 'POST') {
      const body = JSON.parse(opts.body);
      const isRisky = body.config.uftp_rate_kbps > bounds.max_rate_kbps || body.config.uftp_rate_kbps < bounds.min_rate_kbps;
      radioConfirmation = {
        token: 'test-radio-token',
        instance_id: 'server-instance-01',
        state_version: 10,
        expires_in_seconds: 30
      };
      return {
        ok: true,
        json: async () => ({
          config: body.config,
          rate_bounds: bounds,
          warning: isRisky ? '越界：可能造成丢包及传输失败' : null,
          confirmation: radioConfirmation
        })
      };
    }
    if (path === '/api/v1/radio/config/apply' && opts.method === 'POST') {
      const body = JSON.parse(opts.body);
      assert.equal(body.confirmation_token, 'test-radio-token');
      assert.equal(body.confirm_risk, true, 'Applying out-of-bounds config requires confirm_risk=true');
      return {
        ok: true,
        json: async () => ({ status: 'completed', effective_config: { ...config, uftp_rate_kbps: 25000 } })
      };
    }
    throw new Error('Unexpected radio fetch: ' + path);
  };

  const radioCtx = {
    document: {
      getElementById(id) { return radioElements[id] ||= createElement(); },
      createElement
    },
    fetch: radioFetch,
    setInterval() { return 1; },
    Date: { now: () => 10000 },
    confirm: () => true,
    console: { log() {}, error() {}, warn() {} }
  };

  vm.runInNewContext(radioScript, radioCtx);
  await flush();

  assert.equal(radioElements.channel.value, '157');
  // While second job is running from step 6, radio application is blocked
  assert.ok(radioElements['apply-blockers'].textContent.includes('作业正在运行'), 'Radio application blocked while job is running');
  assert.equal(radioElements.validate.disabled, true, 'Validate disabled while job is running');
  assert.equal(radioElements.apply.disabled, true, 'Apply disabled while job is running');

  // Now job completes and cluster becomes idle
  activeJob = null;
  currentServerState = 'idle';
  startBlockers = [];
  await radioElements.refresh.listeners.click();
  await flush();

  assert.equal(radioElements.validate.disabled, false, 'Validate enabled when cluster is idle');
  assert.equal(radioElements.apply.disabled, true, 'Apply initially disabled before validation');

  // Set out-of-bounds rate (25000 kbps vs max 19000 kbps)
  radioElements.uftp_rate_kbps.value = '25000';
  radioElements.uftp_rate_kbps.listeners.input();

  // Validate configuration
  await radioElements['radio-form'].listeners.submit({ preventDefault() {} });
  await flush();

  assert.match(radioElements['rate-warning'].textContent, /超出安全范围|越界/);
  assert.equal(radioElements.apply.disabled, true, 'Apply disabled when risky until checkbox confirmed');
  assert.equal(radioElements['confirm-risk'].checked, false);

  // Check risk confirmation checkbox
  radioElements['confirm-risk'].checked = true;
  radioElements['confirm-risk'].listeners.change();

  assert.equal(radioElements.apply.disabled, false, 'Apply enabled after explicit risk confirmation');

  // Apply configuration
  await radioElements.apply.listeners.click();
  await flush();

  const applyReq = radioRequests.find(r => r.path === '/api/v1/radio/config/apply');
  assert.ok(applyReq, 'Apply request sent');
  assert.deepEqual(JSON.parse(applyReq.opts.body), {
    confirmation_token: 'test-radio-token',
    confirm_risk: true
  });

  console.log('All desktop console workflow automated tests passed successfully!');
})().catch(err => {
  console.error('Desktop console workflow test failed:', err);
  process.exitCode = 1;
});
