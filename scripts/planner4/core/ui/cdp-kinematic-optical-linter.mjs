#!/usr/bin/env node
// cdp-kinematic-optical-linter.mjs - Planner 4.0 UI gate (AG3 "UI Craft").
//
// Drives a real Chromium through the Chrome DevTools Protocol (via Playwright) and measures four things that
// "AI slop" UIs get wrong and static linting cannot see:
//   1. contrast        APCA Lc of every text node against its REAL composited background (alpha-aware).
//   2. optical         empty space above the capitals vs below the baseline inside a box (Capsize / text-box-trim).
//   3. spring          damping ratio of the element's response to a press, fitted from sampled transforms.
//   4. interrupt       velocity/position continuity when the animation is interrupted mid-flight (C1 continuity).
//
// Every check is tri-state: pass | fail | inconclusive | skipped. The verdict is
//   REJECTED_AI_SLOP      any check failed
//   INCONCLUSIVE          nothing failed, but a requested check could not be decided (never silently "pass")
//   HUMAN_CRAFT_VERIFIED  every requested check passed (and at least one was requested)
// Exit codes: 0 verified | 1 rejected | 3 inconclusive | 2 tooling error.
// Honest limits: only transform/translate/scale/opacity of ONE element are sampled; text over images/gradients is
// inconclusive; APCA is advisory (WCAG 3 has not chosen an algorithm); a headless run cannot judge "feel".

import { createRequire } from 'node:module';
import { pathToFileURL } from 'node:url';
import path from 'node:path';
import fs from 'node:fs';
import { apcaFromLayers, recommendedMinLc } from './lib/apca.mjs';
import { fitSpring, motionSegment, analyzeInterrupt, postInterruptSeries, frameStats } from './lib/kinematics.mjs';
import { opticalGaps, opticalAsymmetry } from './lib/optical.mjs';

export const EXIT = Object.freeze({ VERIFIED: 0, REJECTED: 1, TOOLING: 2, INCONCLUSIVE: 3 });
export const VERDICT = Object.freeze({
  VERIFIED: 'HUMAN_CRAFT_VERIFIED', REJECTED: 'REJECTED_AI_SLOP', INCONCLUSIVE: 'INCONCLUSIVE', TOOLING: 'TOOLING_ERROR',
});

export const DEFAULTS = Object.freeze({
  targetApcaLc: null,          // null => recommendedMinLc(fontSize, weight)
  zeta: null,                  // target damping ratio; null => spring check skipped
  zetaTolerance: 0.15,
  notSpringError: 0.02,        // normalised fit RMSE above which the motion is judged not spring-like (=> fail)
  maxFitError: 0.1,            // above this the fit is garbage (dropped frames, partial capture) (=> inconclusive)
  checkInterrupt: false,
  opticalMaxPx: 1.0,           // allowed |space above caps - space below baseline|, px
  checkOptical: true,
  checkContrast: true,
  holdMs: 16,
  interruptDelays: [60, 100, 150], // ms between the two presses; tried in order until one lands mid-motion
  settleMs: 1600,
  minFps: 30,
  timeoutMs: 30000,
  viewport: { width: 1280, height: 800 },
  maxElements: 25,
  executablePath: null,
});

