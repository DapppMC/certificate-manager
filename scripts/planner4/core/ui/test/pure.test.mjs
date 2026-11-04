// node --test core/ui/test  - pure-function tests, no browser needed.
import test from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs';
import path from 'node:path';
import { fileURLToPath } from 'node:url';
import { APCAcontrast, sRGBtoY, composite, apcaFromLayers, recommendedMinLc } from '../lib/apca.mjs';
import { springPosition, zetaFromOvershoot, zetaFromDecrement, fitSpring, motionSegment, frameStats, analyzeInterrupt, polyAt } from '../lib/kinematics.mjs';
import { capsizeTrims, opticalGaps, opticalAsymmetry } from '../lib/optical.mjs';
import { evaluateContrast, evaluateOptical, evaluateSpring, evaluateInterrupt, combineVerdict, exitCodeFor, parseArgs, normaliseUrl, VERDICT, dominantChannel } from '../cdp-kinematic-optical-linter.mjs';

const here = path.dirname(fileURLToPath(import.meta.url));
const tracesDir = path.resolve(here, '../../../tests/fixtures/ui/traces');
const lc = (fg, bg) => APCAcontrast(sRGBtoY(fg), sRGBtoY(bg));
const near = (a, b, eps, msg) => assert.ok(Math.abs(a - b) <= eps, `${msg || ''} ${a} vs ${b} (eps ${eps})`);

test('APCA reference values (apca-w3 0.1.9)', () => {
  near(lc([0, 0, 0], [255, 255, 255]), 106.04, 0.05, 'black on white');
  near(lc([255, 255, 255], [0, 0, 0]), -107.88, 0.05, 'white on black');
  near(lc([136, 136, 136], [255, 255, 255]), 63.06, 0.1, '#888 on white');
  assert.equal(lc([200, 200, 200], [200, 200, 200]), 0);
  assert.ok(lc([0, 0, 0], [255, 255, 255]) > 0 && lc([255, 255, 255], [0, 0, 0]) < 0, 'polarity');
});

test('APCA rejects out-of-range luminance instead of throwing', () => {
  assert.equal(APCAcontrast(NaN, 1), 0);
  assert.equal(APCAcontrast(-1, 1), 0);
  assert.equal(APCAcontrast(0.2, 2), 0);
});

test('alpha is composited, transparent background is never read as black', () => {
  const half = apcaFromLayers([0, 0, 0, 0.5], [], 1);
  near(half.lc, 66.9, 0.5, 'rgba(0,0,0,.5) on white');
  assert.ok(half.lc < 100);
  // a fully transparent layer changes nothing
  near(apcaFromLayers([0, 0, 0, 1], [[0, 0, 0, 0]], 1).lc, 106.04, 0.05);
  // translucent layer over dark layer
  const stacked = apcaFromLayers([255, 255, 255, 1], [[0, 0, 0, 1], [255, 255, 255, 0.1]], 1);
  assert.ok(stacked.lc < -90);
  assert.deepEqual(composite([10, 20, 30], 0, [1, 2, 3]), [1, 2, 3]);
  assert.deepEqual(composite([10, 20, 30], 1, [1, 2, 3]), [10, 20, 30]);
});

test('recommendedMinLc shrinks with size and weight and stays inside the anchor range', () => {
  assert.equal(recommendedMinLc(14, 400), 90);
  assert.ok(recommendedMinLc(24, 400) < recommendedMinLc(16, 400));
  assert.ok(recommendedMinLc(16, 700) < recommendedMinLc(16, 400));
  assert.equal(recommendedMinLc(400, 400), 30);
  assert.ok(recommendedMinLc(16, 100) >= 30);
});

test('springPosition is continuous across damping regimes and starts at x0 with velocity v0', () => {
  for (const z of [0.3, 0.999999, 1, 1.0000001, 1.7]) {
    near(springPosition(z, 0.02, 0, 5, 0.3, 100), 5, 1e-9);
    const h = 1e-3;
    const v = (springPosition(z, 0.02, h, 5, 0.3, 100) - springPosition(z, 0.02, -h, 5, 0.3, 100)) / (2 * h);
    // one-sided at t=0 for t>=0 only: use forward difference
    const vf = (springPosition(z, 0.02, h, 5, 0.3, 100) - 5) / h;
    near(vf, 0.3, 0.02, `v0 for zeta ${z}`);
    near(springPosition(z, 0.02, 5000, 5, 0.3, 100), 100, 0.5, 'settles');
    assert.ok(Number.isFinite(v));
  }
  const a = springPosition(0.99999999, 0.02, 100, 0, 0, 1);
  const b = springPosition(1.00000001, 0.02, 100, 0, 0, 1);
  near(a, b, 1e-6, 'critical damping boundary');
});

