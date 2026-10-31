// kinematics.mjs - spring physics + interruption analysis (pure math, no browser).
//
// A damped harmonic oscillator  x'' + 2*zeta*omega*x' + omega^2 * (x - target) = 0  has a CLOSED-FORM solution for
// every damping regime, so we never integrate numerically (Euler is unstable for stiff springs - the failure the
// Argon critique warned about) and we can fit measured trajectories directly.
// Time unit everywhere: milliseconds. omega is in rad/ms (0.03 rad/ms ~ 210 ms period).

/** Position x(t) of a spring released at x0 with velocity v0 towards `target`. */
export function springPosition(zeta, omega, t, x0 = 0, v0 = 0, target = 1) {
  const A = x0 - target;
  if (zeta < 1 - 1e-9) {
    const wd = omega * Math.sqrt(1 - zeta * zeta);
    const e = Math.exp(-zeta * omega * t);
    return target + e * (A * Math.cos(wd * t) + ((v0 + zeta * omega * A) / wd) * Math.sin(wd * t));
  }
  if (zeta <= 1 + 1e-9) {
    const e = Math.exp(-omega * t);
    return target + e * (A + (v0 + omega * A) * t);
  }
  const s = Math.sqrt(zeta * zeta - 1);
  const r1 = -omega * (zeta - s);
  const r2 = -omega * (zeta + s);
  const c2 = (v0 - r1 * A) / (r2 - r1);
  const c1 = A - c2;
  return target + c1 * Math.exp(r1 * t) + c2 * Math.exp(r2 * t);
}

/** Damping ratio from the fractional overshoot of a step response (0 < overshoot < 1). */
export function zetaFromOvershoot(overshoot) {
  if (!(overshoot > 0)) return null;
  const l = Math.log(overshoot);
  return -l / Math.sqrt(Math.PI * Math.PI + l * l);
}

/** Damping ratio from two successive same-side peak amplitudes (logarithmic decrement). */
export function zetaFromDecrement(a1, a2) {
  if (!(a1 > 0) || !(a2 > 0) || a2 >= a1) return null;
  const d = Math.log(a1 / a2);
  return d / Math.sqrt(4 * Math.PI * Math.PI + d * d);
}

/**
 * Least-squares fit of (zeta, omega[, start delay]) to samples [{t, x}] (t in ms). The first sample is the
 * initial position; `v0` defaults to a forward difference (pass 0 for a spring released from rest); `target` defaults
 * to the mean of the last 3 samples. `maxDelay` (ms) lets the model start up to that long after the first sample,
 * because the real animation starts somewhere between two animation frames. Coarse grid, then local refinement.
 * Returns { zeta, omega, delay, rmse (normalised by the motion range), target, v0 } or null (too few samples).
 */
export function fitSpring(samples, { v0, target, maxDelay = 0 } = {}) {
  if (!samples || samples.length < 5) return null;
  const t0 = samples[0].t;
  const pts = samples.map((s) => ({ t: s.t - t0, x: s.x }));
  const x0 = pts[0].x;
  const tail = pts.slice(-3);
  const tgt = target ?? tail.reduce((a, p) => a + p.x, 0) / tail.length;
  const vInit = v0 ?? (pts[1].x - pts[0].x) / Math.max(1e-6, pts[1].t - pts[0].t);
  const range = Math.max(1e-9, Math.max(...pts.map((p) => Math.abs(p.x - tgt))));
  const sse = (z, w, d) => {
    let s = 0;
    for (const p of pts) {
      const tt = p.t - d;
      const model = tt <= 0 ? x0 : springPosition(z, w, tt, x0, vInit, tgt);
      const e = model - p.x;
      s += e * e;
    }
    return s;
  };
  const delays = maxDelay > 0 ? [0, 0.25, 0.5, 0.75, 1].map((f) => f * maxDelay) : [0];
  let best = { z: 0.7, w: 0.02, d: 0, s: Infinity };
  for (const d of delays) {
    for (let z = 0.05; z <= 3.0001; z += 0.05) {
      for (let k = 0; k <= 60; k++) {
        const w = 0.002 * Math.pow(100, k / 60); // 0.002 .. 0.2 rad/ms
        const s = sse(z, w, d);
        if (s < best.s) best = { z, w, d, s };
      }
    }
  }
  for (let round = 0; round < 3; round++) {
    const dz = 0.05 / Math.pow(4, round);
    const dw = (best.w * 0.15) / Math.pow(3, round);
    const dd = maxDelay > 0 ? (maxDelay * 0.25) / Math.pow(3, round) : 0;
    for (let i = -6; i <= 6; i++) {
      for (let j = -6; j <= 6; j++) {
        for (let k = maxDelay > 0 ? -2 : 0; k <= (maxDelay > 0 ? 2 : 0); k++) {
          const z = Math.max(0.02, best.z + (i * dz) / 3);
          const w = Math.max(0.0005, best.w + (j * dw) / 3);
          const d = Math.min(maxDelay, Math.max(0, best.d + (k * dd) / 2));
          const s = sse(z, w, d);
          if (s < best.s) best = { z, w, d, s };
        }
      }
    }
  }
  return { zeta: best.z, omega: best.w, delay: best.d, rmse: Math.sqrt(best.s / pts.length) / range, target: tgt, v0: vInit };
}