// ---------------------------------------------------------------------------------------------------------------
// Playwright / Chromium resolution (works from any cwd, Windows included)
// ---------------------------------------------------------------------------------------------------------------
export async function loadChromium() {
  const attempts = [];
  const pick = (m) => (m && (m.chromium ?? m.default?.chromium)) || null;
  const req = createRequire(import.meta.url);
  const extra = process.env.PLANNER_PLAYWRIGHT_PATH;
  const searchPaths = [process.cwd(), ...(process.env.NODE_PATH || '').split(path.delimiter).filter(Boolean)];
  if (extra) {
    for (const cand of [extra, ...searchPaths.map(() => null)].filter(Boolean)) {
      try { const c = pick(req(cand)); if (c) return c; } catch (e) { attempts.push(`${cand}: ${e.code || e.message}`); }
    }
    searchPaths.unshift(extra);
  }
  for (const spec of ['playwright', 'playwright-core']) {
    try { const c = pick(await import(spec)); if (c) return c; } catch (e) { attempts.push(`${spec}: ${e.code || e.message}`); }
    try { const c = pick(req(req.resolve(spec, { paths: searchPaths }))); if (c) return c; } catch (e) { attempts.push(`${spec} (resolve): ${e.code || e.message}`); }
  }
  const err = new Error('Playwright is not installed. Run `npm i -D playwright` (or set PLANNER_PLAYWRIGHT_PATH) and `npx playwright install chromium`. Tried: ' + attempts.join('; '));
  err.code = 'NO_PLAYWRIGHT';
  throw err;
}

// ---------------------------------------------------------------------------------------------------------------
// Page-side functions (serialised into the browser; must be self-contained)
// ---------------------------------------------------------------------------------------------------------------
function pageCollectText([sel, maxElements]) {
  const cv = document.createElement('canvas');
  cv.width = cv.height = 1;
  const cx = cv.getContext('2d', { willReadFrequently: true });
  const parseColor = (c) => {
    if (!c) return null;
    const m = c.trim().match(/^rgba?\(([^)]+)\)$/i);
    if (m) {
      const parts = m[1].split(/[\s,/]+/).filter(Boolean);
      if (parts.length >= 3) {
        const num = (v) => (v.endsWith('%') ? (parseFloat(v) / 100) * 255 : parseFloat(v));
        const a = parts.length > 3 ? (parts[3].endsWith('%') ? parseFloat(parts[3]) / 100 : parseFloat(parts[3])) : 1;
        return [num(parts[0]), num(parts[1]), num(parts[2]), a];
      }
    }
    cx.clearRect(0, 0, 1, 1);
    cx.fillStyle = '#010203';
    cx.fillStyle = c;
    if (cx.fillStyle === '#010203') return null;
    cx.fillRect(0, 0, 1, 1);
    const d = cx.getImageData(0, 0, 1, 1).data;
    return [d[0], d[1], d[2], d[3] / 255];
  };
  const visible = (el) => {
    const cs = getComputedStyle(el);
    if (cs.display === 'none' || cs.visibility === 'hidden') return false;
    const r = el.getBoundingClientRect();
    return r.width > 0 && r.height > 0;
  };
  const cssPath = (el) => {
    const parts = [];
    for (let n = el; n && n.nodeType === 1 && parts.length < 4; n = n.parentElement) {
      let s = n.tagName.toLowerCase();
      if (n.id) { parts.unshift(s + '#' + n.id); break; }
      if (n.classList.length) s += '.' + [...n.classList].slice(0, 2).join('.');
      parts.unshift(s);
    }
    return parts.join(' > ');
  };
  const roots = [...document.querySelectorAll(sel)].slice(0, maxElements);
  const seen = new Set();
  const out = [];
  for (const root of roots) {
    const walker = document.createTreeWalker(root, NodeFilter.SHOW_TEXT);
    for (let n = walker.nextNode(); n; n = walker.nextNode()) {
      const text = (n.nodeValue || '').replace(/\s+/g, ' ').trim();
      const el = n.parentElement;
      if (!text || !el || seen.has(el) || !visible(el)) continue;
      seen.add(el);
      const cs = getComputedStyle(el);
      const chain = [];
      for (let a = el; a; a = a.parentElement) chain.push(a);
      // cumulative opacity from the root of the tree down to each element
      const opacities = chain.map((a) => parseFloat(getComputedStyle(a).opacity));
      const cum = [];
      let acc = 1;
      for (let i = chain.length - 1; i >= 0; i--) { acc *= Number.isFinite(opacities[i]) ? opacities[i] : 1; cum[i] = acc; }
      const layers = [];
      let bgImage = false;
      let opaque = false;
      for (let i = 0; i < chain.length; i++) { // element first, then ancestors
        const ccs = getComputedStyle(chain[i]);
        if (ccs.backgroundImage && ccs.backgroundImage !== 'none') bgImage = true;
        const col = parseColor(ccs.backgroundColor);
        if (col && col[3] > 0) {
          layers.push([col[0], col[1], col[2], col[3] * cum[i]]);
          if (col[3] * cum[i] >= 0.999) { opaque = true; break; }
        }
      }
      layers.reverse(); // outermost first
      const fillC = cs.webkitTextFillColor && cs.webkitTextFillColor !== cs.color ? cs.webkitTextFillColor : cs.color;
      const fg = parseColor(fillC);
      const clipText = (cs.webkitBackgroundClip || cs.backgroundClip || '').includes('text');
      out.push({
        path: cssPath(el), text: text.slice(0, 40), fontSize: parseFloat(cs.fontSize), weight: parseInt(cs.fontWeight, 10) || 400,
        fg: fg ? [fg[0], fg[1], fg[2], fg[3] * cum[0]] : null, layers, opaqueBg: opaque, bgImage, clipText,
      });
    }
  }
  return { matched: roots.length, items: out };
}

