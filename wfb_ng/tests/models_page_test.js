/* Node VM seam: observable DOM/fetch behavior, not a real browser. */
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const script = fs.readFileSync(process.argv[2], 'utf8');
function element(tag = '') {
  return {tag, textContent:'', hidden:false, disabled:false, value:'', files:[], children:[], listeners:{},
    append(...nodes){this.children.push(...nodes);}, replaceChildren(...nodes){this.children=nodes;},
    addEventListener(name, callback){this.listeners[name]=callback;},
    setAttribute(name, value){this[name]=value;}};
}
function openPage() {
  const elements = {}, requests = [], confirmations = [];
  const confirmation = {accepted:true};
  class FormData {constructor(){this.entries=[];} append(...entry){this.entries.push(entry);}}
  const context = {document:{getElementById(id){return elements[id] ||= element();}, createElement:element},
    confirm(message){confirmations.push(message); return confirmation.accepted;},
    FormData, fetch(path, options={}) {return new Promise((resolve,reject)=>requests.push({path,options,resolve,reject}));}};
  vm.runInNewContext(script, context);
  return {elements,requests,confirmations,confirmation};
}
const sha = 'a'.repeat(64), otherSha = 'b'.repeat(64);
const model = {sha256:sha, filename:'<img src=x onerror=bad()>模型.bin',size_bytes:41943040,
  created_at:'2026-01-01T00:00:00Z',referenced:false};
