'use client';

import {
  BookOpenText,
  Braces,
  Check,
  ChevronDown,
  CircleDot,
  FlaskConical,
  GitBranch,
  History,
  Link2,
  Network,
  Plus,
  Search,
  ShieldCheck,
  Sparkles,
} from 'lucide-react';
import { SyntheticEvent, useEffect, useMemo, useState } from 'react';
import { Badge } from '@/components/ui/badge';
import { Button } from '@/components/ui/button';
import { Input } from '@/components/ui/input';
import { normalizeArxivHtmlUrl, PaperUrlError } from '@/lib/paper-url';

type FormulaNode = {
  id: string;
  label: string;
  formula: string;
  type: 'evidence' | 'concept' | 'hypothesis';
  x: number;
  y: number;
  confidence: number;
  source: string;
  relation: string;
};

type ResearchWorkspaceProps = {
  user: {
    displayName: string;
    email: string;
  };
};

const nodes: FormulaNode[] = [
  {
    id: 'dot-product',
    label: 'Dot-product attention',
    formula: 'A(Q,K,V) = softmax(QKᵀ)V',
    type: 'evidence',
    x: 10,
    y: 42,
    confidence: 1,
    source: 'Section 3.2 · Eq. 1',
    relation: 'baseline',
  },
  {
    id: 'scaled',
    label: 'Scaled attention',
    formula: 'softmax(QKᵀ / √dₖ)V',
    type: 'evidence',
    x: 39,
    y: 18,
    confidence: 0.99,
    source: 'Section 3.2 · Eq. 1',
    relation: 'stabilizes',
  },
  {
    id: 'multi-head',
    label: 'Multi-head composition',
    formula: 'Concat(head₁…headₕ)Wᴼ',
    type: 'evidence',
    x: 69,
    y: 38,
    confidence: 0.98,
    source: 'Section 3.2 · Eq. 2',
    relation: 'generalizes',
  },
  {
    id: 'kernel',
    label: 'Kernel substitution',
    formula: 'φ(Q)(φ(K)ᵀV) / Z',
    type: 'concept',
    x: 31,
    y: 68,
    confidence: 0.91,
    source: 'Cross-paper concept',
    relation: 'approximates',
  },
  {
    id: 'mashup',
    label: 'Gated kernel heads',
    formula: 'g ⊙ Hsoftmax + (1-g) ⊙ Hkernel',
    type: 'hypothesis',
    x: 67,
    y: 76,
    confidence: 0.72,
    source: 'Hypothesis H-004',
    relation: 'combines',
  },
];

const connections = [
  ['dot-product', 'scaled'],
  ['scaled', 'multi-head'],
  ['dot-product', 'kernel'],
  ['kernel', 'mashup'],
  ['multi-head', 'mashup'],
];

const paperSections = [
  { label: '3.2.1 Scaled Dot-Product', count: 1, active: true },
  { label: '3.2 Multi-Head Attention', count: 4, active: false },
  { label: '3.3 Feed-Forward Networks', count: 1, active: false },
  { label: '3.5 Positional Encoding', count: 2, active: false },
  { label: '5.3 Optimizer', count: 1, active: false },
];

function nodeTone(type: FormulaNode['type']) {
  if (type === 'hypothesis') return 'hypothesis-node';
  if (type === 'concept') return 'concept-node';
  return 'evidence-node';
}

