/* 参考 MNIST 全局模型评估；样式由父页面提供。 */
(function () {
  'use strict';
  const NS = 'http://www.w3.org/2000/svg';
  const renderedSnapshots = new WeakMap();
  const metrics = {
    accuracy: {label: '测试准确率', axis: '准确率（%）', scale: 100},
    loss: {label: '测试交叉熵损失', axis: '交叉熵损失（无量纲）', scale: 1}
  };

  const MNIST_ENTRY_BUNDLES = [
    ['wfb_ng.fl.mnist:client_entry', 'wfb_ng.fl.mnist:aggregate_entry', 'wfb_ng.fl.mnist:evaluate_entry'],
    ['wfb_ng.fl.mnist_cnn_32_64:client_entry', 'wfb_ng.fl.mnist_cnn_32_64:aggregate_entry', 'wfb_ng.fl.mnist_cnn_32_64:evaluate_entry']
  ];

  function applicable(job) {
    const algorithm = job && job.algorithm;
    return !!algorithm && MNIST_ENTRY_BUNDLES.some(bundle =>
      bundle.every((entry, index) => algorithm[['client_entry', 'aggregate_entry', 'evaluate_entry'][index]] === entry));
  }

  function validNumber(value) {
    return typeof value === 'number' && Number.isFinite(value) ? value : null;
  }

  // 只投影快照已存在的评估事实，不按计划轮数补点。
  function modelPoints(job) {
    if (!applicable(job)) return [];
    const evaluation = job.evaluation || {};
    function point(round, sha, values, verdict, reason) {
      const accuracy = validNumber(values && values.accuracy);
      return {round_index: round, model_sha256: typeof sha === 'string' ? sha : '',
        loss: validNumber(values && values.loss),
        accuracy: accuracy !== null && accuracy >= 0 && accuracy <= 1 ? accuracy : null,
        verdict: typeof verdict === 'boolean' ? verdict : null,
        reason: typeof reason === 'string' ? reason : null};
    }
    const points = [];
    if (evaluation.initial_metrics) {
      points.push(point(0, job.model_sha256, evaluation.initial_metrics, null, null));
    }
    for (const round of evaluation.rounds || []) {
      if (!round || !Number.isSafeInteger(round.round_index) || round.round_index < 1) continue;
      points.push(point(round.round_index, round.output_model_sha256, round.metrics, round.verdict, round.reason));
    }
    // first_failure 明确归属轮次；current_round 仅在执行失败时使用，
    // 其服务端契约为 ROUND_STARTED 已发生，不表示评估函数已经执行。
    const failure = job.first_failure || {};
    const positiveRound = value => Number.isSafeInteger(value) && value > 0;
    const failedRound = positiveRound(failure.round_index) ? failure.round_index :
      job.execution_result === 'failed' && positiveRound(job.current_round) ? job.current_round : null;
    if (failedRound !== null && !points.some(item => item.round_index === failedRound)) {
      const reasons = [failure.reason, failure.error, job.reason, job.error]
        .filter(value => typeof value === 'string' && value);
      const reason = [...new Set(reasons)].join('；');
      points.push(point(failedRound, '', null, null, `该轮未取得评估${reason ? '：' + reason : '（原因未取得）'}`));
    }
    return points.sort((a, b) => a.round_index - b.round_index);
  }

  function xmlEscape(value) {
    return String(value).replace(/[&<>"']/g, char => (
      {'&':'&amp;', '<':'&lt;', '>':'&gt;', '"':'&quot;', "'":'&apos;'}[char]));
  }

  function detail(point) {
    return `M${point.round_index} · 通信轮次 ${point.round_index} · 模型 ${point.model_sha256 || '未取得'} · ` +
      `测试准确率 ${point.accuracy === null ? '未取得' : point.accuracy + '（原始值），' + point.accuracy * 100 + '%'} · ` +
      `测试交叉熵损失 ${point.loss === null ? '未取得' : point.loss} · ` +
      `效果判定 ${point.verdict === null ? '未判定' : point.verdict ? '通过' : '未通过'} · 原因 ${point.reason || '未取得'}`;
  }

  function node(tag, attrs = {}, children = [], text = '') {
    return {tag, attrs, children, text};
  }

  function unavailable(job, key, points) {
    if (!job) return '暂无作业，评估指标未取得';
    if (!applicable(job)) return '无适用评估数据：仅支持参考 MNIST 的客户端、聚合与评估入口';
    if (!points.some(point => point[key] !== null)) return `${metrics[key].label}未取得`;
    return '';
  }

  function scene(job, key, points) {
    const metric = metrics[key];
    if (!metric) throw new Error('未知评估指标');
    const title = `全局模型评估 · ${metric.label}`;
    const jobId = job && job.job_id != null ? String(job.job_id) : '未取得';
    const idLines = jobId.match(/[\s\S]{1,68}/g) || [''];
    const left = 90, right = 770, top = Math.max(82, 70 + (idLines.length - 1) * 12), bottom = top + 260;
    const children = [node('title', {}, [], `${title} · 作业 ${jobId}`),
      node('desc', {}, [], points.filter(point => point[key] === null)
        .map(point => `M${point.round_index} ${metric.label}未取得：${point.reason || '原因未取得'}`).join('；')),
      node('rect', {x:0,y:0,width:800,height:bottom+78,fill:'#ffffff'}),
      node('text', {x: 400, y: 28, 'text-anchor':'middle', 'font-size':18}, [], title)];
    idLines.forEach((line, index) => children.push(node('text',
      {x:400,y:52 + index * 12,'text-anchor':'middle','font-size':10}, [], `${index === 0 ? '作业 ' : ''}${line}`)));
    const maxRound = Math.max(1, ...points.map(point => point.round_index));
    const values = points.filter(point => point[key] !== null).map(point => point[key] * metric.scale);
    // 固定百分比坐标；损失从零起，负值亦保留真实坐标。
    const minValue = key === 'accuracy' ? 0 : Math.min(0, ...values);
    const maxValue = key === 'accuracy' ? 100 : Math.max(1, ...values);
    // 按最大绝对值归一化，避免两个有限极值相减产生溢出。
    const norm = Math.max(Math.abs(minValue), Math.abs(maxValue));
    const low = minValue / norm, high = maxValue / norm;
    const x = round => left + (round / maxRound) * (right - left);
    const y = value => bottom - ((value / norm - low) / (high - low)) * (bottom - top);
    children.push(node('line', {x1:left, y1:bottom, x2:right, y2:bottom, stroke:'currentColor'}),
      node('line', {x1:left, y1:top, x2:left, y2:bottom, stroke:'currentColor'}),
      node('text', {x:430, y:bottom+54, 'text-anchor':'middle', 'font-size':14}, [], '通信轮次（M0 为初始模型）'),
      node('text', {x:24, y:(top+bottom)/2, transform:`rotate(-90 24 ${(top+bottom)/2})`, 'text-anchor':'middle', 'font-size':14}, [], metric.axis));
    for (let tick = 0; tick <= 4; tick++) {
      const fraction = tick / 4;
      const value = norm * (low * (1 - fraction) + high * fraction);
      const cy = bottom - fraction * (bottom - top);
      const label = value.toPrecision(4);
      children.push(node('line', {x1:left,y1:cy,x2:right,y2:cy,stroke:'currentColor','stroke-opacity':0.15}),
        node('text', {x:left-8,y:cy+4,'text-anchor':'end','font-size':12}, [],
          Number.isFinite(Number(label)) ? String(Number(label)) : label));
    }
    const ticks = new Set([0, maxRound]);
    for (let tick = 1; tick < 5; tick++) ticks.add(Math.round(maxRound * tick / 5));
    for (const tick of [...ticks].sort((a,b)=>a-b)) {
      children.push(node('text', {x:x(tick),y:bottom+23,'text-anchor':'middle','font-size':12}, [], String(tick)));
    }
    let previous = null;
    for (const point of points) {
      if (point[key] === null) { previous = null; continue; }
      const cx = x(point.round_index), cy = y(point[key] * metric.scale);
      if (previous && point.round_index === previous.round_index + 1) {
        children.push(node('path', {d:`M ${x(previous.round_index)} ${y(previous[key] * metric.scale)} L ${cx} ${cy}`,
          fill:'none',stroke:'#2563eb','stroke-width':2}));
      }
      previous = point;
    }
    for (const point of points.filter(point => point[key] !== null)) {
      const circle = node('circle', {cx:x(point.round_index),cy:y(point[key] * metric.scale),r:5,
        fill:'#2563eb',stroke:'white','stroke-width':1,tabindex:0,role:'button','aria-label':detail(point)},
      [node('title', {}, [], detail(point))]);
      circle.point = point;
      const hit = node('circle', {cx:circle.attrs.cx,cy:circle.attrs.cy,r:18,
        fill:'transparent',stroke:'none','pointer-events':'all','aria-hidden':'true'});
      hit.point = point;
      children.push(hit, circle);
    }
    const reason = unavailable(job, key, points);
    if (reason) children.push(node('text', {x:430,y:(top+bottom)/2,'text-anchor':'middle','font-size':13}, [], reason));
    return node('svg', {xmlns:NS,viewBox:`0 0 800 ${bottom+78}`,width:'100%',role:'img','aria-label':title,
      'font-family':'sans-serif',color:'#172033',fill:'#172033',
      style:'font-family: sans-serif; color: #172033; background: #ffffff'}, children);
  }

  function serialize(tree) {
    const attrs = Object.entries(tree.attrs).map(([key,value]) => ` ${key}="${xmlEscape(value)}"`).join('');
    return `<${tree.tag}${attrs}>${xmlEscape(tree.text)}${tree.children.map(serialize).join('')}</${tree.tag}>`;
  }

  function renderSVG(job, key) {
    return serialize(scene(job, key, modelPoints(job)));
  }

  function toCSV(job) {
    const points = modelPoints(job);
    if (!points.some(point => point.loss !== null || point.accuracy !== null)) return '';
    const columns = ['job_id','round_index','model_sha256','loss','accuracy','verdict','reason'];
    const escape = value => {
      const text = value == null ? '' : String(value);
      return /[",\r\n]/.test(text) ? '"' + text.replace(/"/g, '""') + '"' : text;
    };
    return [columns.join(','), ...points.map(point => columns.map(key => escape(key === 'job_id' ? job.job_id : point[key])).join(','))].join('\r\n') + '\r\n';
  }

  function download(contents, type, filename) {
    const url = URL.createObjectURL(new Blob([contents], {type}));
    try {
      const anchor = document.createElement('a');
      anchor.href = url;
      anchor.download = filename;
      anchor.click();
    } finally { URL.revokeObjectURL(url); }
  }

  function render(container, job) {
    const points = modelPoints(job);
    // 同一快照的轮询不替换 DOM，保留焦点、触屏选择和详情。
    const fingerprint = JSON.stringify([!!job, applicable(job), job && job.job_id, points]);
    const previous = renderedSnapshots.get(container);
    if (previous && previous.fingerprint === fingerprint && container.children[0] === previous.root) return;
    const sameJob = previous && job && job.job_id != null && previous.jobId === job.job_id;
    const active = document.activeElement;
    const focused = sameJob && active && previous.root.contains(active) ?
      previous.focusTargets.find(target => target.el === active) : null;
    const root = document.createElement('section');
    const state = {fingerprint, root, jobId:job && job.job_id,
      selectedRound:sameJob ? previous.selectedRound : null, focusTargets:[]};
    root.className = 'evaluation-curves';
    const heading = document.createElement('h3');
    heading.textContent = '全局模型评估';
    const details = document.createElement('p');
    details.className = 'evaluation-curves-details';
    details.setAttribute('aria-live','polite');
    details.textContent = '悬停、聚焦或点击数据点查看详情。';
    function makeDOM(tree, metric) {
      const el = document.createElementNS(NS, tree.tag);
      for (const [key, value] of Object.entries(tree.attrs)) el.setAttribute(key, String(value));
      el.textContent = tree.text;
      for (const child of tree.children) el.append(makeDOM(child, metric));
      if (tree.point) {
        if (tree.attrs.tabindex === 0) state.focusTargets.push({el,metric,round:tree.point.round_index});
        const show = () => {
          state.selectedRound = tree.point.round_index;
          details.textContent = detail(tree.point);
        };
        for (const event of ['mouseenter', 'focus', 'click']) el.addEventListener(event, show);
        el.addEventListener('keydown', event => {
          if (event.key === 'Enter' || event.key === ' ') { event.preventDefault(); show(); }
        });
      }
      return el;
    }
    const gaps = [];
    let lastRound = -1;
    for (const point of points) {
      for (let round = lastRound + 1; round < point.round_index; round++) {
        gaps.push(`M${round} 评估未取得（原因未取得）`);
      }
      lastRound = point.round_index;
    }
    const safeId = String(job && job.job_id || 'job').replace(/[^a-zA-Z0-9_-]/g, '_').slice(0, 64);
    root.append(heading);
    for (const key of ['accuracy', 'loss']) {
      const figure = document.createElement('figure');
      figure.className = `evaluation-curves-figure evaluation-curves-${key}`;
      const tree = scene(job, key, points);
      const button = document.createElement('button');
      button.type = 'button';
      button.className = 'evaluation-curves-download';
      button.textContent = `下载${metrics[key].label} SVG`;
      const reason = unavailable(job, key, points);
      button.disabled = !!reason;
      button.title = reason || button.textContent;
      // 导出捕获当前渲染的同一快照，后续调用 render 时整体替换。
      const svg = serialize(tree);
      button.addEventListener('click', () => {
        if (!button.disabled) download(svg, 'image/svg+xml;charset=utf-8', `${safeId}-${key}.svg`);
      });
      const note = document.createElement('figcaption');
      const absent = points.filter(point => point[key] === null);
      note.className = 'evaluation-curves-note';
      note.textContent = [reason || `${metrics[key].label} · ${metrics[key].axis}`,
        ...absent.map(point => `M${point.round_index} ${metrics[key].label}未取得：${point.reason || '原因未取得'}`),
        ...gaps].join('\n');
      figure.append(makeDOM(tree, key), note, button);
      root.append(figure);
    }
    root.append(details);
    const missing = points.filter(point => point.loss === null || point.accuracy === null);
    if (missing.length) {
      const note = document.createElement('p');
      note.className = 'evaluation-curves-missing';
      note.textContent = missing.map(detail).join('\n');
      root.append(note);
    }
    const csv = toCSV(job);
    const button = document.createElement('button');
    button.type = 'button';
    button.className = 'evaluation-curves-download';
    button.textContent = '下载评估 CSV（原始数值）';
    button.disabled = !csv;
    button.title = csv ? button.textContent : unavailable(job, 'accuracy', points);
    button.addEventListener('click', () => {
      if (!button.disabled) download(csv, 'text/csv;charset=utf-8', `${safeId}-evaluation.csv`);
    });
    root.append(button);
    const selected = points.find(point => point.round_index === state.selectedRound);
    container.replaceChildren(root);
    renderedSnapshots.set(container, state);
    if (focused) {
      const target = state.focusTargets.find(target => target.metric === focused.metric && target.round === focused.round);
      if (target) target.el.focus({preventScroll:true});
    }
    // 恢复焦点会触发 focus 事件；最后恢复用户选中的详情，二者可以不同。
    if (selected) {
      state.selectedRound = selected.round_index;
      details.textContent = detail(selected);
    }
  }

  globalThis.EvaluationCurves = {render, renderSVG, modelPoints, toCSV, xmlEscape, applicable};
}());