async function pageCollectBoxes([sel, maxElements]) {
  if (document.fonts && document.fonts.ready) { try { await document.fonts.ready; } catch (e) { /* ignore */ } }
  const cv = document.createElement('canvas');
  const cx = cv.getContext('2d');
  const roots = [...document.querySelectorAll(sel)].slice(0, maxElements);
  const supportsTrim = !!(window.CSS && CSS.supports && CSS.supports('text-box-trim', 'trim-both'));
  return {
    supportsTrim,
    items: roots.map((el) => {
      const cs = getComputedStyle(el);
      const r = el.getBoundingClientRect();
      const text = (el.textContent || '').replace(/\s+/g, ' ').trim();
      const font = `${cs.fontStyle} ${cs.fontWeight} ${cs.fontSize} ${cs.fontFamily}`;
      cx.font = font;
      const mH = cx.measureText('H');
      const mx = cx.measureText(text || 'H');
      const trimVal = (cs.getPropertyValue('text-box-trim') || '').trim();
      const edge = (cs.getPropertyValue('text-box-edge') || '').trim();
      let trimKind = 'none';
      if (trimVal && trimVal !== 'none') trimKind = /cap/.test(edge) && /alphabetic/.test(edge) ? 'cap-alphabetic' : 'text';
      let capsize = false;
      for (const pseudo of ['::before', '::after']) {
        const p = getComputedStyle(el, pseudo);
        if (p.content !== 'none' && (parseFloat(p.marginTop) < 0 || parseFloat(p.marginBottom) < 0)) capsize = true;
      }
      const lh = parseFloat(cs.lineHeight);
      const padTop = parseFloat(cs.paddingTop) || 0;
      const padBottom = parseFloat(cs.paddingBottom) || 0;
      const borderTop = parseFloat(cs.borderTopWidth) || 0;
      const borderBottom = parseFloat(cs.borderBottomWidth) || 0;
      return {
        tag: el.tagName.toLowerCase(), text: text.slice(0, 40), hasText: !!text, visible: r.width > 0 && r.height > 0 && cs.visibility !== 'hidden' && cs.display !== 'none',
        fontSize: parseFloat(cs.fontSize), font, display: cs.display, lineHeightPx: Number.isFinite(lh) ? lh : null,
        padTop: padTop + borderTop, padBottom: padBottom + borderBottom, height: r.height,
        contentHeight: r.height - padTop - padBottom - borderTop - borderBottom,
        fbAscent: mH.fontBoundingBoxAscent ?? null, fbDescent: mH.fontBoundingBoxDescent ?? null, capHeightPx: mH.actualBoundingBoxAscent ?? null,
        lines: Number.isFinite(lh) && lh > 0 ? Math.round((r.height - padTop - padBottom - borderTop - borderBottom) / lh) : null,
        textWidth: mx.width, trimKind, capsize,
      };
    }),
  };
}

