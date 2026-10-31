// apca.mjs - APCA-W3 0.0.98G-4g contrast (pure math, no browser, no dependencies).
//
// Constants and order of operations follow the reference implementation apca-w3 0.1.9 (the version the W3 draft
// still points to). IMPORTANT corrections versus the 4.0 prototype:
//   * alpha is composited here (text over its real background, translucent backgrounds over their ancestors).
//     Ignoring alpha on rgba(0,0,0,.5) over white reports Lc 106 instead of Lc 66.9.
//   * a fully transparent background (rgba(0,0,0,0), the computed value of most elements) must NEVER be read as black.
//   * the result is signed: positive = dark text on light, negative = light text on dark. Thresholds use |Lc|.
// APCA is advisory: WCAG 3 has not selected a contrast algorithm (Editor's Draft, April 2026).
// Licence note: the constants must stay unmodified to use the name "APCA" (W3 License for Compliant Code Only).

export const APCA = Object.freeze({
  mainTRC: 2.4, sRco: 0.2126729, sGco: 0.7151522, sBco: 0.0721750,
  normBG: 0.56, normTXT: 0.57, revTXT: 0.62, revBG: 0.65,
  blkThrs: 0.022, blkClmp: 1.414, scaleBoW: 1.14, scaleWoB: 1.14,
  loBoWoffset: 0.027, loWoBoffset: 0.027, deltaYmin: 0.0005, loClip: 0.1,
});

const clamp255 = (v) => Math.min(255, Math.max(0, v));

/** sRGB (0-255 per channel) -> estimated screen luminance Y (simple 2.4 gamma, as APCA specifies). */
export function sRGBtoY([r, g, b]) {
  const lin = (c) => Math.pow(clamp255(c) / 255, APCA.mainTRC);
  return APCA.sRco * lin(r) + APCA.sGco * lin(g) + APCA.sBco * lin(b);
}

/** Composite rgba (alpha 0..1) over an opaque rgb background. Returns opaque [r,g,b]. */
export function composite(fg, a, bg) {
  const al = Math.min(1, Math.max(0, a));
  return [0, 1, 2].map((i) => fg[i] * al + bg[i] * (1 - al));
}

/** APCA Lc from two luminances (txtY, bgY). Returns signed Lc (about -108..+106). */
export function APCAcontrast(txtY, bgY) {
  if (Number.isNaN(txtY) || Number.isNaN(bgY) || Math.min(txtY, bgY) < 0 || Math.max(txtY, bgY) > 1.1) return 0;
  const soft = (Y) => (Y > APCA.blkThrs ? Y : Y + Math.pow(APCA.blkThrs - Y, APCA.blkClmp));
  const Yt = soft(txtY);
  const Yb = soft(bgY);
  if (Math.abs(Yb - Yt) < APCA.deltaYmin) return 0;
  let out;
  if (Yb > Yt) { // dark text on light background
    const sapc = (Math.pow(Yb, APCA.normBG) - Math.pow(Yt, APCA.normTXT)) * APCA.scaleBoW;
    out = sapc < APCA.loClip ? 0 : sapc - APCA.loBoWoffset;
  } else { // light text on dark background
    const sapc = (Math.pow(Yb, APCA.revBG) - Math.pow(Yt, APCA.revTXT)) * APCA.scaleWoB;
    out = sapc > -APCA.loClip ? 0 : sapc + APCA.loWoBoffset;
  }
  return out * 100;
}

/** Lc of text colour [r,g,b,a] on a stack of background layers (outermost first), over a white canvas. */
export function apcaFromLayers(fgRGBA, bgLayers = [], opacity = 1) {
  let bg = [255, 255, 255];
  for (const layer of bgLayers) bg = composite(layer.slice(0, 3), layer[3] ?? 1, bg);
  const fgA = (fgRGBA[3] ?? 1) * opacity;
  const fg = composite(fgRGBA.slice(0, 3), fgA, bg);
  return { lc: APCAcontrast(sRGBtoY(fg), sRGBtoY(bg)), fg, bg };
}

// Minimum |Lc| by font size and weight, interpolated from the "APCA in a Nutshell" anchors (Barlow-like fonts):
//   400 weight: 14px -> 90, 18px -> 75, 24px -> 60, 36px -> 45, 48px -> 30.   Heavier weights count as larger text.
// This is a documented HEURISTIC; a ticket's explicit `target_apca_lc` always wins.
const ANCHORS_400 = [[14, 90], [18, 75], [24, 60], [36, 45], [48, 30]];
const weightFactor = (w) => (w <= 300 ? 0.78 : w < 450 ? 1 : w < 550 ? 1.12 : w < 650 ? 1.25 : w < 750 ? 1.4 : 1.5);

export function recommendedMinLc(fontSizePx, fontWeight = 400) {
  const eff = fontSizePx * weightFactor(fontWeight);
  if (eff <= ANCHORS_400[0][0]) return ANCHORS_400[0][1];
  for (let i = 1; i < ANCHORS_400.length; i++) {
    const [x1, y1] = ANCHORS_400[i];
    const [x0, y0] = ANCHORS_400[i - 1];
    if (eff <= x1) return Math.round(y0 + ((eff - x0) / (x1 - x0)) * (y1 - y0));
  }
  return ANCHORS_400[ANCHORS_400.length - 1][1];
}
