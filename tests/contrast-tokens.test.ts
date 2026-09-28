import { readFileSync } from 'node:fs';
import { describe, expect, it } from 'vitest';

const styles = readFileSync(new URL('../app/globals.css', import.meta.url), 'utf8');

function cssRule(selector: string): string {
  let declarations: string | undefined;
  for (const match of styles.matchAll(/([^{}]+)\{([^{}]*)\}/g)) {
    if (match[1].split(',').some(candidate => candidate.trim() === selector)) declarations = match[2];
  }
  if (!declarations) throw new Error(`Missing CSS rule: ${selector}`);
  return declarations;
}

function linearize(value: number): number {
  return value <= 0.04045 ? value / 12.92 : ((value + 0.055) / 1.055) ** 2.4;
}

function luminance(rgb: [number, number, number]): number {
  return rgb.map(linearize).reduce((sum, channel, index) =>
    sum + channel * [0.2126, 0.7152, 0.0722][index], 0);
}

function oklchToRgb(lightness: number, chroma: number, hue: number): [number, number, number] {
  const angle = hue * Math.PI / 180;
  const a = chroma * Math.cos(angle);
  const b = chroma * Math.sin(angle);
  const l = (lightness + 0.3963377774 * a + 0.2158037573 * b) ** 3;
  const m = (lightness - 0.1055613458 * a - 0.0638541728 * b) ** 3;
  const s = (lightness - 0.0894841775 * a - 1.291485548 * b) ** 3;
  return [
    4.0767416621 * l - 3.3077115913 * m + 0.2309699292 * s,
    -1.2684380046 * l + 2.6097574011 * m - 0.3413193965 * s,
    -0.0041960863 * l - 0.7034186147 * m + 1.707614701 * s,
  ].map(channel => {
    const bounded = Math.max(0, Math.min(1, channel));
    return bounded <= 0.0031308
      ? 12.92 * bounded
      : 1.055 * bounded ** (1 / 2.4) - 0.055;
  }) as [number, number, number];
}

function contrastRatio(foreground: [number, number, number], background: [number, number, number]): number {
  const values = [luminance(foreground), luminance(background)].sort((a, b) => b - a);
  return (values[0] + 0.05) / (values[1] + 0.05);
}

describe('small-text contrast tokens', () => {
  it('keeps muted labels at WCAG AA contrast on the application surfaces', () => {
    const root = styles.match(/:root\s*\{([^}]*)\}/)?.[1] ?? '';
    const foreground = root.match(/--muted-foreground:\s*oklch\(([^)]+)\)/)?.[1]
      .trim().split(/\s+/).map(Number);
    if (!foreground || foreground.length !== 3) throw new Error('Missing muted foreground token');
    const muted = oklchToRgb(foreground[0], foreground[1], foreground[2]);
    const white: [number, number, number] = [1, 1, 1];
    const shell = [247, 248, 252].map(value => value / 255) as [number, number, number];

    expect(contrastRatio(muted, white)).toBeGreaterThanOrEqual(4.5);
    expect(contrastRatio(muted, shell)).toBeGreaterThanOrEqual(4.5);
    const badge = [235, 238, 244].map(value => value / 255) as [number, number, number];
    expect(contrastRatio(muted, badge)).toBeGreaterThanOrEqual(4.5);

    for (const selector of [
      '.search-result-topline',
      '.search-result-signals small',
      '.paper-card > p',
      '.list-label',
      '.section-row span:last-child',
      '.provenance-note p',
      '.flow-evidence-node small',
      '.graph-status span:last-child',
      '.hypothesis-dock p',
      '.research-move-trigger small',
      '.section-title span',
      '.relation-card strong',
    ]) {
      expect(cssRule(selector)).toContain('color: var(--muted-foreground)');
    }
    expect(cssRule('.graph-stage:has(.graph-3d-viewport) .graph-status span:last-child'))
      .toContain('color: #94a3b8');
  });
});
