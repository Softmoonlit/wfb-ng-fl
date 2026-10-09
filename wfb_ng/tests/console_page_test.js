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
const snapshot = {
  instance_id: 'instance-one', state_version: 4, generated_at: '2026-01-01T00:00:00Z',
  server: {state: 'idle', management_web: {status:'ready', host:'192.0.2.1', port:8080, error:null},
    radio:{channel:157, radio_txpower_dbm:12, downlink_mcs:3, uplink_mcs:6},
    can_start_job:false, start_blockers:[{code:'JOB_RESOURCE_NOT_READY',message:'资源恢复未完成'}],
    tun:{is_active:true}, link_process:{running:true}},
  nodes:[{node_id:1, state:'idle', readiness:'READY', online:true, error_code:null}],
  job:{job_id:'job-1', execution_result:'failed', recovery_state:'blocked', error:'摘要错误', rounds:3, target_nodes:[1]},
  events:[{sequence:1, type:'JOB_FAILED', message:'摘要错误', timestamp:'now'}]
};
function openPage(responses) {
  const elements = {};
  let tick;
  const context = {document:{getElementById(id){return elements[id] ||= element();}, createElement:element},
    fetch:async(path) => {
      assert.equal(path, '/api/v1/state');
      const value=responses.shift();
      if (value instanceof Error) throw value;
      return {ok:value.ok !== false, json:async()=>value.body || value};
    }, setInterval(callback){tick=callback;}, sessionStorage:{getItem(){return null;},setItem(){}}, console};
  vm.runInNewContext(script, context);
  return {elements, tick:()=>tick()};
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
  assert.match(page.elements['start-blockers'].children[0].textContent,/资源恢复未完成/);
  assert.match(page.elements['radio'].textContent,/157/);
  assert.equal(page.elements['nodes'].children.length,1);
  await page.tick(); await flush();
  assert.equal(page.elements['connection-error'].hidden,false);
  assert.match(page.elements['connection-error'].textContent,/过时/);
  assert.match(page.elements['job-result'].textContent,/失败/);
  await page.elements['refresh'].listeners.click(); await flush();
  assert.equal(page.elements['connection-error'].hidden,true);
  await page.tick(); await flush();
  assert.match(page.elements['connection-error'].textContent,/STATE_UNAVAILABLE/);
  const refreshed=openPage([snapshot]); await flush();
  assert.match(refreshed.elements['job-id'].textContent,/job-1/);
  assert.match(refreshed.elements['job-recovery'].textContent,/恢复受阻/);
  console.log('Packaged console JS: rendering, failure, retry and refresh passed');
})().catch(error=>{console.error(error);process.exitCode=1;});