function pageInstallSampler(sel) {
  const el = document.querySelector(sel);
  if (!el) return { ok: false };
  const st = { trace: [], ptr: [], run: true };
  window.__plannerSampler = st;
  const num = (v) => { const f = parseFloat(v); return Number.isFinite(f) ? f : 0; };
  const tick = (ts) => {
    if (!st.run) return;
    const cs = getComputedStyle(el);
    const m = cs.transform && cs.transform !== 'none' ? new DOMMatrix(cs.transform) : new DOMMatrix();
    let tx = m.m41; let ty = m.m42; let sx = Math.hypot(m.m11, m.m12); let sy = Math.hypot(m.m21, m.m22);
    if (cs.translate && cs.translate !== 'none') { const p = cs.translate.split(/\s+/); tx += num(p[0]); ty += num(p[1] || '0'); }
    if (cs.scale && cs.scale !== 'none') { const p = cs.scale.split(/\s+/).map(Number); sx *= p[0]; sy *= p[1] ?? p[0]; }
    st.trace.push({ t: ts, tx, ty, sx, sy, rot: Math.atan2(m.m12, m.m11), opacity: num(cs.opacity) });
    requestAnimationFrame(tick);
  };
  requestAnimationFrame(tick);
  const rec = (type) => (e) => st.ptr.push({ type, t: e.timeStamp });
  for (const t of ['pointerdown', 'pointerup']) window.addEventListener(t, rec(t), true);
  return { ok: true };
}

const pageStopSampler = () => {
  const st = window.__plannerSampler;
  if (!st) return null;
  st.run = false;
  return { trace: st.trace, ptr: st.ptr, now: performance.now() };
};

// ---------------------------------------------------------------------------------------------------------------
// Pure check logic (unit-tested without a browser)
// ---------------------------------------------------------------------------------------------------------------
const result = (status, details = {}) => ({ status, ...details });

export function evaluateContrast(items, { targetApcaLc = null } = {}) {
  if (!items.length) return result('inconclusive', { reason: 'no visible text under the selector', elements: [] });
  const elements = [];
  for (const it of items) {
    const need = targetApcaLc ?? recommendedMinLc(it.fontSize, it.weight);
    if (!it.fg) { elements.push({ path: it.path, text: it.text, status: 'inconclusive', reason: 'unparseable text colour', need }); continue; }
    if (it.bgImage || it.clipText) {
      elements.push({ path: it.path, text: it.text, status: 'inconclusive', reason: it.clipText ? 'background-clip:text' : 'text over background-image/gradient', need });
      continue;
    }
    const { lc } = apcaFromLayers(it.fg, it.layers, 1);
    const abs = Math.abs(lc);
    elements.push({ path: it.path, text: it.text, lc: Math.round(lc * 10) / 10, need, fontSize: it.fontSize, weight: it.weight,
      status: abs + 1e-9 >= need ? 'pass' : 'fail', assumedCanvas: !it.opaqueBg });
  }
  const anyFail = elements.some((e) => e.status === 'fail');
  const anyInc = elements.some((e) => e.status === 'inconclusive');
  return result(anyFail ? 'fail' : anyInc ? 'inconclusive' : 'pass', { elements });
}