test('zeta estimators agree with the forward model', () => {
  const z = 0.4;
  let peak = 0;
  for (let t = 0; t < 400; t += 0.5) peak = Math.max(peak, springPosition(z, 0.02, t, 0, 0, 1));
  near(zetaFromOvershoot(peak - 1), z, 0.005);
  const td = (2 * Math.PI) / (0.02 * Math.sqrt(1 - z * z));
  const a1 = springPosition(z, 0.02, td / 2, 0, 0, 1) - 1;
  const a2 = springPosition(z, 0.02, (3 * td) / 2, 0, 0, 1) - 1;
  near(zetaFromDecrement(a1, a2), z, 0.01);
  assert.equal(zetaFromOvershoot(0), null);
  assert.equal(zetaFromDecrement(1, 2), null);
});

function synth(z, w, { frame = 16.7, delay = 5, target = 200, noise = 0, seed = 1 } = {}) {
  let s = seed;
  const rnd = () => { s = (s * 16807) % 2147483647; return (s / 2147483647 - 0.5) * 2; };
  const out = [];
  for (let t = 0; t < 1200; t += frame) out.push({ t, x: springPosition(z, w, Math.max(0, t - delay), 0, 0, target) + noise * rnd() });
  return out;
}

test('fitSpring recovers zeta and omega, with frame delay and with noise', () => {
  for (const z of [0.3, 0.5, 0.8, 1.0, 1.4]) {
    const series = synth(z, 0.02, { noise: 0.4 });
    const seg = motionSegment(series);
    const fit = fitSpring(seg, { v0: 0, target: 200, maxDelay: 16.7 });
    near(fit.zeta, z, 0.08, `zeta ${z}`);
    near(fit.omega, 0.02, 0.003, `omega for zeta ${z}`);
    assert.ok(fit.rmse < 0.05);
  }
});

test('fitSpring refuses tiny inputs; motionSegment returns null for a still element', () => {
  assert.equal(fitSpring([{ t: 0, x: 0 }]), null);
  assert.equal(motionSegment(Array.from({ length: 20 }, (_, i) => ({ t: i * 16, x: 5 }))), null);
  assert.equal(motionSegment([]), null);
});

test('a linear CSS-style ease is not mistaken for a spring (large fit error)', () => {
  const series = [];
  for (let t = 0; t < 800; t += 16.7) series.push({ t, x: 200 * Math.min(1, t / 300) });
  const fit = fitSpring(motionSegment(series), { v0: 0, target: 200, maxDelay: 16.7 });
  assert.ok(fit.rmse > 0.03, `rmse ${fit.rmse}`);
});

test('frameStats flags dropped frames', () => {
  const ts = [];
  let t = 0;
  for (let i = 0; i < 60; i++) { t += i % 10 === 9 ? 50 : 16.7; ts.push(t); }
  const s = frameStats(ts);
  near(s.medianDt, 16.7, 0.01);
  assert.ok(s.dropRate > 0.05 && s.dropRate < 0.2);
  assert.equal(frameStats([1, 2]).fps, null);
});

test('polyAt solves cubics exactly and rejects too few points', () => {
  const f = (t) => 3 + 2 * t - 0.5 * t * t + 0.01 * t * t * t;
  const pts = [-3, -2, -1, 0.5, 2].map((t) => ({ t, x: f(t) }));
  const r = polyAt(pts, 0, 3);
  near(r.x, 3, 1e-9);
  near(r.v, 2, 1e-9);
  assert.equal(polyAt(pts.slice(0, 3), 0, 3), null);
});

const loadTrace = (name) => JSON.parse(fs.readFileSync(path.join(tracesDir, name), 'utf8'));

test('real Chromium traces: velocity hand-off passes, velocity reset fails', () => {
  for (const gap of [60, 110]) {
    const g = loadTrace(`trace_good_${gap}.json`);
    const b = loadTrace(`trace_bad_${gap}.json`);
    const rg = analyzeInterrupt(g.trace, g.ptr[1].t);
    const rb = analyzeInterrupt(b.trace, b.ptr[1].t);
    assert.equal(rg.verdict, 'pass', `good ${gap}: ${JSON.stringify(rg.channels.tx)}`);
    assert.equal(rb.verdict, 'fail', `bad ${gap}: ${JSON.stringify(rb.channels.tx)}`);
    assert.ok(rg.channels.tx.jumpRatio < 0.15);
    assert.ok(rb.channels.tx.jumpRatio > 0.6);
  }
});

test('an interrupt at the turning point of the motion is inconclusive, not a pass', () => {
  const g = loadTrace('trace_good_160.json');
  const r = analyzeInterrupt(g.trace, g.ptr[1].t);
  assert.equal(r.verdict, 'inconclusive');
  assert.match(r.reason, /mid-motion/);
});