/**
 * Cut the part of a single-channel series [{t, x}] that contains the motion: from the last still sample before the
 * first change to the first sample that stays within `settleFrac` of the final value. Returns null if nothing moved.
 */
export function motionSegment(series, { startFrac = 0.01, settleFrac = 0.01 } = {}) {
  if (!series || series.length < 5) return null;
  const first = series[0].x;
  const last = series[series.length - 1].x;
  const range = Math.max(...series.map((p) => Math.abs(p.x - last)), Math.abs(last - first));
  if (!(range > 0)) return null;
  const iMove = series.findIndex((p) => Math.abs(p.x - first) > startFrac * range);
  if (iMove < 0) return null;
  let iEnd = series.length - 1;
  while (iEnd > iMove && Math.abs(series[iEnd - 1].x - last) <= settleFrac * range) iEnd--;
  return series.slice(Math.max(0, iMove - 1), Math.min(series.length, iEnd + 2));
}

/** Frame cadence of rAF timestamps: median dt, fps, share of dropped frames (dt > 1.5 x median). */
export function frameStats(ts) {
  if (!ts || ts.length < 3) return { frames: ts ? ts.length : 0, medianDt: null, fps: null, dropRate: null };
  const dts = [];
  for (let i = 1; i < ts.length; i++) dts.push(ts[i] - ts[i - 1]);
  const sorted = [...dts].sort((a, b) => a - b);
  const med = sorted[Math.floor(sorted.length / 2)];
  const dropped = dts.filter((d) => d > 1.5 * med).length;
  return { frames: ts.length, medianDt: med, fps: med > 0 ? 1000 / med : null, dropRate: dropped / dts.length };
}

const CHANNELS = [
  ['tx', 0.5], ['ty', 0.5], ['sx', 1e-3], ['sy', 1e-3], ['rot', 1e-3], ['opacity', 1e-3],
];

function velocities(trace, key) {
  const out = [];
  for (let i = 1; i < trace.length; i++) {
    const dt = trace[i].t - trace[i - 1].t;
    if (dt <= 0) continue;
    out.push({ t0: trace[i - 1].t, t1: trace[i].t, t: (trace[i].t + trace[i - 1].t) / 2, v: (trace[i][key] - trace[i - 1][key]) / dt });
  }
  return out;
}

/** q-quantile (0..1) of a numeric array, nearest-rank. */
export function percentile(values, q) {
  if (!values.length) return 0;
  const sorted = [...values].sort((a, b) => a - b);
  return sorted[Math.min(sorted.length - 1, Math.max(0, Math.ceil(q * sorted.length) - 1))];
}

/**
 * Least-squares polynomial of degree `deg` through [{t, x}], expressed around `at` (s = t - at).
 * Returns { x, v } = value and slope at `at`, or null when the system is singular. Gaussian elimination, 4x4 at most.
 */
export function polyAt(points, at, deg = 3) {
  const m = deg + 1;
  if (points.length < m) return null;
  const S = new Array(2 * m - 1).fill(0);
  const T = new Array(m).fill(0);
  for (const p of points) {
    const s = p.t - at;
    let pw = 1;
    for (let k = 0; k < 2 * m - 1; k++) { S[k] += pw; if (k < m) T[k] += pw * p.x; pw *= s; }
  }
  const M = Array.from({ length: m }, (_, i) => [...Array.from({ length: m }, (_, j) => S[i + j]), T[i]]);
  for (let i = 0; i < m; i++) {
    let q = i;
    for (let r = i + 1; r < m; r++) if (Math.abs(M[r][i]) > Math.abs(M[q][i])) q = r;
    if (Math.abs(M[q][i]) < 1e-12) return null;
    [M[i], M[q]] = [M[q], M[i]];
    for (let r = 0; r < m; r++) {
      if (r === i) continue;
      const f = M[r][i] / M[i][i];
      for (let c = i; c <= m; c++) M[r][c] -= f * M[i][c];
    }
  }
  return { x: M[0][m] / M[0][0], v: M[1][m] / M[1][1] };
}

