// Music FFT layer graph rules (web-control-refactor M5 slice 2, D-058–D-060).
// Pure, so node --test covers it; fft_graph.js draws from it.
//
// projectionLayout is core.analysis_plan.projection_layout (D-023) in JavaScript,
// pinned to tests/fixtures/projection_layout_vectors.json: numpy's linspace,
// logspace, and rfftfreq, half-open bar membership, arithmetic bar centers, and
// np.interp over the points sorted by position.

// The fields a layer's bars are computed from (structure.bin_layout's inputs).
export const GRAPH_FIELDS = Object.freeze({
  min: 'min_freq',
  max: 'max_freq',
  floor: 'noise_floor_dbfs',
  points: 'weight_points',
});

export const STRIP_LOW_HZ = 20; // the strip's left end (D-058)
export const RANGE_RATIO = 1.05; // the narrowest range a drag makes
export const WEIGHT_AXIS = Object.freeze([0.1, 100]); // log, plus an "off" row (D-059)
export const FLOOR_AXIS = Object.freeze([-120, -20]); // dBFS, linear (fft-level-controls D-034)
export const FLOOR_TICKS = Object.freeze([-120, -100, -80, -60, -40, -20]);

// ---- Rounding (D-060) --------------------------------------------------------

export const clamp = (value, low, high) => Math.min(high, Math.max(low, value));
export const sig3 = value => (value === 0 ? 0 : Number(value.toPrecision(3)));
export const round3 = value => Math.round(value * 1000) / 1000;

// ---- The layout formula --------------------------------------------------------

export function linspace(start, stop, num) {
  const step = (stop - start) / (num - 1);
  const out = new Array(num);
  for (let index = 0; index < num; index += 1) out[index] = index * step + start;
  out[num - 1] = stop;
  return out;
}

// np.interp: linear between points, holding the end values beyond them.
export function interp(x, xp, fp) {
  if (x <= xp[0]) return fp[0];
  const last = xp.length - 1;
  if (x >= xp[last]) return fp[last];
  let index = 0;
  while (index < last - 1 && x >= xp[index + 1]) index += 1;
  return (fp[index + 1] - fp[index]) / (xp[index + 1] - xp[index]) * (x - xp[index]) + fp[index];
}

export const byPosition = points => [...points].sort((a, b) => a.position - b.position);

// Bar edges, FFT bins per bar, bar centers, their 0–1 positions, and weights.
// Null when the inputs can't make a layout (an invalid range or no points).
export function projectionLayout(minFreq, maxFreq, weightPoints, sampleRate, fftSize, numBars) {
  if (!(minFreq > 0 && maxFreq > minFreq && sampleRate > 0 && fftSize > 0 && numBars > 0)) return null;
  if (!Array.isArray(weightPoints) || weightPoints.length === 0) return null;
  const binHz = 1 / (fftSize * (1 / sampleRate));
  const bins = Math.floor(fftSize / 2) + 1;
  const edges = linspace(Math.log10(minFreq), Math.log10(maxFreq), numBars + 1).map(exponent => 10 ** exponent);
  // One pass: frequencies and edges both increase, so bar i holds the bins in
  // [edges[i], edges[i + 1]) exactly as numpy's masks do.
  const binCounts = new Array(numBars).fill(0);
  let bar = 0;
  for (let bin = 0; bin < bins; bin += 1) {
    const frequency = bin * binHz;
    if (frequency < edges[0]) continue;
    if (frequency >= edges[numBars]) break;
    while (bar < numBars - 1 && frequency >= edges[bar + 1]) bar += 1;
    binCounts[bar] += 1;
  }
  const points = byPosition(weightPoints);
  const xp = points.map(point => point.position);
  const fp = points.map(point => point.weight);
  const span = Math.log10(maxFreq / minFreq);
  const centers = [];
  const positions = [];
  const weights = [];
  for (let index = 0; index < numBars; index += 1) {
    const center = (edges[index] + edges[index + 1]) / 2;
    const position = Math.log10(center / minFreq) / span;
    centers.push(center);
    positions.push(position);
    weights.push(interp(position, xp, fp));
  }
  return {edges, binCounts, centers, positions, weights, binHz};
}

export const emptyBars = layout => (layout ? layout.binCounts.filter(count => count === 0).length : 0);

// ---- What the graph is drawn from (D-049; plan criterion 2) ---------------------

// The bar count the panel would use: its width, led_cols × led_chain. Pixel
// mappers are not predicted (the snapshot corrects it at launch).
export function matrixBars(values) {
  const cols = values?.led_cols;
  const chain = values?.led_chain;
  return Number.isInteger(cols) && Number.isInteger(chain) && cols > 0 && chain > 0 ? cols * chain : null;
}