test('interrupt analysis on a static trace or too few frames is inconclusive', () => {
  const still = Array.from({ length: 30 }, (_, i) => ({ t: i * 16, tx: 0, ty: 0, sx: 1, sy: 1, rot: 0, opacity: 1 }));
  assert.equal(analyzeInterrupt(still, 200).verdict, 'inconclusive');
  assert.equal(analyzeInterrupt(still.slice(0, 5), 40).verdict, 'inconclusive');
});

test('a teleport (position step) across the interrupt fails even with matching velocity', () => {
  const tr = [];
  for (let i = 0; i < 40; i++) {
    const t = i * 16.7;
    tr.push({ t, tx: i < 20 ? t * 0.5 : 100 + t * 0.5, ty: 0, sx: 1, sy: 1, rot: 0, opacity: 1 });
  }
  const r = analyzeInterrupt(tr, 19.5 * 16.7);
  assert.equal(r.verdict, 'fail');
  assert.ok(r.channels.tx.positionStep > 0.05);
});

test('capsize trims make the trimmed line box exactly the cap height', () => {
  const m = { capHeight: 1456, ascent: 1900, descent: -500, lineGap: 0, unitsPerEm: 2048, fontSize: 16 };
  for (const lineHeight of [null, 24, 32]) {
    const t = capsizeTrims({ ...m, lineHeight });
    const lh = lineHeight ?? (16 * (1900 + 500)) / 2048;
    near(lh + t.capHeightTrimPx + t.baselineTrimPx, (16 * 1456) / 2048, 1e-9, `lineHeight ${lineHeight}`);
  }
  const withGap = capsizeTrims({ ...m, lineGap: 90, lineHeight: 28 });
  near(28 + withGap.capHeightTrimPx + withGap.baselineTrimPx, (16 * 1456) / 2048, 1e-9);
});

test('opticalGaps / opticalAsymmetry', () => {
  const g = opticalGaps({ fbAscent: 18, fbDescent: 6, capHeightPx: 13, lineHeightPx: 36 });
  assert.deepEqual(g, { top: 6 + 5, bottom: 6 + 6, halfLeading: 6 });
  const a = opticalAsymmetry({ gaps: g, padTop: 10, padBottom: 10 });
  near(a.asymmetryPx, 1, 1e-9);
  assert.equal(opticalAsymmetry({ gaps: g, padTop: 10, padBottom: 10, trimmed: true }).asymmetryPx, 0);
  const normal = opticalGaps({ fbAscent: 18, fbDescent: 6, capHeightPx: 13, lineHeightPx: NaN });
  assert.equal(normal.halfLeading, 0);
});

const item = (over = {}) => ({ path: 'p', text: 't', fontSize: 16, weight: 400, fg: [0, 0, 0, 1], layers: [[255, 255, 255, 1]], opaqueBg: true, bgImage: false, clipText: false, ...over });

test('evaluateContrast: pass, fail, gradient and unparseable colours are inconclusive, empty is inconclusive', () => {
  assert.equal(evaluateContrast([item()]).status, 'pass');
  assert.equal(evaluateContrast([item({ fg: [180, 180, 180, 1] })]).status, 'fail');
  assert.equal(evaluateContrast([item({ bgImage: true })]).status, 'inconclusive');
  assert.equal(evaluateContrast([item({ clipText: true })]).status, 'inconclusive');
  assert.equal(evaluateContrast([item({ fg: null })]).status, 'inconclusive');
  assert.equal(evaluateContrast([]).status, 'inconclusive');
  assert.equal(evaluateContrast([item({ bgImage: true }), item({ fg: [200, 200, 200, 1] })]).status, 'fail', 'a failure outranks an inconclusive');
  assert.equal(evaluateContrast([item({ fg: [120, 120, 120, 1] })], { targetApcaLc: 50 }).status, 'pass');
  assert.equal(evaluateContrast([item({ fg: [120, 120, 120, 1] })], { targetApcaLc: 90 }).status, 'fail');
});

const box = (over = {}) => ({ tag: 'button', text: 'x', hasText: true, visible: true, fontSize: 16, lineHeightPx: 24, padTop: 10, padBottom: 10, fbAscent: 14.5, fbDescent: 3.5,
  capHeightPx: 11.6, lines: 1, trimKind: 'none', capsize: false, ...over });