const response = (body, status=200) => ({ok:status>=200 && status<300,status,json:async()=>body});
async function flush(){for(let i=0;i<16;i++) await Promise.resolve();}
async function reply(request, body, status=200){request.resolve(response(body,status));await flush();}
function click(page, id){return page.elements[id].listeners.click();}
function submit(page,file={name:'model.bin',size:12}) {
  (page.elements['model-file'] ||= element()).files=file ? [file] : [];
  return page.elements['upload-form'].listeners.submit({preventDefault(){}});
}
function rows(page){return page.elements.models.children;}
function deletion(page,index=0){return rows(page)[index].children[5].children[0];}
function text(node){return [node.textContent,...node.children.map(text)].join(' ');}
(async()=>{
  const page=openPage();
  assert.equal(page.requests[0].path,'/api/v1/models');
  assert.equal(page.requests[0].options.cache,'no-store');
  await reply(page.requests[0],{models:[{...model,sha256:otherSha,referenced:true},model]});
  assert.equal(rows(page).length,2);
  assert.match(text(rows(page)[1]),new RegExp(sha));
  assert.match(text(rows(page)[1]),/41943040/);
  assert.match(text(rows(page)[1]),/2026-01-01T00:00:00Z/);
  assert.match(text(rows(page)[1]),/<img src=x onerror=bad\(\)>模型.bin/);
  assert.match(text(rows(page)[0]),/已引用/);
  assert.equal(deletion(page).disabled,true);
  await deletion(page).listeners.click();
  assert.equal(page.requests.length,1,'referenced model cannot issue DELETE');
  assert.equal(page.confirmations.length,0,'referenced model does not prompt for deletion');
  page.confirmation.accepted=false;
  const canceled=deletion(page,1).listeners.click();
  assert.equal(page.requests.length,1,'canceling deletion must not issue DELETE');
  await canceled;
  assert.equal(rows(page).length,2,'canceling deletion preserves the list');
  assert.equal(deletion(page,1).disabled,false);
  assert.equal(page.confirmations.length,1);
  assert.ok(page.confirmations[0].includes(model.filename));
  assert.ok(page.confirmations[0].includes(sha));
  page.confirmation.accepted=true;

  for(const file of [null,{size:0},{size:1073741825}]) {
    await submit(page,file);
    assert.equal(page.requests.length,1);
    assert.equal(page.elements['action-error'].hidden,false);
  }
  const upload=submit(page);
  assert.equal(page.elements.upload.disabled,true);
  assert.equal(deletion(page,1).disabled,true);
  const request=page.requests[1];
  assert.equal(request.path,'/api/v1/models');
  assert.equal(request.options.method,'POST');
  assert.equal(request.options.headers,undefined,'browser owns multipart boundary');
  assert.equal(request.options.body.entries.length,1);
  assert.equal(request.options.body.entries[0][0],'file');
  await submit(page);
  assert.equal(page.requests.length,2,'duplicate upload is blocked while pending');
  await reply(request,{model,deduplicated:false},201);
  assert.match(page.elements['action-status'].textContent,/上传成功/);
  assert.match(page.elements['action-status'].textContent,new RegExp(sha));
  await reply(page.requests[2],{models:[model]}); await upload;
  assert.equal(page.elements.upload.disabled,false);
  assert.equal(page.elements['action-error'].hidden,true);

  const duplicate=submit(page);
  await reply(page.requests[3],{model,deduplicated:true});
  assert.match(page.elements['action-status'].textContent,/去重/);
  await reply(page.requests[4],{models:[model]});await duplicate;

  const denied=deletion(page).listeners.click();
  assert.equal(page.confirmations.length,2,'confirming deletion issues the request');
  assert.equal(page.requests[5].path,`/api/v1/models/${sha}`);
  assert.equal(page.requests[5].options.method,'DELETE');
  await reply(page.requests[5],{error:{code:'MODEL_REFERENCED',message:'模型已被作业引用'}},409);await denied;
  assert.match(page.elements['action-error'].textContent,/MODEL_REFERENCED.*模型已被作业引用/);
  assert.equal(deletion(page).disabled,false);
  const removed=deletion(page).listeners.click();
  await reply(page.requests[6],{deleted:true});
  await reply(page.requests[7],{models:[]});await removed;
  assert.match(page.elements['list-status'].textContent,/暂无模型/);
  assert.equal(rows(page).length,0);

  const failed=submit(page);
  page.requests[8].reject(new Error('network disconnected'));await failed;
  assert.match(page.elements['action-error'].textContent,/network disconnected/);
  assert.equal(page.elements.upload.disabled,false);
  const capacity=submit(page);
  await reply(page.requests[9],{error:{code:'MODEL_CAPACITY_EXCEEDED',message:'模型库容量不足'}},409);await capacity;
  assert.match(page.elements['action-error'].textContent,/MODEL_CAPACITY_EXCEEDED.*模型库容量不足/);
  const retried=submit(page);
  await reply(page.requests[10],{model,deduplicated:false},201);
  await reply(page.requests[11],{models:[model]});await retried;
  assert.equal(page.elements['action-error'].hidden,true);

  const refresh=click(page,'refresh-models');
  page.requests[12].reject(new Error('offline'));await refresh;
  assert.match(page.elements['list-error'].textContent,/过时.*offline/);
  assert.equal(rows(page).length,1,'refresh failure preserves last list');
  const retry=click(page,'refresh-models');
  await reply(page.requests[13],{models:[model]});await retry;
  assert.equal(page.elements['list-error'].hidden,true);

  const race=openPage();
  const newer=click(race,'refresh-models');
  await reply(race.requests[1],{models:[model]});await newer;
  await reply(race.requests[0],{models:[]});
  assert.equal(rows(race).length,1,'older refresh cannot overwrite newer list');
  const old=click(race,'refresh-models');
  const mutation=submit(race);
  await reply(race.requests[3],{model,deduplicated:false},201);
  await reply(race.requests[4],{models:[model]});await mutation;
  await reply(race.requests[2],{models:[]});await old;
  assert.equal(rows(race).length,1,'pre-upload list cannot overwrite post-upload list');
  const obsolete=click(race,'refresh-models');
  const current=click(race,'refresh-models');
  await reply(race.requests[6],{models:[model]});await current;
  race.requests[5].reject(new Error('old failure'));await obsolete;
  assert.equal(race.elements['list-error'].hidden,true,'obsolete error cannot replace success');

  const preDelete=click(race,'refresh-models');
  const deleteRace=deletion(race).listeners.click();
  await reply(race.requests[8],{deleted:true});
  await reply(race.requests[9],{models:[]});await deleteRace;
  await reply(race.requests[7],{models:[model]});await preDelete;
  assert.equal(rows(race).length,0,'pre-delete list cannot restore deleted model');

  const uploadWithRefreshFailure=submit(race);
  await reply(race.requests[10],{model,deduplicated:false},201);
  await reply(race.requests[11],{error:{code:'LIST_UNAVAILABLE',message:'清单读取失败'}},503);
  await uploadWithRefreshFailure;
  assert.match(race.elements['action-status'].textContent,/上传成功/);
  assert.match(race.elements['list-error'].textContent,/LIST_UNAVAILABLE.*清单读取失败/);
  assert.equal(race.elements.upload.disabled,false);
  const recovered=click(race,'refresh-models');
  await reply(race.requests[12],{models:[model]});await recovered;
  assert.equal(race.elements['list-error'].hidden,true);

  const restored=openPage();await reply(restored.requests[0],{models:[model]});
  assert.equal(rows(restored).length,1,'page reload obtains server list');
  console.log('Models page Node VM: metadata, upload, deduplication, deletion, recovery and stale responses passed');
})().catch(error=>{console.error(error);process.exitCode=1;});
