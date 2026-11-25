// The Music FFT layer graph (web-control-refactor M5 slice 2, D-058–D-060): one
// layer's range strip, detail graph, and floor lane. The number fields stay the
// source of truth (fft_editor.js): a drag or a key calls back with new values,
// the editor writes them into the fields, and its refresh redraws the graph from
// them. Rules and scales are in programs/fft_layout.js.
//
// Only the handles take input; a touch anywhere else scrolls the page. Handles
// are persistent elements, so a focused one keeps focus while the graph redraws.

import {exactHz, formatHz, positionHz} from './programs/fft.js';
import * as rules from './programs/fft_layout.js';
import {h} from './ui.js';

const SVG_NS = 'http://www.w3.org/2000/svg';
const STRIP_HEIGHT = 56;
const FLOOR_HEIGHT = 46;
const PICK_RADIUS = 26; // the nearest handle within this many px wins a press
const DRAG_THRESHOLD = 3; // px of travel before a press edits; a tap only selects
let graphCounter = 0;

function s(tag, attributes = {}, text) {
  const node = document.createElementNS(SVG_NS, tag);
  for (const [name, value] of Object.entries(attributes)) {
    if (value !== undefined && value !== null && value !== false) node.setAttribute(name, String(value));
  }
  if (text !== undefined) node.textContent = text;
  return node;
}

const fixed = value => Number(value).toFixed(1);

function hit(key, shape, attributes) {
  return s(shape, {class: `graph-hit ${attributes.kind}`, 'data-key': key, tabindex: 0, role: 'slider',
    'aria-label': attributes.label});
}