export default function ResearchWorkspace({ user }: ResearchWorkspaceProps) {
  const [selectedId, setSelectedId] = useState('scaled');
  const [isImporting, setIsImporting] = useState(false);
  const [paperUrl, setPaperUrl] = useState(
    'https://arxiv.org/html/1706.03762',
  );
  const [notice, setNotice] = useState(
    'Demo graph loaded · source extraction service is next',
  );
  const selected = useMemo(
    () => nodes.find((node) => node.id === selectedId) ?? nodes[1],
    [selectedId],
  );

  useEffect(() => {
    const context = document.modelContext;
    if (!context?.registerTool) return;

    const lifecycle = new AbortController();
    const registration = context.registerTool(
      {
        name: 'stage_paper_import',
        title: 'Stage paper import',
        description:
          'Validate an arXiv HTML or abstract URL and place its canonical HTML URL in the visible import field. This stages the import but does not submit it.',
        inputSchema: {
          type: 'object',
          properties: {
            url: {
              type: 'string',
              description: 'An HTTPS arxiv.org /html/ or /abs/ paper URL.',
            },
          },
          required: ['url'],
          additionalProperties: false,
        },
        annotations: {
          readOnlyHint: false,
          untrustedContentHint: false,
        },
        execute(input) {
          if (
            !input ||
            typeof input !== 'object' ||
            !('url' in input) ||
            typeof input.url !== 'string'
          ) {
            throw new PaperUrlError('A paper URL string is required.');
          }
          const canonicalUrl = normalizeArxivHtmlUrl(input.url);
          setPaperUrl(canonicalUrl);
          setNotice('URL staged by your AI assistant · review and import when ready');
          return { status: 'staged', canonicalUrl };
        },
      },
      { signal: lifecycle.signal },
    );

    void Promise.resolve(registration).catch(() => {
      // WebMCP is progressive enhancement; the visible form remains available.
    });
    return () => lifecycle.abort();
  }, []);

  async function handleImport(event: SyntheticEvent<HTMLFormElement>) {
    event.preventDefault();
    try {
      const canonicalUrl = normalizeArxivHtmlUrl(paperUrl);
      setPaperUrl(canonicalUrl);
      setIsImporting(true);
      setNotice('Extracting MathML and source anchors…');
      const response = await fetch('/api/imports/preview', {
        method: 'POST',
        headers: { 'content-type': 'application/json' },
        body: JSON.stringify({ url: canonicalUrl }),
      });
      const result = (await response.json()) as {
        code?: string;
        equations?: unknown[];
        title?: string;
      };
      if (!response.ok) {
        setNotice(
          result.code === 'GRAPH_API_UNAVAILABLE'
            ? 'Extractor service is offline · the curated demo graph remains available'
            : `Import stopped · ${result.code ?? 'unknown error'}`,
        );
        return;
      }
      setNotice(
        `${result.equations?.length ?? 0} equations extracted from ${result.title ?? 'paper'}`,
      );
    } catch (error) {
      setNotice(
        error instanceof PaperUrlError
          ? error.message
          : 'Enter a valid paper URL.',
      );
    } finally {
      setIsImporting(false);
    }
  }

  return (
    <main className="app-shell">
      <header className="topbar">
        <div className="brand-lockup">
          <div className="brand-mark" aria-hidden="true">
            <Network size={19} strokeWidth={2.2} />
          </div>
          <div>
            <p className="brand-name">FormulaGraph</p>
            <p className="brand-suffix">LAB / 01</p>
          </div>
        </div>

        <nav className="top-nav" aria-label="Primary navigation">
          <button className="nav-item">Library</button>
          <button className="nav-item nav-item-active">Graph</button>
          <button className="nav-item">Experiments</button>
        </nav>

        <div className="top-actions">
          <button className="icon-button" aria-label="Search graph">
            <Search size={18} />
          </button>
          <div className="workspace-pill">
            <span className="workspace-dot" />
            <span title={user.email}>{user.displayName}</span>
            <ChevronDown size={14} />
          </div>
        </div>
      </header>

      <section className="import-strip" aria-label="Import a paper">
        <div className="import-label">
          <Link2 size={16} />
          HTML source
        </div>
        <form className="import-form" onSubmit={handleImport}>
          <Input
            aria-label="arXiv HTML paper URL"
            value={paperUrl}
            onChange={(event) => setPaperUrl(event.target.value)}
            className="paper-url-input"
            spellCheck={false}
          />
          <Button className="import-button" type="submit" disabled={isImporting}>
            <Plus size={16} />
            {isImporting ? 'Extracting…' : 'Import paper'}
          </Button>
        </form>
        <p className="import-notice" aria-live="polite">
          <CircleDot size={13} />
          {notice}
        </p>
      </section>

      <div className="research-grid">
        <aside className="source-panel">
          <div className="panel-heading">
            <div>
              <p className="eyebrow">Evidence</p>
              <h2>Paper sources</h2>
            </div>
            <Badge variant="outline" className="count-badge">
              01
            </Badge>
          </div>

          <article className="paper-card">
            <div className="paper-index">P–01</div>
            <Badge className="version-badge">v7 · current</Badge>
            <h3>Attention Is All You Need</h3>
            <p>Vaswani et al. · arXiv:1706.03762</p>
            <div className="paper-meta">
              <span>
                <Braces size={14} />9 equations
              </span>
              <span>
                <History size={14} />6 versions
              </span>
            </div>
          </article>

          <div className="section-list">
            <p className="list-label">Extracted structure</p>
            {paperSections.map((section) => (
              <button
                className={`section-row ${section.active ? 'section-row-active' : ''}`}
                key={section.label}
              >
                <span>{section.label}</span>
                <span>{section.count}</span>
              </button>
            ))}
          </div>

          <div className="provenance-note">
            <ShieldCheck size={18} />
            <div>
              <strong>Source-bound evidence</strong>
              <p>Every extracted fact retains its paper version and HTML anchor.</p>
            </div>
          </div>
        </aside>

        <section className="graph-panel" aria-label="Formula relationship graph">
          <div className="graph-header">
            <div>
              <p className="eyebrow">Temporal formula graph</p>
              <h1>Attention lineage</h1>
            </div>
            <div className="legend" aria-label="Graph legend">
              <span><i className="legend-dot evidence-dot" />Evidence</span>
              <span><i className="legend-dot concept-dot" />Concept</span>
              <span><i className="legend-dot hypothesis-dot" />Hypothesis</span>
            </div>
          </div>

          <div className="graph-stage">
            <div className="graph-grid" aria-hidden="true" />
            <svg
              className="connection-layer"
              viewBox="0 0 100 100"
              preserveAspectRatio="none"
              aria-hidden="true"
            >
              {connections.map(([fromId, toId]) => {
                const from = nodes.find((node) => node.id === fromId)!;
                const to = nodes.find((node) => node.id === toId)!;
                return (
                  <line
                    key={`${fromId}-${toId}`}
                    x1={from.x + 9}
                    y1={from.y + 5}
                    x2={to.x + 9}
                    y2={to.y + 5}
                  />
                );
              })}
            </svg>

            {nodes.map((node) => (
              <button
                key={node.id}
                type="button"
                className={`formula-node ${nodeTone(node.type)} ${
                  selected.id === node.id ? 'formula-node-selected' : ''
                }`}
                style={{ left: `${node.x}%`, top: `${node.y}%` }}
                onClick={() => setSelectedId(node.id)}
                aria-pressed={selected.id === node.id}
              >
                <span className="node-kicker">
                  {node.type === 'hypothesis' ? 'HYPOTHESIS' : node.relation}
                </span>
                <strong>{node.label}</strong>
                <code>{node.formula}</code>
              </button>
            ))}

            <div className="graph-status">
              <span className="pulse-dot" />
              Graph is version-aware
              <span>14 relationships</span>
            </div>
          </div>

          <div className="hypothesis-dock">
            <div className="hypothesis-icon">
              <Sparkles size={18} />
            </div>
            <div>
              <p>Research move</p>
              <strong>Combine selected formulas under typed constraints</strong>
            </div>
            <Button variant="outline" className="mashup-button">
              <FlaskConical size={16} />
              Open mashup
            </Button>
          </div>
        </section>

        <aside className="inspector-panel">
          <div className="panel-heading inspector-heading">
            <div>
              <p className="eyebrow">Inspector</p>
              <h2>{selected.label}</h2>
            </div>
            <span className={`type-token type-${selected.type}`}>
              {selected.type}
            </span>
          </div>

          <div className="formula-display">
            <p>Canonical expression</p>
            <code>{selected.formula}</code>
          </div>

          <section className="inspector-section">
            <div className="section-title">
              <h3>Relation</h3>
              <span>{Math.round(selected.confidence * 100)}% confidence</span>
            </div>
            <div className="relation-card">
              <GitBranch size={17} />
              <div>
                <p>{selected.relation}</p>
                <strong>{selected.source}</strong>
              </div>
            </div>
          </section>

          <section className="inspector-section">
            <div className="section-title">
              <h3>Symbol contract</h3>
              <span>4 symbols</span>
            </div>
            <div className="symbol-table">
              <div><code>Q</code><span>query tensor</span><b>n × dₖ</b></div>
              <div><code>K</code><span>key tensor</span><b>m × dₖ</b></div>
              <div><code>V</code><span>value tensor</span><b>m × dᵥ</b></div>
              <div><code>dₖ</code><span>key dimension</span><b>ℕ⁺</b></div>
            </div>
          </section>

          <section className="inspector-section">
            <div className="section-title">
              <h3>Validation</h3>
              <span>latest run</span>
            </div>
            <ul className="check-list">
              <li><Check size={14} />Tensor shapes align</li>
              <li><Check size={14} />Normalization domain valid</li>
              <li><Check size={14} />Source anchor resolved</li>
            </ul>
          </section>

          <button className="source-link">
            <BookOpenText size={16} />
            Open equation in source
            <span>↗</span>
          </button>
        </aside>
      </div>
    </main>
  );
}