// `snapshotLayout` is the current run's structure.bin_layout (null when the page
// has none); `catalogLayout` and `assumedUntilLaunch` come from fft_catalog.json.
// A draft source other than the running one makes the sample rate assumed.
export function binContext({snapshotLayout = null, catalogLayout = null, assumedUntilLaunch = [],
  matrixValues = null, sourceChanged = false} = {}) {
  if (snapshotLayout) {
    return {
      sampleRate: sourceChanged ? catalogLayout?.sample_rate ?? snapshotLayout.sample_rate : snapshotLayout.sample_rate,
      fftSize: snapshotLayout.fft_size,
      numBars: snapshotLayout.num_bars,
      assumed: new Set(sourceChanged ? ['sample_rate'] : []),
    };
  }
  // Without a run nothing is measured: the rate and bar count are always assumed
  // (a catalog that stopped listing sample_rate must not turn D-069's warning
  // into an error).
  const assumed = new Set([...assumedUntilLaunch, 'sample_rate', 'num_bars']);
  const fromMatrix = matrixBars(matrixValues);
  return {
    sampleRate: catalogLayout?.sample_rate ?? null,
    fftSize: catalogLayout?.fft_size ?? null,
    numBars: fromMatrix ?? catalogLayout?.num_bars ?? null,
    assumed,
  };
}

export const nyquistOf = context => (Number.isFinite(context?.sampleRate) ? context.sampleRate / 2 : null);

// ---- Scales (pixel layout of the prototype, D-058/D-059) ------------------------

// The strip spans 20 Hz to Nyquist, widened to include a range already outside it.
export function stripScale(width, minFreq, maxFreq, nyquist) {
  const low = Math.min(STRIP_LOW_HZ, minFreq);
  const high = Math.max(nyquist ?? maxFreq, maxFreq);
  const left = 12;
  const right = width - 12;
  const a = Math.log10(low);
  const b = Math.log10(high);
  return {
    low, high, left, right,
    pxPerDecade: (right - left) / (b - a),
    x: frequency => left + (Math.log10(frequency) - a) / (b - a) * (right - left),
    f: x => 10 ** (a + (x - left) / (right - left) * (b - a)),
  };
}

export const DETAIL_HEIGHT = 210;

export function detailScale(width) {
  const left = 38;
  const right = width - 14;
  const top = 12;
  const logBottom = 160;
  const offTop = 166;
  const offBottom = 184;
  const a = Math.log10(WEIGHT_AXIS[0]);
  const b = Math.log10(WEIGHT_AXIS[1]);
  return {
    left, right, top, logBottom, offTop, offBottom,
    x: position => left + position * (right - left),
    p: x => (x - left) / (right - left),
    // Weights between 0 and 0.1 sit on the axis floor; 0 sits in the off row.
    y: weight => (weight <= 0 ? (offTop + offBottom) / 2
      : weight < WEIGHT_AXIS[0] ? logBottom
        : logBottom - (Math.log10(weight) - a) / (b - a) * (logBottom - top)),
    w: y => {
      if (y > logBottom + 6) return 0;
      const t = (logBottom - clamp(y, top, logBottom)) / (logBottom - top);
      return 10 ** (a + t * (b - a));
    },
  };
}

// The floor lane: dBFS on a linear axis, quietest at the left.
export function floorScale(width) {
  const left = 24;
  const right = width - 24;
  const [a, b] = FLOOR_AXIS;
  return {
    left, right, y: 18,
    x: value => left + (clamp(value, a, b) - a) / (b - a) * (right - left),
    v: x => a + (clamp(x, left, right) - left) / (right - left) * (b - a),
  };
}

// ---- Range edits: drags and nudges ----------------------------------------------
//
// `range` is {minFreq, maxFreq, top}. `top` is the highest Max Hz a drag may set:
// the Nyquist frequency, or the field's own maximum when that is lower (Infinity
// when neither is known). Range ends round to 3 significant figures; a value the
// rounding would carry past RANGE_RATIO rounds inward instead, and `top` is kept
// exactly (22,050 Hz would otherwise round up to 22,100).

// 3 significant figures, toward `direction` (-1 or +1) when the nearest one lies
// on the wrong side of `value` (beyond floating-point noise).
export function sig3Toward(value, direction) {
  const nearest = sig3(value);
  const slack = Math.abs(value) * 1e-9;
  if (direction < 0 ? nearest <= value + slack : nearest >= value - slack) return nearest;
  // The step is the third digit of `value` itself: 99.96 rounds to 100, and the
  // next one down is 99.9, not 99.
  const unit = 10 ** (Math.floor(Math.log10(Math.abs(value))) - 2);
  return Number((nearest + direction * unit).toPrecision(3));
}

// A new Min Hz: at least 20 Hz and at most Max Hz / RANGE_RATIO (the order wins).
export function setMin(frequency, {maxFreq}) {
  const high = maxFreq / RANGE_RATIO;
  const low = Math.min(STRIP_LOW_HZ, high);
  const value = sig3(clamp(frequency, low, high));
  return {min_freq: value > high ? sig3Toward(high, -1) : value};
}