export function evaluateOptical(boxes, { opticalMaxPx = 1.0 } = {}) {
  const elements = [];
  for (const b of boxes) {
    const base = { tag: b.tag, text: b.text };
    if (!b.hasText || !b.visible) { elements.push({ ...base, status: 'skipped', reason: 'no visible text' }); continue; }
    if (b.fbAscent == null || b.capHeightPx == null) { elements.push({ ...base, status: 'inconclusive', reason: 'font metrics unavailable' }); continue; }
    if (b.lines != null && b.lines > 1) { elements.push({ ...base, status: 'skipped', reason: 'multi-line block (optical centring applies to single-line boxes)' }); continue; }
    let gaps = opticalGaps({ fbAscent: b.fbAscent, fbDescent: b.fbDescent, capHeightPx: b.capHeightPx, lineHeightPx: b.lineHeightPx });
    let trimmed = false;
    if (b.trimKind === 'cap-alphabetic' || b.capsize) trimmed = true;
    else if (b.trimKind === 'text') gaps = { top: b.fbAscent - b.capHeightPx, bottom: b.fbDescent, halfLeading: 0 };
    const a = opticalAsymmetry({ gaps, padTop: b.padTop, padBottom: b.padBottom, trimmed });
    elements.push({ ...base, status: a.asymmetryPx <= opticalMaxPx ? 'pass' : 'fail', asymmetryPx: Math.round(a.asymmetryPx * 100) / 100,
      topPx: Math.round(a.top * 100) / 100, bottomPx: Math.round(a.bottom * 100) / 100, trimmed, trimKind: b.capsize ? 'capsize' : b.trimKind });
  }
  const real = elements.filter((e) => e.status !== 'skipped');
  if (!real.length) return result('inconclusive', { reason: 'no single-line text box to measure', elements });
  const anyFail = real.some((e) => e.status === 'fail');
  const anyInc = real.some((e) => e.status === 'inconclusive');
  return result(anyFail ? 'fail' : anyInc ? 'inconclusive' : 'pass', { elements, maxPx: opticalMaxPx });
}

const CHANNEL_RANGE = { tx: 0.5, ty: 0.5, sx: 1e-3, sy: 1e-3, rot: 1e-3, opacity: 1e-3 };

/** Pick the channel that moved the most (relative to its noise floor) in a trace. */
export function dominantChannel(trace) {
  let best = null;
  let score = 0;
  for (const [key, floor] of Object.entries(CHANNEL_RANGE)) {
    const v = trace.map((p) => p[key]).filter(Number.isFinite);
    if (v.length < 6) continue;
    const r = Math.max(...v) - Math.min(...v);
    if (r >= floor && r / floor > score) { score = r / floor; best = key; }
  }
  return best;
}

export function evaluateSpring(trace, { zeta, zetaTolerance = 0.15, notSpringError = 0.02, maxFitError = 0.1, minFps = 30 } = {}) {
  if (zeta == null) return result('skipped', { reason: 'no target damping ratio requested' });
  const fps = frameStats(trace.map((p) => p.t));
  if (fps.fps != null && fps.fps < minFps) return result('inconclusive', { reason: `rendering too slow to measure (${fps.fps.toFixed(0)} fps < ${minFps})`, fps });
  const key = dominantChannel(trace);
  if (!key) return result('inconclusive', { reason: 'no motion detected on the element (only transform/translate/scale/opacity are sampled)', fps });
  const series = trace.map((p) => ({ t: p.t, x: p[key] }));
  const last = series[series.length - 1].x;
  const range = Math.max(...series.map((p) => p.x)) - Math.min(...series.map((p) => p.x));
  const tail = series.slice(-6); // still moving by more than 2% of the total travel over the last 5 frames => not settled
  if (Math.abs(tail[tail.length - 1].x - tail[0].x) > 0.02 * range) return result('inconclusive', { reason: 'the motion had not settled when sampling stopped (raise settleMs)', channel: key, fps });
  const seg = motionSegment(series);
  if (!seg || seg.length < 8) return result('inconclusive', { reason: 'motion too short to fit (fewer than 8 frames)', channel: key, fps });
  const fit = fitSpring(seg, { v0: 0, target: last, maxDelay: fps.medianDt || 16.7 });
  if (!fit) return result('inconclusive', { reason: 'fit failed', channel: key, fps });
  const info = { channel: key, fittedZeta: Math.round(fit.zeta * 1000) / 1000, targetZeta: zeta, omegaRadPerMs: fit.omega, fitError: Math.round(fit.rmse * 1000) / 1000, frames: seg.length, fps };
  if (fit.rmse > maxFitError) return result('inconclusive', { ...info, reason: `motion cannot be fitted (error ${fit.rmse.toFixed(2)} > ${maxFitError}); dropped frames or several overlapping animations?` });
  if (fit.rmse > notSpringError) return result('fail', { ...info, reason: `motion is not spring-like (fit error ${fit.rmse.toFixed(3)} > ${notSpringError}) - likely a CSS easing curve instead of a spring` });
  return result(Math.abs(fit.zeta - zeta) <= zetaTolerance ? 'pass' : 'fail', { ...info, tolerance: zetaTolerance });
}

