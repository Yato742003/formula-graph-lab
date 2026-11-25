import katex from 'katex';
import type {
  EvidenceEntityType,
  EvidenceRelationType,
} from '@/lib/import-types';

export type GraphViewportNode = {
  id: string;
  kind: EvidenceEntityType;
  label: string;
  expression: string;
  meta: string;
};

export type GraphViewportEdge = {
  id: string;
  source: string;
  target: string;
  relation: EvidenceRelationType;
};

export type InCardSymbol = {
  id: string;
  label: string;
  expression: string;
};

export function cleanFormula(expr: string): string {
  if (!expr) return expr;
  return expr
    .replace(/\\mathrm\{([^}]+)\}/g, '$1')
    .replace(/\\text\{([^}]+)\}/g, '$1')
    .replace(/\\displaystyle\s*/g, '')
    .replace(/\\frac\{([^}]+)\}\{([^}]+)\}/g, '($1 / $2)')
    .replace(/\\sqrt\{([^}]+)\}/g, '√($1)')
    .replace(/\\,/g, ' ')
    .replace(/~/g, ' ')
    .replace(/\\cdot/g, '·')
    .replace(/\\times/g, '×')
    .replace(/\\left|\\right/g, '')
    .trim();
}

export function renderFormulaHtml(rawExpr: string): string | null {
  if (!rawExpr || typeof rawExpr !== 'string') return null;
  const trimmed = rawExpr.trim();
  const clean = trimmed
    .replace(/^\${1,2}/, '')
    .replace(/\${1,2}$/, '')
    .trim();
  if (!clean) return null;
  try {
    return katex.renderToString(clean, {
      throwOnError: false,
      displayMode: false,
      strict: false,
    });
  } catch {
    return null;
  }
}
