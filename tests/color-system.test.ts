import { readFileSync } from 'node:fs';
import { describe, expect, it } from 'vitest';

const styles = readFileSync(new URL('../app/globals.css', import.meta.url), 'utf8');

function cssRule(selector: string): string {
  let declarations = '';
  for (const match of styles.matchAll(/([^{}]+)\{([^{}]*)\}/g)) {
    if (match[1].split(',').some(candidate => candidate.trim() === selector)) {
      declarations = match[2];
    }
  }
  return declarations;
}

describe('research color system', () => {
  it('defines semantic colors for evidence, citation, derivation and review', () => {
    for (const token of [
      '--color-evidence',
      '--color-citation',
      '--color-derivation',
      '--color-review',
      '--surface-evidence',
      '--surface-citation',
      '--surface-derivation',
      '--surface-review',
    ]) {
      expect(styles).toContain(`${token}:`);
    }
  });

  it('keeps graph legend roles on semantic tokens instead of one-off colors', () => {
    expect(cssRule('.evidence-dot')).toContain('background: var(--color-evidence)');
    expect(cssRule('.concept-dot')).toContain('background: var(--color-citation)');
    expect(cssRule('.hypothesis-dot')).toContain('background: var(--color-derivation)');
    expect(cssRule('.stage-nav-link.is-active')).toContain('background: var(--primary)');
  });

  it('keeps untested gates neutral and the narrow-screen inspector accessible', () => {
    expect(cssRule('.compatibility-gate-chips span')).toContain('background: var(--muted)');
    expect(styles).toMatch(/\.inspector-panel\s*\{\s*display: block;\s*grid-column: 1 \/ -1;/);
    expect(cssRule('.onboarding-toggle')).toContain('min-height: 44px');
  });
});