export function evaluateInterrupt(trace, ptr, { minFps = 30 } = {}) {
  const downs = ptr.filter((e) => e.type === 'pointerdown');
  if (downs.length < 2) return result('inconclusive', { reason: 'the interrupting press was not delivered' });
  const fps = frameStats(trace.map((p) => p.t));
  if (fps.fps != null && fps.fps < minFps) return result('inconclusive', { reason: `rendering too slow to measure (${fps.fps.toFixed(0)} fps)`, fps });
  const tInt = downs[1].t;
  const r = analyzeInterrupt(trace, tInt);
  const info = { tInterruptMs: Math.round(tInt), worstJump: r.worst == null ? null : Math.round(r.worst * 1000) / 1000, motionAtInterrupt: Math.round(r.motionAtInterrupt * 1000) / 1000, channels: r.channels, fps };
  if (r.verdict === 'inconclusive') return result('inconclusive', { ...info, reason: r.reason });
  return result(r.verdict, { ...info, failing: r.failing || [] });
}

export function combineVerdict(checks) {
  const real = Object.values(checks).filter((c) => c.status !== 'skipped');
  if (!real.length) return VERDICT.INCONCLUSIVE;
  if (real.some((c) => c.status === 'fail')) return VERDICT.REJECTED;
  if (real.some((c) => c.status === 'inconclusive')) return VERDICT.INCONCLUSIVE;
  return VERDICT.VERIFIED;
}

export const exitCodeFor = (verdict) => ({
  [VERDICT.VERIFIED]: EXIT.VERIFIED, [VERDICT.REJECTED]: EXIT.REJECTED, [VERDICT.INCONCLUSIVE]: EXIT.INCONCLUSIVE,
}[verdict] ?? EXIT.TOOLING);

// ---------------------------------------------------------------------------------------------------------------
// The audit
// ---------------------------------------------------------------------------------------------------------------
async function press(page, selector, { holdMs }) {
  const box = await page.locator(selector).first().boundingBox();
  if (!box) throw new Error(`selector ${selector} has no bounding box`);
  await page.mouse.move(box.x + box.width / 2, box.y + box.height / 2);
  await page.mouse.down();
  if (holdMs > 0) await page.waitForTimeout(holdMs);
  await page.mouse.up();
}

async function openPage(browser, url, o) {
  const context = await browser.newContext({ viewport: o.viewport, reducedMotion: 'no-preference', deviceScaleFactor: 1 });
  const page = await context.newPage();
  page.setDefaultTimeout(o.timeoutMs);
  await page.goto(url, { waitUntil: 'load', timeout: o.timeoutMs });
  await page.evaluate(() => (document.fonts && document.fonts.ready ? document.fonts.ready.then(() => true) : true));
  return { context, page };
}