// Callbacks: onRange({min_freq?, max_freq?}), onFloor(value), onPoint(id, {position,
// weight}), onSelect(id or null), onRemove(id).
export function createLayerGraph({onRange, onFloor, onPoint, onSelect, onRemove}) {
  const number = ++graphCounter;
  const hatchId = `fft-hatch-${number}`;

  let model = null;
  let drag = null;
  let tipKey = null;

  // ---- Elements -------------------------------------------------------------

  const stripSvg = s('svg', {class: 'graph graph-strip', height: STRIP_HEIGHT, 'aria-label': 'Frequency range on the spectrum'});
  const stripBase = s('g');
  const stripHits = s('g');
  const bandHit = hit('band', 'rect', {kind: 'hit-band', label: 'Slide the whole range'});
  const minHit = hit('min', 'rect', {kind: 'hit-end', label: 'Min Hz'});
  const maxHit = hit('max', 'rect', {kind: 'hit-end', label: 'Max Hz'});
  stripHits.append(bandHit, minHit, maxHit);
  stripSvg.append(stripBase, stripHits);

  const detailSvg = s('svg', {class: 'graph graph-detail', height: rules.DETAIL_HEIGHT,
    'aria-label': "Weighting curve over the layer's bars"});
  const hatch = s('pattern', {id: hatchId, width: 6, height: 6, patternUnits: 'userSpaceOnUse',
    patternTransform: 'rotate(45)'});
  hatch.append(s('line', {class: 'hatch-line', x1: 0, y1: 0, x2: 0, y2: 6}));
  const defs = s('defs');
  defs.append(hatch);
  const detailBase = s('g');
  const detailDots = s('g');
  const detailHits = s('g');
  detailSvg.append(defs, detailBase, detailDots, detailHits);
  const pointHits = new Map(); // point id -> hit circle

  const floorSvg = s('svg', {class: 'graph graph-floor', height: FLOOR_HEIGHT, 'aria-label': 'Noise floor'});
  const floorBase = s('g');
  const floorHit = hit('floor', 'circle', {kind: 'hit-floor', label: 'Noise floor'});
  floorSvg.append(floorBase, floorHit);

  const tip = () => h('div', {className: 'graph-tip', hidden: true});
  const stripWrap = h('div', {className: 'graph-wrap'}, stripSvg, tip());
  const detailWrap = h('div', {className: 'graph-wrap'}, detailSvg, tip());
  const floorWrap = h('div', {className: 'graph-wrap'}, floorSvg, tip());
  const stripCaption = h('p', {className: 'graph-caption'});
  const detailCaption = h('p', {className: 'graph-caption'});
  const wraps = [stripWrap, detailWrap, floorWrap];

  // ---- Drawing ---------------------------------------------------------------

  function sizeSvg(svg, wrap, height) {
    const width = Math.round(wrap.clientWidth);
    if (width < 120) return 0; // hidden (a closed layer) or not laid out yet
    svg.setAttribute('width', width);
    svg.setAttribute('viewBox', `0 0 ${width} ${height}`);
    return width;
  }

  // `values` is [now, min, max] for the slider role.
  function setHit(element, attributes, {valueText, disabled, values}) {
    for (const [name, value] of Object.entries(attributes)) element.setAttribute(name, String(value));
    element.setAttribute('aria-valuetext', valueText);
    ['aria-valuenow', 'aria-valuemin', 'aria-valuemax'].forEach((name, index) => {
      if (Number.isFinite(values[index])) element.setAttribute(name, String(values[index]));
      else element.removeAttribute(name);
    });
    element.classList.toggle('inert', Boolean(disabled));
    element.setAttribute('tabindex', disabled ? '-1' : '0');
    if (disabled) element.setAttribute('aria-disabled', 'true');
    else element.removeAttribute('aria-disabled');
  }

  function nyquist() {
    return rules.nyquistOf(model.context);
  }

  // The highest Max Hz a drag may set: Nyquist, or the field's maximum if lower.
  function topHz() {
    return Math.min(nyquist() ?? Infinity, model.maxHz ?? Infinity);
  }

  function drawStrip() {
    const width = sizeSvg(stripSvg, stripWrap, STRIP_HEIGHT);
    if (!width) return;
    const {minFreq, maxFreq} = model;
    const top = nyquist();
    const span = drag?.svg === stripSvg ? [drag.scaleMin, drag.scaleMax] : [minFreq, maxFreq];
    const scale = rules.stripScale(width, span[0], span[1], top);
    const out = [s('rect', {class: 'strip-track', x: scale.left, y: 12, width: scale.right - scale.left, height: 22, rx: 6})];
    if (top && maxFreq > top) {
      const x = scale.x(top);
      out.push(s('rect', {class: `strip-over${model.context.assumed.has('sample_rate') ? ' assumed' : ''}`,
        x: fixed(x), y: 12, width: fixed(scale.right - x), height: 22}));
    }
    const labelled = width < 460 ? new Set([20, 100, 1000, 10000])
      : new Set([20, 50, 100, 200, 500, 1000, 2000, 5000, 10000, 20000]);
    for (const frequency of rules.NICE_HZ) {
      if (frequency < scale.low || frequency > scale.high) continue;
      const x = fixed(scale.x(frequency));
      out.push(s('line', {class: 'grid-line minor', x1: x, y1: 12, x2: x, y2: 34}));
      if (labelled.has(frequency)) out.push(s('text', {class: 'axis', x, y: 50, 'text-anchor': 'middle'}, rules.hzTick(frequency)));
    }
    const x0 = scale.x(minFreq);
    const x1 = scale.x(maxFreq);
    out.push(s('rect', {class: 'strip-band', x: fixed(x0), y: 12, width: fixed(Math.max(1, x1 - x0)), height: 22}));
    for (const x of [x0, x1]) out.push(s('rect', {class: 'strip-end', x: fixed(x - 2.5), y: 6, width: 5, height: 34, rx: 2.5}));
    stripBase.replaceChildren(...out);

    const disabled = model.disabled.range;
    const range = `${formatHz(minFreq)} to ${formatHz(maxFreq)}`;
    setHit(bandHit, {x: fixed(x0 + 22), y: 6, width: fixed(Math.max(0, x1 - x0 - 44)), height: 34},
      {valueText: range, disabled, values: [minFreq, rules.STRIP_LOW_HZ, topHz()]});
    bandHit.classList.toggle('off', x1 - x0 <= 60);
    setHit(minHit, {x: fixed(x0 - 22), y: 0, width: 44, height: 46, 'data-cx': fixed(x0), 'data-cy': 23},
      {valueText: formatHz(minFreq), disabled, values: [minFreq, rules.STRIP_LOW_HZ, maxFreq]});
    setHit(maxHit, {x: fixed(x1 - 22), y: 0, width: 44, height: 46, 'data-cx': fixed(x1), 'data-cy': 23},
      {valueText: formatHz(maxFreq), disabled, values: [maxFreq, minFreq, topHz()]});

    const rate = model.context.sampleRate;
    stripCaption.textContent = top
      ? `Spectrum 20 Hz to ${exactHz(top)}, the Nyquist frequency at ${formatHz(rate)}` +
        `${model.context.assumed.has('sample_rate') ? ' (assumed until launch)' : ''}. ` +
        'Drag an end, or drag the band to slide the range.'
      : 'Drag an end, or drag the band to slide the range.';
  }

  function drawDetail() {
    const width = sizeSvg(detailSvg, detailWrap, rules.DETAIL_HEIGHT);
    if (!width) return;
    const {minFreq, maxFreq, points, context} = model;
    const scale = rules.detailScale(width);
    const sorted = rules.byPosition(points);
    const layout = rules.projectionLayout(minFreq, maxFreq, sorted, context.sampleRate, context.fftSize, context.numBars);
    const out = [
      s('rect', {class: 'off-row', x: scale.left, y: scale.offTop, width: scale.right - scale.left,
        height: scale.offBottom - scale.offTop, rx: 4}),
      s('text', {class: 'axis', x: scale.left - 6, y: (scale.offTop + scale.offBottom) / 2 + 4, 'text-anchor': 'end'}, 'off'),
    ];
    for (const weight of [0.2, 0.5, 2, 5, 20, 50]) {
      const y = fixed(scale.y(weight));
      out.push(s('line', {class: 'grid-line minor', x1: scale.left, y1: y, x2: scale.right, y2: y}));
    }
    for (const weight of [0.1, 1, 10, 100]) {
      const y = scale.y(weight);
      out.push(s('line', {class: 'grid-line', x1: scale.left, y1: fixed(y), x2: scale.right, y2: fixed(y)}));
      out.push(s('text', {class: 'axis', x: scale.left - 6, y: fixed(y + 4), 'text-anchor': 'end'}, String(weight)));
    }
    const bars = layout ? layout.weights.length : 0;
    const barWidth = bars ? (scale.right - scale.left) / bars : 0;
    for (let index = 0; index < bars; index += 1) {
      const x = (scale.left + index * barWidth + 0.5).toFixed(2);
      const w = Math.max(0.6, barWidth - 1).toFixed(2);
      const weight = layout.weights[index];
      if (layout.binCounts[index] === 0) {
        out.push(s('rect', {class: 'bar-empty', fill: `url(#${hatchId})`, x, y: scale.top, width: w,
          height: scale.logBottom - scale.top}));
      } else if (weight >= rules.WEIGHT_AXIS[0]) {
        const y = scale.y(weight);
        out.push(s('rect', {class: 'bar', x, y: fixed(y), width: w, height: fixed(scale.logBottom - y)}));
      } else if (weight > 0) {
        out.push(s('rect', {class: 'bar-low', x, y: scale.logBottom - 2, width: w, height: 2}));
      } else {
        out.push(s('rect', {class: 'bar-off', x, y: (scale.offTop + scale.offBottom) / 2 - 1, width: w, height: 2}));
      }
    }
    if (sorted.length) {
      const samples = Math.max(64, Math.round((scale.right - scale.left) / 2));
      const xp = sorted.map(point => point.position);
      const fp = sorted.map(point => point.weight);
      let d = '';
      for (let k = 0; k <= samples; k += 1) {
        const position = k / samples;
        d += `${k ? 'L' : 'M'}${fixed(scale.x(position))} ${fixed(scale.y(rules.interp(position, xp, fp)))}`;
      }
      out.push(s('path', {class: 'curve', d}));
    }
    // Frequency labels: the range ends, then 1-2-5 values with room between them.
    const used = [scale.left, scale.right];
    out.push(s('text', {class: 'axis strong', x: scale.left, y: 203, 'text-anchor': 'start'}, formatHz(minFreq)));
    out.push(s('text', {class: 'axis strong', x: scale.right, y: 203, 'text-anchor': 'end'}, formatHz(maxFreq)));
    const span = Math.log10(maxFreq / minFreq);
    for (const frequency of rules.NICE_HZ) {
      if (frequency <= minFreq || frequency >= maxFreq) continue;
      const x = scale.x(Math.log10(frequency / minFreq) / span);
      if (used.some(other => Math.abs(other - x) < 62)) continue;
      used.push(x);
      out.push(s('line', {class: 'grid-line minor', x1: fixed(x), y1: scale.offBottom, x2: fixed(x), y2: scale.offBottom + 5}));
      out.push(s('text', {class: 'axis', x: fixed(x), y: 203, 'text-anchor': 'middle'}, rules.hzTick(frequency)));
    }
    detailBase.replaceChildren(...out);

    // Points: dots (and the selected ring) under persistent, thumb-sized targets.
    const dots = [];
    const disabled = model.disabled.points;
    const seen = new Set();
    sorted.forEach((point, index) => {
      const cx = fixed(scale.x(point.position));
      const cy = fixed(scale.y(point.weight));
      const selected = point.id === model.selectedId;
      if (selected) dots.push(s('circle', {class: 'point-ring', cx, cy, r: 11}));
      dots.push(s('circle', {class: `point-dot${selected ? ' selected' : ''}`, cx, cy, r: selected ? 7 : 6}));
      let element = pointHits.get(point.id);
      if (!element) {
        element = hit(`pt:${point.id}`, 'circle', {kind: 'hit-point', label: ''});
        pointHits.set(point.id, element);
        detailHits.append(element);
      }
      seen.add(point.id);
      element.setAttribute('aria-label', `Point ${index + 1}`);
      setHit(element, {cx, cy, r: 22, 'data-cx': cx, 'data-cy': cy}, {
        valueText: `position ${point.position}, about ${formatHz(positionHz(minFreq, maxFreq, point.position))}, weight ${point.weight}`,
        disabled,
        values: [point.position, model.positionRange[0], model.positionRange[1]],
      });
    });
    for (const [id, element] of pointHits) {
      if (seen.has(id)) continue;
      element.remove();
      pointHits.delete(id);
      if (tipKey === `pt:${id}`) tipKey = null;
    }
    // Tab order follows position. Moving a focused node blurs it, so only reorder
    // when the order changed (drags and nudges keep it) and restore focus after.
    const wanted = sorted.map(point => pointHits.get(point.id));
    if (wanted.some((element, index) => detailHits.children[index] !== element)) {
      const focused = document.activeElement;
      detailHits.append(...wanted);
      if (wanted.includes(focused)) focused.focus({preventScroll: true});
    }
    detailDots.replaceChildren(...dots);

    const parts = [];
    const count = context.numBars;
    if (layout) {
      parts.push(`${count} bars${context.assumed.has('num_bars') ? ' (assumed until launch)' : ''}, ` +
        'each drawn at its weight; the curve is linear between points and flat beyond the first and last.');
      const empty = rules.emptyBars(layout);
      if (empty) {
        parts.push(`${empty} of ${count} bars are hatched: narrower than one ${layout.binHz.toFixed(1)} Hz FFT bin, ` +
          'so they always read 0.');
      }
    } else {
      parts.push('The curve is linear between points and flat beyond the first and last.');
    }
    detailCaption.textContent = parts.join(' ');
  }

  function drawFloor() {
    const width = sizeSvg(floorSvg, floorWrap, FLOOR_HEIGHT);
    if (!width) return;
    const scale = rules.floorScale(width);
    const value = model.floor;
    const hx = scale.x(value);
    const out = [
      s('rect', {class: 'zero-zone', x: scale.zeroLeft, y: scale.y - 9, width: scale.zeroRight - scale.zeroLeft, height: 18, rx: 9}),
      s('text', {class: 'axis', x: (scale.zeroLeft + scale.zeroRight) / 2, y: 42, 'text-anchor': 'middle'}, '0'),
      s('line', {class: 'floor-track', x1: scale.left, y1: scale.y, x2: scale.right, y2: scale.y}),
    ];
    if (value > 0) out.push(s('line', {class: 'floor-fill', x1: scale.left, y1: scale.y, x2: fixed(hx), y2: scale.y}));
    for (const tick of [0.01, 0.1, 1, 10, 100]) {
      const x = fixed(scale.x(tick));
      out.push(s('line', {class: 'grid-line', x1: x, y1: scale.y + 6, x2: x, y2: scale.y + 10}));
      out.push(s('text', {class: 'axis', x, y: 42, 'text-anchor': 'middle'}, String(tick)));
    }
    out.push(s('circle', {class: 'floor-handle', cx: fixed(hx), cy: scale.y, r: 8}));
    floorBase.replaceChildren(...out);
    setHit(floorHit, {cx: fixed(hx), cy: scale.y, r: 22, 'data-cx': fixed(hx), 'data-cy': scale.y},
      {valueText: String(value), disabled: model.disabled.floor, values: [value, 0, rules.FLOOR_AXIS[1]]});
  }

  function draw() {
    if (!model) return;
    drawStrip();
    drawDetail();
    drawFloor();
    refreshTip();
  }

  // ---- Value tips ------------------------------------------------------------

  function tipText(key) {
    if (!model) return '';
    if (key === 'min') return `Min ${formatHz(model.minFreq)}`;
    if (key === 'max') return `Max ${formatHz(model.maxFreq)}`;
    if (key === 'band') return `${formatHz(model.minFreq)} – ${formatHz(model.maxFreq)}`;
    if (key === 'floor') return `Noise floor ${model.floor}`;
    const point = model.points.find(item => `pt:${item.id}` === key);
    if (!point) return '';
    return `${point.position.toFixed(3)} · ≈ ${formatHz(positionHz(model.minFreq, model.maxFreq, point.position))} · ` +
      `weight ${point.weight}`;
  }

  function showTip(key) {
    tipKey = key;
    refreshTip();
  }

  function refreshTip() {
    for (const wrap of wraps) {
      const box = wrap.querySelector('.graph-tip');
      const target = tipKey ? wrap.querySelector(`.graph-hit[data-key="${tipKey}"]`) : null;
      if (!target || target.classList.contains('off')) {
        box.hidden = true;
        continue;
      }
      let cx;
      let cy;
      if (target.dataset.cx) {
        cx = Number(target.dataset.cx);
        cy = Number(target.dataset.cy) - 12;
      } else {
        cx = Number(target.getAttribute('x')) + Number(target.getAttribute('width')) / 2;
        cy = Number(target.getAttribute('y'));
      }
      box.textContent = tipText(tipKey);
      box.hidden = false;
      const half = box.offsetWidth / 2;
      box.style.left = `${rules.clamp(cx, half + 2, wrap.clientWidth - half - 2)}px`;
      box.style.top = `${Math.max(cy, box.offsetHeight + 10)}px`;
    }
  }

  // ---- Pointer input -----------------------------------------------------------

  // The drawn width (sizeSvg's): the wrapper's, since an <svg> may report none.
  const widthOf = svg => Math.round(svg.parentElement.clientWidth);

  function localPoint(svg, event) {
    const box = svg.getBoundingClientRect();
    return {x: event.clientX - box.left, y: event.clientY - box.top};
  }

  // The nearest handle within reach wins, so close points stay grabbable.
  function pickHandle(svg, event, target) {
    const {x, y} = localPoint(svg, event);
    let best = null;
    let bestDistance = PICK_RADIUS;
    for (const element of svg.querySelectorAll('.graph-hit[data-cx]:not(.inert):not(.off)')) {
      const distance = Math.hypot(Number(element.dataset.cx) - x, Number(element.dataset.cy) - y);
      if (distance < bestDistance) {
        best = element;
        bestDistance = distance;
      }
    }
    return best || target;
  }

  const pointId = key => Number(String(key).slice(3));
  const isPoint = key => String(key).startsWith('pt:');

  function rangeNow() {
    return {minFreq: model.minFreq, maxFreq: model.maxFreq, top: topHz()};
  }

  // Applies values locally so the next move builds on them, then reports them.
  function applyRange(changes) {
    if (changes.min_freq !== undefined) model.minFreq = changes.min_freq;
    if (changes.max_freq !== undefined) model.maxFreq = changes.max_freq;
    onRange(changes);
  }

  function applyFloor(value) {
    model.floor = value;
    onFloor(value);
  }

  function applyPoint(id, values) {
    const point = model.points.find(item => item.id === id);
    if (!point) return;
    Object.assign(point, values);
    onPoint(id, values);
  }

  // A press edits only after DRAG_THRESHOLD px of travel, and then by the
  // pointer's travel from the handle's center (the grab offset), so a tap or an
  // off-center press never jumps a value.
  function moveDrag(event) {
    const point = localPoint(drag.svg, event);
    if (!drag.moved) {
      if (Math.hypot(point.x - drag.startX, point.y - drag.startY) < DRAG_THRESHOLD) return;
      drag.moved = true;
      if (drag.key === 'ends') {
        // Both range ends were in reach: the first move's direction picks one.
        const chosen = point.x < drag.startX ? minHit : maxHit;
        drag.key = chosen.dataset.key;
        drag.offsetX = drag.startX - Number(chosen.dataset.cx);
        chosen.focus({preventScroll: true});
        showTip(drag.key);
      }
    }
    const x = point.x - drag.offsetX;
    const y = point.y - drag.offsetY;
    const width = widthOf(drag.svg);
    const key = drag.key;
    if (key === 'min' || key === 'max') {
      const scale = rules.stripScale(width, drag.scaleMin, drag.scaleMax, nyquist());
      const frequency = scale.f(x);
      applyRange(key === 'min' ? rules.setMin(frequency, rangeNow()) : rules.setMax(frequency, rangeNow()));
    } else if (key === 'band') {
      applyRange(rules.slideRange((point.x - drag.startX) / drag.pxPerDecade,
        {minFreq: drag.startMin, maxFreq: drag.startMax, top: topHz()}));
    } else if (key === 'floor') {
      applyFloor(rules.dragFloor(x, rules.floorScale(width)));
    } else if (isPoint(key)) {
      const id = pointId(key);
      const current = model.points.find(item => item.id === id);
      if (!current) return;
      const scale = rules.detailScale(width);
      const bounds = rules.pointBounds(model.points, id, model.positionRange[0], model.positionRange[1]);
      applyPoint(id, rules.dragPoint(scale.p(x), scale.w(y), bounds, current.position));
    }
  }

  function disabledKey(key) {
    if (!model) return true;
    if (key === 'floor') return model.disabled.floor;
    if (isPoint(key)) return model.disabled.points;
    return model.disabled.range;
  }

  function attach(svg) {
    svg.addEventListener('pointerdown', event => {
      const target = event.target.closest?.('.graph-hit');
      if (!target || event.button > 0 || !model) return;
      const element = pickHandle(svg, event, target);
      const key = element.dataset.key;
      if (disabledKey(key)) return;
      event.preventDefault();
      const {x, y} = localPoint(svg, event);
      const centerX = element.dataset.cx !== undefined ? Number(element.dataset.cx) : x;
      const centerY = element.dataset.cy !== undefined ? Number(element.dataset.cy) : y;
      // The strip's scale is frozen for the drag (drawn and read), so its ends
      // don't shift under the pointer when the range leaves 20 Hz–Nyquist.
      drag = {svg, key, pointerId: event.pointerId, startX: x, startY: y, moved: false,
        offsetX: x - centerX, offsetY: y - centerY,
        startMin: model.minFreq, startMax: model.maxFreq, scaleMin: model.minFreq, scaleMax: model.maxFreq};
      const other = key === 'min' ? maxHit : key === 'max' ? minHit : null;
      if (other && Math.abs(Number(other.dataset.cx) - x) < PICK_RADIUS) drag.key = 'ends';
      if (key === 'band') {
        drag.pxPerDecade = rules.stripScale(widthOf(svg), model.minFreq, model.maxFreq, nyquist()).pxPerDecade;
      }
      svg.setPointerCapture(event.pointerId);
      svg.classList.add('dragging');
      element.focus({preventScroll: true});
      if (isPoint(key) && model.selectedId !== pointId(key)) onSelect(pointId(key));
      showTip(key);
    });
    svg.addEventListener('pointermove', event => {
      if (drag && drag.pointerId === event.pointerId) {
        moveDrag(event);
        return;
      }
      if (drag || event.pointerType !== 'mouse') return;
      const target = event.target.closest?.('.graph-hit');
      const focusedKey = document.activeElement?.dataset?.key;
      if (target && !target.classList.contains('inert')) {
        const key = pickHandle(svg, event, target).dataset.key;
        if (tipKey !== key) showTip(key);
      } else if (tipKey && tipKey !== focusedKey && svg.querySelector(`[data-key="${tipKey}"]`)) {
        showTip(null); // the mouse left this graph's handle
      }
    });
    const end = event => {
      if (!drag || drag.pointerId !== event.pointerId) return;
      svg.classList.remove('dragging');
      const rescale = drag.svg === stripSvg;
      drag = null;
      if (rescale) draw(); // the strip may now span a different range
      if (event.pointerType !== 'mouse' && document.activeElement?.dataset?.key !== tipKey) showTip(null);
    };
    svg.addEventListener('pointerup', end);
    svg.addEventListener('pointercancel', end);
    svg.addEventListener('lostpointercapture', end);
    svg.addEventListener('pointerleave', event => {
      if (!drag && event.pointerType === 'mouse' && tipKey && document.activeElement?.dataset?.key !== tipKey) showTip(null);
    });
    // iOS starts a scroll before pointer events can claim a touch on a handle.
    svg.addEventListener('touchstart', event => {
      const target = event.target.closest?.('.graph-hit');
      if (target && !target.classList.contains('inert')) event.preventDefault();
    }, {passive: false});
    svg.addEventListener('touchmove', event => {
      if (drag) event.preventDefault();
    }, {passive: false});

    svg.addEventListener('focusin', event => {
      const key = event.target.dataset?.key;
      if (!key) return;
      if (isPoint(key) && model?.selectedId !== pointId(key)) onSelect(pointId(key));
      showTip(key);
    });
    svg.addEventListener('focusout', () => {
      setTimeout(() => {
        if (!drag && !document.activeElement?.classList?.contains('graph-hit')) showTip(null);
      }, 0);
    });
    svg.addEventListener('keydown', event => {
      const key = event.target.dataset?.key;
      if (!key || !model || disabledKey(key)) return;
      if (handleKey(key, event)) event.preventDefault();
    });
  }

  // Arrow keys nudge (Shift for more); Delete removes a point; Esc deselects.
  function handleKey(key, event) {
    if (event.key === 'Escape') {
      onSelect(null);
      event.target.blur();
      return true;
    }
    if (isPoint(key)) {
      const id = pointId(key);
      if (event.key === 'Delete' || event.key === 'Backspace') {
        const sorted = rules.byPosition(model.points);
        const index = sorted.findIndex(item => item.id === id);
        const neighbor = sorted[index + 1] || sorted[index - 1];
        onRemove(id); // the editor redraws at once
        // The removed handle held focus; keep the keyboard on the graph.
        if (!pointHits.has(id) && neighbor) pointHits.get(neighbor.id)?.focus({preventScroll: true});
        return true;
      }
      const point = model.points.find(item => item.id === id);
      const bounds = rules.pointBounds(model.points, id, model.positionRange[0], model.positionRange[1]);
      const next = point && rules.nudgePoint(point, event.key, event.shiftKey, bounds);
      if (!next) return false;
      applyPoint(id, next);
      return true;
    }
    const up = event.key === 'ArrowUp' || event.key === 'ArrowRight';
    const down = event.key === 'ArrowDown' || event.key === 'ArrowLeft';
    if (!up && !down) return false;
    if (key === 'floor') applyFloor(rules.nudgeFloor(model.floor, up, event.shiftKey));
    else applyRange(rules.nudgeRange(key, up, event.shiftKey, rangeNow()));
    return true;
  }

  for (const svg of [stripSvg, detailSvg, floorSvg]) attach(svg);
  // A closed layer has no width; opening it (or a wider column) redraws.
  const observer = typeof ResizeObserver === 'function' ? new ResizeObserver(() => draw()) : null;
  for (const wrap of wraps) observer?.observe(wrap);

  return {
    // The parts go in different places in the layer's Range & weighting group.
    strip: h('div', {className: 'graph-part'}, stripWrap, stripCaption),
    detail: h('div', {className: 'graph-part'}, detailWrap, detailCaption),
    floor: floorWrap,

    // `next`: {minFreq, maxFreq, floor, points: [{id, position, weight}],
    // selectedId, context (binContext), disabled: {range, floor, points},
    // positionRange: [low, high]}.
    render(next) {
      model = {...next, points: next.points.map(point => ({...point}))};
      draw();
    },

    // When the layer's row goes away (a reload or Remove).
    destroy() {
      observer?.disconnect();
    },
  };
}
