/* 使用 Node VM 验证公开 DOM 和导出接缝，无浏览器依赖。 */
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const path = require('node:path');
const source = process.argv[2] || path.join(__dirname, '../fl/static/evaluation-curves.js');
function element(tag, ns) {
  return {tag, ns, children:[], attributes:{}, listeners:{}, textContent:'',
    append(...nodes){this.children.push(...nodes);},
    replaceChildren(...nodes){this.children=nodes;},
    setAttribute(key,value){this.attributes[key]=String(value);},
    addEventListener(key,fn){this.listeners[key]=fn;},
    contains(node){return this===node || this.children.some(child=>child.contains(node));},
    focus(){context.document.activeElement=this;if(this.listeners.focus)this.listeners.focus();},
    click(){if(this.listeners.click) this.listeners.click();}};
}
const downloads=[], revoked=[], blobs=[];
const context = {document:{createElement:element,createElementNS:(ns,tag)=>element(tag,ns)},
  Blob:class {constructor(parts, options){this.parts=parts;this.type=options.type;blobs.push(this);}},
  URL:{createObjectURL(){return 'blob:test';},revokeObjectURL(url){revoked.push(url);}}};
const create=context.document.createElement;
context.document.createElement=tag=>{const el=create(tag);if(tag==='a') el.click=()=>downloads.push(el);return el;};
vm.runInNewContext(fs.readFileSync(source,'utf8'),context);
const api=context.EvaluationCurves;
const algorithm={client_entry:'wfb_ng.fl.mnist:client_entry',aggregate_entry:'wfb_ng.fl.mnist:aggregate_entry',evaluate_entry:'wfb_ng.fl.mnist:evaluate_entry'};
function job(){return {job_id:'任务<&"'+ 'x'.repeat(120),algorithm,model_sha256:'initial',rounds:8,execution_result:'failed',evaluation:{initial_metrics:{loss:2,accuracy:0},rounds:[
  {round_index:1,output_model_sha256:'first',metrics:{loss:1,accuracy:0.5},verdict:false,reason:'已有原因,含"引号"\n下一行'},
  {round_index:2,output_model_sha256:'second',metrics:{loss:0,accuracy:null},reason:'评估失败'},
  {round_index:4,output_model_sha256:'fourth',metrics:{loss:0.25,accuracy:1},verdict:true},
  {round_index:5,output_model_sha256:'fifth',metrics:{loss:Infinity,accuracy:1.01}},
  {round_index:6,metrics:{loss:'1',accuracy:NaN}}
]}};}
const input=job(), before=JSON.stringify(input);
const points=api.modelPoints(input);
assert.equal(points.length,6);
assert.equal(points[0].round_index,0);
assert.equal(points[0].model_sha256,'initial');
assert.equal(points[0].verdict,null);
assert.equal(points[0].reason,null);
assert.equal(points[2].loss,0);
assert.equal(points[2].accuracy,null);
assert.equal(points[4].loss,null);
assert.equal(points[4].accuracy,null);
assert.equal(points[5].loss,null);
assert.equal(JSON.stringify(input),before);
for(const altered of [null,{...input,algorithm:null},{...input,algorithm:{...algorithm,client_entry:'other'}},{...input,algorithm:{...algorithm,evaluate_entry:'other'}},{...input,algorithm:{client_entry:algorithm.client_entry}}]) {
  assert.equal(api.modelPoints(altered).length,0);
}
assert.equal(api.modelPoints({...input,algorithm:{...algorithm,aggregate_entry:'other'}}).length,0);
const accuracy=api.renderSVG(input,'accuracy'), loss=api.renderSVG(input,'loss');
assert.match(accuracy,/xmlns="http:\/\/www.w3.org\/2000\/svg"/);
assert.match(accuracy,/viewBox="0 0 800 420"/);
assert.match(accuracy,/font-family="sans-serif"/);
assert.match(accuracy,/color="#172033"/);
assert.match(accuracy,/<rect [^>]*fill="#ffffff"/);
assert.match(accuracy,/准确率（%）/);
assert.match(accuracy,/通信轮次/);
assert.match(accuracy,/任务&lt;&amp;&quot;/);
assert.match(accuracy,/评估失败/);
assert.ok(!accuracy.includes('<&"'));
assert.ok(!accuracy.includes('…'),'long job ID must be wrapped, never truncated');
assert.match(accuracy,new RegExp('x'.repeat(50)));
const enormous = api.renderSVG({...input,job_id:'长'.repeat(1000)},'accuracy');
assert.ok(Number(enormous.match(/viewBox="0 0 800 (\d+)"/)[1])>420,'long labels expand SVG height');
assert.ok(!/NaN|Infinity/.test(api.renderSVG({...input,evaluation:{rounds:[
  {round_index:1,metrics:{loss:-Number.MAX_VALUE}},
  {round_index:2,metrics:{loss:Number.MAX_VALUE}}
]}},'loss')),'finite extremes must not overflow plot coordinates');
assert.equal((accuracy.match(/<circle [^>]*r="5"/g)||[]).length,3);
assert.equal((accuracy.match(/<path /g)||[]).length,1,'only M0 to M1 accuracy line');
assert.equal((loss.match(/<circle [^>]*r="5"/g)||[]).length,4);
assert.equal((loss.match(/<path /g)||[]).length,2,'M0-M1-M2 loss, no bridge to M4');
assert.match(loss,/测试交叉熵损失/);
assert.ok(!/NaN|Infinity/.test(loss));
const csv=api.toCSV(input);
assert.ok(csv.startsWith('job_id,round_index,model_sha256,loss,accuracy,verdict,reason\r\n'));
assert.match(csv,/,1,first,1,0.5,false,/);
assert.match(csv,/,0,initial,2,0,,/);
assert.match(csv,/"已有原因,含""引号""\n下一行"/);
assert.match(csv,/,5,fifth,,,,/);
assert.ok(!csv.includes('Infinity'));
assert.equal(api.toCSV({...input,algorithm:null}),'');
function all(node,tag){return [...(node.tag===tag?[node]:[]),...node.children.flatMap(child=>all(child,tag))];}
function visiblePoints(node){return all(node,'circle').filter(circle=>circle.attributes.r==='5');}
function text(node){return [node.textContent,...node.children.map(text)].join(' ');}
const container=element('div');
api.render(container,input);
assert.equal(all(container,'svg').length,2);
assert.equal(all(container,'button').length,3);
for(const svg of all(container,'svg')) assert.equal(svg.ns,'http://www.w3.org/2000/svg');
const circle=visiblePoints(container)[1];
const hitAreas=all(container,'circle').filter(circle=>circle.attributes.r==='18');
assert.equal(hitAreas.length,7);
hitAreas[1].listeners.click();
assert.match(text(container),/first/);
assert.match(text(container),/M3 评估未取得/);
assert.ok(!text(container).includes('M7 评估未取得'),'no future round notes');
assert.equal(circle.attributes.tabindex,'0');
assert.match(circle.attributes['aria-label'],/first/);
assert.ok(all(circle,'title').length);
for(const event of ['click','focus','mouseenter']) {
  circle.listeners[event]();assert.match(text(container),/0.5/);assert.match(text(container),/first/);
}
assert.match(text(container),/测试准确率未取得：评估失败/);
circle.listeners.keydown({key:'Enter',preventDefault(){}});
const originalRoot=container.children[0];
const originalDetails=all(container,'p').find(node=>node.className==='evaluation-curves-details').textContent;
api.render(container,JSON.parse(JSON.stringify(input)));
assert.equal(container.children[0],originalRoot,'identical polling snapshot retains actual DOM and keyboard focus target');
assert.equal(visiblePoints(container)[1],circle);
assert.equal(all(container,'p').find(node=>node.className==='evaluation-curves-details').textContent,originalDetails);
let prevented=false;
circle.listeners.keydown({key:' ',preventDefault(){prevented=true;}});
assert.equal(prevented,true);
const buttons=all(container,'button');
buttons.forEach(button=>button.click());
assert.equal(downloads.length,3);
assert.equal(revoked.length,3);
assert.equal(blobs.length,3);
assert.equal(blobs[0].parts[0],accuracy);
assert.equal(blobs[2].parts[0],csv);
assert.ok(downloads.every(anchor=>anchor.download && !/[<>"/]/.test(anchor.download)));
api.render(container,{...input,evaluation:{initial_metrics:{accuracy:0.2},rounds:[]}});
assert.equal(visiblePoints(container).length,1,'refresh replaces snapshot');
assert.equal(all(container,'button')[1].disabled,true,'loss export disabled separately');
api.render(container,{...input,job_id:'next',evaluation:{rounds:[]}});
assert.equal(visiblePoints(container).length,0);
assert.ok(all(container,'button').every(button=>button.disabled));
assert.match(text(container),/未取得/);
api.render(container,{...input,evaluation:{initial_metrics:{loss:0,accuracy:0},rounds:[]}});
assert.equal(visiblePoints(container).length,2,'zero is a real metric in both charts');
assert.ok(all(container,'button').every(button=>!button.disabled));
api.render(container,{...input,algorithm:null});
assert.ok(all(container,'button').every(button=>button.disabled));
assert.match(text(container),/无适用/);
api.render(container,null);
assert.equal(all(container,'svg').length,2);
assert.ok(all(container,'button').every(button=>button.disabled));
// 失败轮次必须来自明确归属，不按计划轮数补未来点。
const failed={job_id:'failed-round',algorithm,model_sha256:'m0',execution_result:'failed',rounds:5,
  first_failure:{round_index:1,reason:'评估函数失败'},evaluation:{initial_metrics:{accuracy:0.2,loss:2},rounds:[]}};
const failedPoints=api.modelPoints(failed);
assert.equal(failedPoints.length,2,'explicit failed round remains an empty evaluation fact');
assert.equal(failedPoints[1].round_index,1);
assert.equal(failedPoints[1].model_sha256,'');
assert.equal(failedPoints[1].accuracy,null);
assert.equal(failedPoints[1].loss,null);
assert.equal(failedPoints[1].verdict,null);
assert.match(failedPoints[1].reason,/评估函数失败/);
assert.match(api.toCSV(failed),/,1,,,,,.*评估函数失败/);
const inferred={...failed,first_failure:null,current_round:3,error:'传输中断',reason:'node_lost'};
assert.equal(api.modelPoints(inferred).at(-1).round_index,3,'current_round is set by ROUND_STARTED');
assert.match(api.modelPoints(inferred).at(-1).reason,/该轮未取得评估/);
assert.match(api.modelPoints(inferred).at(-1).reason,/传输中断/);
assert.ok(!api.modelPoints(inferred).at(-1).reason.includes('评估失败'));
assert.match(api.renderSVG(inferred,'accuracy'),/>3<\/text>/,'failed round expands x axis');
for(const altered of [
  {...inferred,execution_result:'running'}, {...inferred,execution_result:'aborted'},
  {...inferred,current_round:0}, {...inferred,current_round:undefined},
  {...inferred,current_round:'3'}
]) assert.equal(api.modelPoints(altered).length,1,'no un-attributed or future placeholders');
const evaluated={...failed,evaluation:{initial_metrics:{accuracy:0.2},rounds:[
  {round_index:1,output_model_sha256:'real',metrics:{accuracy:0.8,loss:1},reason:'真实原因',verdict:true}
]}};
assert.equal(api.modelPoints(evaluated).length,2);
assert.equal(api.modelPoints(evaluated)[1].accuracy,0.8);
assert.equal(api.modelPoints(evaluated)[1].reason,'真实原因');
const focusContainer=element('div');
const initial={job_id:'focus-job',algorithm,model_sha256:'focus-m0',execution_result:'running',evaluation:{initial_metrics:{accuracy:0.25,loss:2},rounds:[]}};
const updated={...initial,evaluation:{...initial.evaluation,rounds:[{round_index:1,output_model_sha256:'focus-m1',metrics:{accuracy:0.5,loss:1}}]}};
const selectedText=()=>all(focusContainer,'p').find(node=>node.className==='evaluation-curves-details').textContent;
api.render(focusContainer,initial);
const lossM0=visiblePoints(all(focusContainer,'svg')[1])[0];
lossM0.focus();
api.render(focusContainer,updated);
assert.equal(context.document.activeElement,visiblePoints(all(focusContainer,'svg')[1])[0],
  'new round restores focused metric and round');
assert.match(selectedText(),/focus-m0/,'new round retains selected model');
const external=element('input');external.focus();
const more={...updated,evaluation:{...updated.evaluation,rounds:[...updated.evaluation.rounds,
  {round_index:2,output_model_sha256:'focus-m2',metrics:{accuracy:0.75,loss:0.5}}]}};
api.render(focusContainer,more);
assert.equal(context.document.activeElement,external,'poll must not steal focus from outside the charts');
assert.match(selectedText(),/focus-m0/,'selection persists while focus is elsewhere');
const lossM1=visiblePoints(all(focusContainer,'svg')[1])[1];lossM1.focus();
visiblePoints(all(focusContainer,'svg')[0])[0].listeners.click();
api.render(focusContainer,{...more,evaluation:{...more.evaluation,initial_metrics:{accuracy:0.26,loss:2}}});
assert.equal(context.document.activeElement,visiblePoints(all(focusContainer,'svg')[1])[1]);
assert.match(selectedText(),/focus-m0/,'selected point and focused point are preserved independently');
api.render(focusContainer,{...initial,job_id:'another-job'});
assert.ok(!selectedText().includes('focus-m0'),'another job clears prior selection');
assert.ok(!focusContainer.contains(context.document.activeElement),'another job does not restore old point focus');
api.render(focusContainer,failed);
assert.match(text(focusContainer),/评估函数失败/);
assert.equal(visiblePoints(focusContainer).length,2,'failed placeholder never becomes a fabricated metric');
assert.ok(!all(focusContainer,'figcaption').some(note=>note.textContent.includes('M2')));
console.log('evaluation_curves_test: PASS (helpers, gaps, DOM, interactions, snapshot replacement, SVG/CSV downloads, failed rounds, focus restoration)');