async function recordMotion(browser, url, selector, o, script) {
  const { context, page } = await openPage(browser, url, o);
  try {
    const inst = await page.evaluate(pageInstallSampler, selector);
    if (!inst || !inst.ok) return { missing: true };
    await page.waitForTimeout(120); // a few still frames before the first press
    await script(page);
    return await page.evaluate(pageStopSampler);
  } finally {
    await context.close().catch(() => {});
  }
}

export async function runKinematicAndOpticalAudit(url, selector, options = {}) {
  const o = { ...DEFAULTS, ...options, viewport: { ...DEFAULTS.viewport, ...(options.viewport || {}) } };
  const chromium = options.chromium || (await loadChromium());
  const started = Date.now();
  let browser;
  let deadline;
  const watchdog = new Promise((_, rej) => { deadline = setTimeout(() => rej(new Error(`audit exceeded ${o.timeoutMs * 4} ms`)), o.timeoutMs * 4); });
  const work = (async () => {
    const launch = { headless: true };
    const exe = o.executablePath || process.env.PLANNER_CHROMIUM_PATH;
    if (exe) launch.executablePath = exe;
    browser = await chromium.launch(launch);
    const checks = {};
    const { context, page } = await openPage(browser, url, o);
    let matched = 0;
    try {
      const count = await page.locator(selector).count();
      if (count === 0) throw Object.assign(new Error(`selector ${selector} matches nothing on ${url}`), { code: 'NO_MATCH' });
      if (o.checkContrast) {
        const t = await page.evaluate(pageCollectText, [selector, o.maxElements]);
        matched = t.matched;
        checks.contrast = evaluateContrast(t.items, { targetApcaLc: o.targetApcaLc });
      } else checks.contrast = result('skipped', { reason: 'disabled' });
      if (o.checkOptical) {
        const b = await page.evaluate(pageCollectBoxes, [selector, o.maxElements]);
        checks.optical = evaluateOptical(b.items, { opticalMaxPx: o.opticalMaxPx });
        checks.optical.textBoxTrimSupported = b.supportsTrim;
      } else checks.optical = result('skipped', { reason: 'disabled' });
    } finally {
      await context.close().catch(() => {});
    }
    if (o.zeta != null) {
      const rec = await recordMotion(browser, url, selector, o, async (pg) => {
        await press(pg, selector, o);
        await pg.waitForTimeout(o.settleMs);
      });
      checks.spring = rec.missing ? result('inconclusive', { reason: 'selector vanished' }) : evaluateSpring(rec.trace, o);
    } else checks.spring = result('skipped', { reason: 'no target damping ratio requested' });
    if (o.checkInterrupt) {
      let last = null;
      const tried = [];
      for (const delay of o.interruptDelays) {
        const rec = await recordMotion(browser, url, selector, o, async (pg) => {
          await press(pg, selector, o);
          await pg.waitForTimeout(delay);
          await press(pg, selector, o);
          await pg.waitForTimeout(o.settleMs);
        });
        last = rec.missing ? result('inconclusive', { reason: 'selector vanished' }) : evaluateInterrupt(rec.trace, rec.ptr, o);
        tried.push({ delayMs: delay, status: last.status });
        if (last.status !== 'inconclusive') break;
      }
      checks.interrupt = { ...last, attempts: tried };
    } else checks.interrupt = result('skipped', { reason: 'interrupt check not requested' });
    const verdict = combineVerdict(checks);
    return { verdict, exitCode: exitCodeFor(verdict), url, selector, matched, durationMs: Date.now() - started, checks,
      limits: ['APCA is advisory (WCAG 3 has not selected a contrast algorithm)', 'only transform/translate/scale/opacity of the first matched element are sampled'] };
  })();
  try {
    return await Promise.race([work, watchdog]);
  } finally {
    clearTimeout(deadline);
    if (browser) await browser.close().catch(() => {});
  }
}