/**
 * C1-continuity of motion across an interruption at time `tInt` (same time base as the trace).
 * A spring that is retargeted keeps its position AND velocity but its acceleration changes at once, so a frame-to-frame
 * velocity comparison (or a quadratic extrapolation) reports false jumps. Instead one cubic is fitted to the 4-5 frames
 * before the interrupt and one to the 4-5 frames after it (the frame interval that straddles the interrupt is the
 * only information dropped); both are evaluated AT the interrupt and compared:
 *   jumpRatio    = |vPost - vPre| / max(|vPre|, |vPost|)   ~0 for a velocity hand-off, ~1 when velocity is killed/reset
 *   positionStep = |xPost - xPre| / range                   a teleport / restart from the rest position
 * Only a channel that was really moving (local speed >= minMotion x its peak speed) can fail; if none was, the
 * verdict is `inconclusive` (the interrupt did not land mid-motion), never `pass`.
 * Returns { channels, worst, motionAtInterrupt, verdict, failing } with verdict pass | fail | inconclusive.
 * Verified on real Chromium traces: hand-off fixtures give jumpRatio <= 0.06 (0.35 in 1 of ~12 runs, when the page
 * handled the event a few ms after its timestamp - hence the 0.4 default), velocity-reset fixtures >= 0.9.
 */
export function analyzeInterrupt(trace, tInt, { tolerance = 0.4, positionTolerance = 0.05, minMotion = 0.15 } = {}) {
  const channels = {};
  let worst = 0;
  let motion = 0;
  for (const [key, minRange] of CHANNELS) {
    const series = trace.filter((p) => Number.isFinite(p[key])).map((p) => ({ t: p.t, x: p[key] }));
    if (series.length < 10) continue;
    const range = Math.max(...series.map((p) => p.x)) - Math.min(...series.map((p) => p.x));
    if (range < minRange) continue;
    const vel = velocities(trace, key);
    const peak = Math.max(1e-9, percentile(vel.map((p) => Math.abs(p.v)), 0.9)); // robust to one-frame teleports
    const pre = series.filter((p) => p.t <= tInt).slice(-5);
    const post = series.filter((p) => p.t >= tInt).slice(0, 5);
    if (pre.length < 4 || post.length < 4) continue;
    const b = polyAt(pre, tInt, 3);
    const a = polyAt(post, tInt, 3);
    if (!a || !b) continue;
    const local = Math.max(Math.abs(a.v), Math.abs(b.v));
    const motionNow = local / peak;
    const jump = local > 0 ? Math.abs(a.v - b.v) / local : 0;
    const posStep = Math.abs(a.x - b.x) / range;
    channels[key] = { range, peakSpeed: peak, vPre: b.v, vPost: a.v, jumpRatio: jump, positionStep: posStep, motionAtInterrupt: motionNow };
    motion = Math.max(motion, motionNow);
    if (motionNow >= minMotion) worst = Math.max(worst, jump);
  }
  const names = Object.keys(channels);
  if (!names.length) return { channels, worst: null, motionAtInterrupt: 0, verdict: 'inconclusive', reason: 'no animated channel with enough frames around the interrupt' };
  if (motion < minMotion) return { channels, worst, motionAtInterrupt: motion, verdict: 'inconclusive', reason: 'interrupt did not land mid-motion' };
  const bad = names.filter((n) => channels[n].motionAtInterrupt >= minMotion &&
    (channels[n].jumpRatio > tolerance || channels[n].positionStep > positionTolerance));
  return { channels, worst, motionAtInterrupt: motion, verdict: bad.length ? 'fail' : 'pass', failing: bad };
}

/** Pick the dominant animated channel (largest normalised range) and return its [{t,x}] after `tInt`. */
export function postInterruptSeries(trace, tInt) {
  let bestKey = null;
  let bestRange = 0;
  for (const [key, minRange] of CHANNELS) {
    const vals = trace.map((p) => p[key]).filter(Number.isFinite);
    if (vals.length < 6) continue;
    const r = Math.max(...vals) - Math.min(...vals);
    const norm = r / (minRange * 1000);
    if (r >= minRange && norm > bestRange) { bestRange = norm; bestKey = key; }
  }
  if (!bestKey) return null;
  const series = trace.filter((p) => p.t >= tInt).map((p) => ({ t: p.t, x: p[bestKey] }));
  return { key: bestKey, samples: series };
}
