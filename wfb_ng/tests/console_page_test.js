/* Execute the packaged browser script with observable DOM and fetch boundaries. */
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const script = fs.readFileSync(process.argv[2], 'utf8');
function element() {
  return {textContent: '', hidden: false, children: [], listeners: {},
    append(...nodes) { this.children.push(...nodes); },
    replaceChildren(...nodes) {this.children = nodes;},
    addEventListener(name, callback) {this.listeners[name] = callback;}};
}
function text(node) { return [node.textContent, ...node.children.map(text)].join(' '); }
const snapshot = {
  instance_id: 'instance-one', state_version: 4, generated_at: '2026-01-01T00:00:00Z',
  server: {state: 'idle', management_web: {status:'ready', host:'127.0.0.1', port:0, error:null},
    radio:{channel:157, radio_txpower_dbm:12, downlink_mcs:3, uplink_mcs:6},
    can_start_job:false, start_blockers:[{code:'JOB_RESOURCE_NOT_READY',message:'资源恢复未完成'}],
    tun:{is_active:true}, link_process:{running:true}},
  nodes:[{node_id:1, state:'idle', readiness:'READY', online:true, error_code:null, can_select:true, selection_blockers:[]}],
  job:{job_id:'job-1', execution_result:'failed', recovery_state:'blocked', error:'摘要错误', rounds:1, target_nodes:[1],
    model_sha256:'a'.repeat(64),
    evaluation:{initial_metrics:{loss:2, accuracy:0}, rounds:[{round_index:1, output_model_sha256:'b'.repeat(64),
      metrics:{loss:0, accuracy:0}, verdict:null, client_submission_returns_received:false, training:[]}],
      effect_result:{verdict:null}, final_model_sha256:'b'.repeat(64)},
    participants:[{node_id:1, online:false, state:'offline', readiness:'OFFLINE', reported_state:null,
      error_code:null, can_select:false, selection_blockers:[{code:'NODE_OFFLINE', message:'节点未上线或心跳已超时'}],
      preparation:{sample_count:60000, device:{type:'cuda', name:'GPU 1'}},
      training:[{round_index:1, sample_count:60000, optimizer_steps:938, train_loss:0, device:{type:'cuda'}}]}],
    recovery_missing_nodes:[1], server_recovery_complete:false,
    first_failure:{error_code:'GPU_TRAINING_FAILED', reason:'设备失败'},
    liveness_events:[{node_id:1,state:'offline'}],
    additional_errors:[{error_code:'LATE_ERROR',reason:'附加诊断'}]},
  events:[{sequence:1, type:'JOB_FAILED', message:'摘要错误', timestamp:'now'}]
};
function openPage(responses) {
  const elements = {};
  let tick;
  function EventSource(url) {
    this.url = url;
    this.listeners = {};
    EventSource.instance = this;
  }
  EventSource.prototype.addEventListener = function(name, callback) {this.listeners[name] = callback;};
  EventSource.prototype.emit = function(name, data) {this.listeners[name]({data, lastEventId:'9'});};
  const context = {document:{getElementById(id){return elements[id] ||= element();}, createElement:element},
    fetch:async(path) => {
      assert.equal(path, '/api/v1/state');
      const value=responses.shift();
      if (value instanceof Error) throw value;
      return {ok:value.ok !== false, json:async()=>value.body || value};
    }, setInterval(callback){tick=callback;}, EventSource,
    sessionStorage:{getItem(){return null;},setItem(){}}, console};
  vm.runInNewContext(script, context);
  return {elements, tick:()=>tick(), stream:EventSource.instance};
}
async function flush(){for(let i=0;i<8;i++) await Promise.resolve();}
(async()=>{
  const page = openPage([snapshot, new Error('network'), snapshot,
    {ok:false,body:{error:{code:'STATE_UNAVAILABLE',message:'暂时无法读取状态'}}}]);
  await flush();
  assert.match(page.elements['server-state'].textContent,/待命/);
  assert.match(page.elements['job-result'].textContent,/失败/);
  assert.match(page.elements['job-recovery'].textContent,/恢复受阻/);
  assert.match(page.elements['job-error'].textContent,/摘要错误/);
  assert.equal(page.elements['job-evaluation-rows'].children.length, 2);
  assert.match(text(page.elements['job-evaluation-rows'].children[0]), /2/);
  assert.match(text(page.elements['job-evaluation-rows'].children[0]), /0/);
  assert.match(text(page.elements['job-evaluation-rows'].children[1]), /未判定/);
  assert.match(text(page.elements['job-evaluation-rows'].children[1]), /未确认/);
  assert.match(page.elements['job-effect-result'].textContent, /未判定/);
  assert.equal(page.elements['job-participant-rows'].children.length, 1);
  assert.match(text(page.elements['job-participant-rows'].children[0]), /60000/);
  assert.match(text(page.elements['job-participant-rows'].children[0]), /938/);
  assert.match(page.elements['job-recovery-facts'].textContent, /Server 资源恢复尚未完成/);
  assert.match(page.elements['job-recovery-facts'].textContent, /未确认恢复节点：1/);
  const aborted = JSON.parse(JSON.stringify(snapshot));
  aborted.state_version = 5;
  aborted.job.execution_result = 'aborted';
  aborted.job.recovery_state = 'recovering';
  page.stream.emit('snapshot', JSON.stringify(aborted));
  await flush();
  assert.match(text(page.elements['job-evaluation-rows'].children[1]), /未确认/);

  assert.match(page.elements['start-blockers'].children[0].textContent,/资源恢复未完成/);
  assert.match(page.elements['radio'].textContent,/157/);
  assert.equal(page.elements['nodes'].children.length,1);
  assert.equal(page.stream.url, '/api/v1/events');
  const restored = JSON.parse(JSON.stringify(snapshot));
  restored.state_version = 6;
  page.stream.emit('snapshot', JSON.stringify(restored));
  await flush();
  assert.match(page.elements['job-id'].textContent,/job-1/);
  page.stream.onerror();
  assert.equal(page.elements['connection-error'].hidden,false);
  assert.match(page.elements['connection-error'].textContent,/实时连接中断/);
  assert.match(page.elements['job-result'].textContent,/失败/);
  await page.tick(); await flush();
  assert.match(page.elements['connection-error'].textContent,/过时/);
  await page.elements['refresh'].listeners.click(); await flush();
  assert.equal(page.elements['connection-error'].hidden,true);
  page.stream.onerror();
  await page.tick(); await flush();
  assert.match(page.elements['connection-error'].textContent,/过时/);
  const refreshed=openPage([snapshot]); await flush();
  assert.match(refreshed.elements['job-id'].textContent,/job-1/);
  assert.match(refreshed.elements['job-recovery'].textContent,/恢复受阻/);
  console.log('Packaged console JS: rendering, failure, retry and refresh passed');
})().catch(error=>{console.error(error);process.exitCode=1;});