// A new Max Hz: at least Min Hz × RANGE_RATIO and at most `top`. When Min Hz is
// too close to `top` for the ratio, `top` itself; empty when Min Hz is at or above it.
export function setMax(frequency, {minFreq, top = Infinity}) {
  const low = minFreq * RANGE_RATIO;
  if (low > top) return top > minFreq ? {max_freq: top} : {};
  const value = sig3(clamp(frequency, low, top));
  return {max_freq: Math.min(top, value < low ? sig3Toward(low, 1) : value)};
}

// Slides the range by `decades` on the log axis, keeping it inside 20 Hz–`top`
// (a range already outside may stay where it is but not move further out).
export function slideRange(decades, {minFreq, maxFreq, top = Infinity}) {
  const logMin = Math.log10(minFreq);
  const logMax = Math.log10(maxFreq);
  const lowest = Math.min(0, Math.log10(STRIP_LOW_HZ) - logMin);
  const highest = Math.max(0, Math.log10(top) - logMax);
  const delta = clamp(decades, lowest, highest);
  return {
    min_freq: sig3(10 ** (logMin + delta)),
    max_freq: Math.min(Math.max(maxFreq, top), sig3(10 ** (logMax + delta))),
  };
}

// Arrow keys on a range handle: a semitone, or an octave with Shift.
export function nudgeRange(key, up, big, range) {
  const factor = 2 ** ((up ? 1 : -1) * (big ? 12 : 1) / 12);
  if (key === 'min') return setMin(range.minFreq * factor, range);
  if (key === 'max') return setMax(range.maxFreq * factor, range);
  if (key === 'band') return slideRange(Math.log10(factor), range);
  return {};
}

// ---- Floor edits ------------------------------------------------------------------

// A drag sets whole dB.
export const dragFloor = (x, scale) => Math.round(scale.v(x)) || 0;

// 1 dB, or 5 dB with Shift, kept inside the axis.
export function nudgeFloor(value, up, big) {
  const step = (up ? 1 : -1) * (big ? 5 : 1);
  return clamp(Math.round((value + step) * 10) / 10, FLOOR_AXIS[0], FLOOR_AXIS[1]);
}

// ---- Point edits ------------------------------------------------------------------

// Where point `id` may move: strictly between its neighbors by position.
export function pointBounds(points, id, low = 0, high = 1) {
  const sorted = byPosition(points);
  const index = sorted.findIndex(point => point.id === id);
  return {
    low: index > 0 ? round3(sorted[index - 1].position + 0.001) : low,
    high: index >= 0 && index < sorted.length - 1 ? round3(sorted[index + 1].position - 0.001) : high,
  };
}

// Holds a position inside `bounds`. When the neighbors leave no room (typed less
// than 0.002 apart) the point keeps `current` rather than landing on one of them.
const holdPosition = (position, {low, high}, current) =>
  (low > high ? current : clamp(round3(position), low, high));

// A dragged weight: 0 in the off row, otherwise 0.1–100 at 3 significant figures.
export const dragWeight = weight => (weight === 0 ? 0 : sig3(clamp(weight, WEIGHT_AXIS[0], WEIGHT_AXIS[1])));

// `current` is the point's position before the drag step.
export function dragPoint(position, weight, bounds, current = position) {
  return {position: holdPosition(position, bounds, current), weight: dragWeight(weight)};
}

// ←/→ move by 0.01 (0.1 with Shift); ↑/↓ scale the weight by 1.1 (2 with
// Shift), with 0 below 0.1 and 0.1 up from 0. Null for other keys.
export function nudgePoint(point, key, big, bounds) {
  if (key === 'ArrowLeft' || key === 'ArrowRight') {
    const step = (big ? 0.1 : 0.01) * (key === 'ArrowRight' ? 1 : -1);
    return {position: holdPosition(point.position + step, bounds, point.position), weight: point.weight};
  }
  if (key === 'ArrowUp' || key === 'ArrowDown') {
    const factor = big ? 2 : 1.1;
    let weight;
    if (key === 'ArrowUp') weight = point.weight <= 0 ? WEIGHT_AXIS[0] : sig3(Math.min(WEIGHT_AXIS[1], point.weight * factor));
    else weight = point.weight / factor < WEIGHT_AXIS[0] ? 0 : sig3(point.weight / factor);
    return {position: point.position, weight};
  }
  return null;
}

// ---- Axis labels --------------------------------------------------------------------

// 1-2-5 steps from 1 Hz to 50 kHz.
export const NICE_HZ = Object.freeze([0, 1, 2, 3, 4].flatMap(exponent =>
  [1, 2, 5].map(mantissa => mantissa * 10 ** exponent)));

export const hzTick = frequency => (frequency >= 1000 ? `${frequency / 1000}k` : `${frequency}`);