// ---------------------------------------------------------------------------------------------------------------
// CLI
// ---------------------------------------------------------------------------------------------------------------
const HELP = `Usage: node cdp-kinematic-optical-linter.mjs --url URL --selector CSS [options]
  --target-lc N         minimum |APCA Lc| for every text node (default: heuristic by size/weight)
  --zeta N              expected spring damping ratio of the element's press response (enables the spring check)
  --zeta-tol N          allowed |fitted - expected| (default 0.15)
  --interrupt           also test C1 continuity when a press interrupts a running animation
  --optical-max-px N    allowed optical asymmetry in px (default 1.0)
  --no-optical | --no-contrast
  --chromium PATH       Chromium executable (or env PLANNER_CHROMIUM_PATH)
  --timeout MS          per-navigation timeout (default 30000)
  --json                print only the JSON report
Exit codes: 0 verified | 1 rejected | 2 tooling error | 3 inconclusive`;

export function parseArgs(argv) {
  const o = {};
  const take = (i) => { if (i + 1 >= argv.length) throw new Error(`missing value for ${argv[i]}`); return argv[i + 1]; };
  const num = (i) => { const v = Number(take(i)); if (!Number.isFinite(v)) throw new Error(`${argv[i]} needs a number`); return v; };
  for (let i = 0; i < argv.length; i++) {
    const a = argv[i];
    switch (a) {
      case '--url': o.url = take(i); i++; break;
      case '--selector': o.selector = take(i); i++; break;
      case '--target-lc': o.targetApcaLc = num(i); i++; break;
      case '--zeta': o.zeta = num(i); i++; break;
      case '--zeta-tol': o.zetaTolerance = num(i); i++; break;
      case '--optical-max-px': o.opticalMaxPx = num(i); i++; break;
      case '--timeout': o.timeoutMs = num(i); i++; break;
      case '--chromium': o.executablePath = take(i); i++; break;
      case '--interrupt': o.checkInterrupt = true; break;
      case '--no-optical': o.checkOptical = false; break;
      case '--no-contrast': o.checkContrast = false; break;
      case '--json': o.json = true; break;
      case '-h': case '--help': o.help = true; break;
      default: throw new Error(`unknown argument ${a}`);
    }
  }
  return o;
}

export function normaliseUrl(u) {
  if (/^[a-z][a-z0-9+.-]*:/i.test(u) && !/^[a-z]:[\\/]/i.test(u)) return u; // already a URL (not a Windows drive path)
  return pathToFileURL(path.resolve(u)).href;
}

async function main() {
  let args;
  try { args = parseArgs(process.argv.slice(2)); } catch (e) { console.error(e.message + '\n' + HELP); return EXIT.TOOLING; }
  if (args.help) { console.log(HELP); return 0; }
  if (!args.url || !args.selector) { console.error('--url and --selector are required\n' + HELP); return EXIT.TOOLING; }
  const { url, selector, json, ...opts } = args;
  try {
    const report = await runKinematicAndOpticalAudit(normaliseUrl(url), selector, opts);
    if (!json) {
      const mark = { pass: 'PASS', fail: 'FAIL', inconclusive: '????', skipped: 'skip' };
      for (const [name, c] of Object.entries(report.checks)) console.error(`[${mark[c.status]}] ${name}${c.reason ? ' - ' + c.reason : ''}`);
      console.error(`verdict: ${report.verdict}`);
    }
    console.log(JSON.stringify(report));
    return report.exitCode;
  } catch (e) {
    console.log(JSON.stringify({ verdict: VERDICT.TOOLING, exitCode: EXIT.TOOLING, error: String(e.message || e), code: e.code || null }));
    console.error('tooling error: ' + (e.message || e));
    return EXIT.TOOLING;
  }
}

const isMain = (() => {
  try { return fs.realpathSync(process.argv[1]) === fs.realpathSync(new URL(import.meta.url)); } catch (e) { return false; }
})();
if (isMain) main().then((c) => { process.exitCode = c; });