test('evaluateOptical: trimmed boxes pass, untrimmed asymmetric boxes fail, multi-line and hidden are skipped', () => {
  assert.equal(evaluateOptical([box({ trimKind: 'cap-alphabetic' })]).status, 'pass');
  assert.equal(evaluateOptical([box({ capsize: true })]).status, 'pass');
  assert.equal(evaluateOptical([box({ padBottom: 22 })]).status, 'fail');
  assert.equal(evaluateOptical([box({ lines: 3 })]).status, 'inconclusive');
  assert.equal(evaluateOptical([box({ hasText: false })]).status, 'inconclusive');
  assert.equal(evaluateOptical([box({ fbAscent: null })]).status, 'inconclusive');
  assert.equal(evaluateOptical([box({ trimKind: 'cap-alphabetic', padBottom: 20 })]).status, 'fail', 'trim does not hide padding asymmetry');
});

test('evaluateSpring: gating on missing target, motion, speed, fit error', () => {
  assert.equal(evaluateSpring([], {}).status, 'skipped');
  const still = Array.from({ length: 40 }, (_, i) => ({ t: i * 16.7, tx: 0, ty: 0, sx: 1, sy: 1, rot: 0, opacity: 1 }));
  assert.equal(evaluateSpring(still, { zeta: 0.5 }).status, 'inconclusive');
  const tr = synth(0.5, 0.02).map((p) => ({ t: p.t, tx: p.x, ty: 0, sx: 1, sy: 1, rot: 0, opacity: 1 }));
  assert.equal(evaluateSpring(tr, { zeta: 0.5 }).status, 'pass');
  assert.equal(evaluateSpring(tr, { zeta: 1.0 }).status, 'fail');
  const slow = tr.map((p, i) => ({ ...p, t: i * 60 }));
  assert.equal(evaluateSpring(slow, { zeta: 0.5, minFps: 30 }).status, 'inconclusive');
  const ease = [];
  for (let t = 0; t < 800; t += 16.7) ease.push({ t, tx: 200 * Math.min(1, t / 300), ty: 0, sx: 1, sy: 1, rot: 0, opacity: 1 });
  const ez = evaluateSpring(ease, { zeta: 0.5 });
  assert.equal(ez.status, 'fail', 'a linear ease is not a spring');
  assert.match(ez.reason, /not spring-like/);
  const unsettled = synth(0.5, 0.004).map((p) => ({ t: p.t, tx: p.x, ty: 0, sx: 1, sy: 1, rot: 0, opacity: 1 }));
  assert.equal(evaluateSpring(unsettled, { zeta: 0.5 }).status, 'inconclusive', 'cut off before settling');
});

test('evaluateInterrupt needs two presses', () => {
  const g = loadTrace('trace_good_60.json');
  assert.equal(evaluateInterrupt(g.trace, [g.ptr[0]]).status, 'inconclusive');
  assert.equal(evaluateInterrupt(g.trace, g.ptr).status, 'pass');
});

test('dominantChannel picks the channel that moved', () => {
  const tr = Array.from({ length: 30 }, (_, i) => ({ t: i * 16, tx: i * 3, ty: 0, sx: 1, sy: 1, rot: 0, opacity: 1 }));
  assert.equal(dominantChannel(tr), 'tx');
  assert.equal(dominantChannel(tr.map((p) => ({ ...p, tx: 0 }))), null);
});

test('verdict combination is conservative', () => {
  const c = (s) => ({ status: s });
  assert.equal(combineVerdict({ a: c('pass'), b: c('pass'), z: c('skipped') }), VERDICT.VERIFIED);
  assert.equal(combineVerdict({ a: c('pass'), b: c('inconclusive') }), VERDICT.INCONCLUSIVE);
  assert.equal(combineVerdict({ a: c('inconclusive'), b: c('fail') }), VERDICT.REJECTED);
  assert.equal(combineVerdict({ a: c('skipped') }), VERDICT.INCONCLUSIVE, 'nothing checked is never a pass');
  assert.equal(exitCodeFor(VERDICT.VERIFIED), 0);
  assert.equal(exitCodeFor(VERDICT.REJECTED), 1);
  assert.equal(exitCodeFor(VERDICT.INCONCLUSIVE), 3);
  assert.equal(exitCodeFor('nonsense'), 2);
});

test('CLI argument parsing and url normalisation', () => {
  const a = parseArgs(['--url', 'x.html', '--selector', '#a', '--zeta', '0.5', '--interrupt', '--no-optical', '--target-lc', '60']);
  assert.equal(a.zeta, 0.5);
  assert.equal(a.checkInterrupt, true);
  assert.equal(a.checkOptical, false);
  assert.equal(a.targetApcaLc, 60);
  assert.throws(() => parseArgs(['--zeta']), /missing value/);
  assert.throws(() => parseArgs(['--zeta', 'abc']), /number/);
  assert.throws(() => parseArgs(['--bogus']), /unknown/);
  assert.equal(normaliseUrl('http://localhost:3000/a'), 'http://localhost:3000/a');
  assert.match(normaliseUrl('page.html'), /^file:\/\//);
  assert.match(normaliseUrl('C:\\site\\a.html'), /^file:\/\//);
});
