// optical.mjs - Capsize half-leading maths and optical-gap measurement (pure, no browser).
//
// Capsize trims the empty space between the line box and the glyphs so that a text block is optically centred:
//   lhNormal         = fontSize * (ascent + lineGap + |descent|) / unitsPerEm
//   capHeightTrim(em)= -( ascent/upm - capHeight/upm + lineGap/(2*upm) - ((lhNormal - lineHeight)/2)/fontSize )
//   baselineTrim(em) = -( |descent|/upm + lineGap/(2*upm) - ((lhNormal - lineHeight)/2)/fontSize )
// (the lineHeight term disappears when line-height is `normal`). Source: @capsizecss/core precomputeValues (4.1.x).

export function capsizeTrims({ capHeight, ascent, descent, lineGap = 0, unitsPerEm, fontSize, lineHeight = null }) {
  const upm = unitsPerEm;
  const lhNormal = (fontSize * (ascent + lineGap + Math.abs(descent))) / upm;
  const offset = lineHeight == null ? 0 : (lhNormal - lineHeight) / 2 / fontSize;
  const capHeightTrim = -(ascent / upm - capHeight / upm + lineGap / (2 * upm) - offset);
  const baselineTrim = -(Math.abs(descent) / upm + lineGap / (2 * upm) - offset);
  return { capHeightTrimEm: capHeightTrim, baselineTrimEm: baselineTrim,
           capHeightTrimPx: capHeightTrim * fontSize, baselineTrimPx: baselineTrim * fontSize };
}

/**
 * Empty space above the capital letters and below the baseline inside one line box, from canvas text metrics.
 *   fbAscent/fbDescent : font bounding box ascent/descent (canvas measureText fontBoundingBox*)
 *   capHeightPx        : actualBoundingBoxAscent of 'H'
 *   lineHeightPx       : used line height (null/NaN => `normal`, i.e. fbAscent + fbDescent)
 */
export function opticalGaps({ fbAscent, fbDescent, capHeightPx, lineHeightPx }) {
  const content = fbAscent + fbDescent;
  const lh = Number.isFinite(lineHeightPx) && lineHeightPx > 0 ? lineHeightPx : content;
  const halfLeading = (lh - content) / 2;
  return { top: halfLeading + (fbAscent - capHeightPx), bottom: halfLeading + fbDescent, halfLeading };
}

/**
 * Optical asymmetry in px: (padding + gap above caps) - (padding + gap below baseline).
 * If the text is trimmed (Capsize pseudo-elements or CSS text-box-trim) the gaps are zero by construction, so only
 * the padding difference remains.
 */
export function opticalAsymmetry({ gaps, padTop = 0, padBottom = 0, trimmed = false }) {
  const top = padTop + (trimmed ? 0 : gaps.top);
  const bottom = padBottom + (trimmed ? 0 : gaps.bottom);
  return { top, bottom, asymmetryPx: Math.abs(top - bottom) };
}
