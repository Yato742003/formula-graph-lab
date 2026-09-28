'use client';

import ProblemSpecPanel from './problem-spec-panel';
import ResearchCreatePanel from './research-create-panel';
import ResearchMovePanel from './research-move-panel';

import {
  type CompatibilityView,
  type LineageView,
  type CoverageView,
  type ResearchSourceContext,
  parseCompatibilityView,
  parseLineageView,
  parseCoverageView,
  parseResearchSourceContext,
} from '@/lib/research-view';

import {
  AlertTriangle,
  ArrowRight,
  BookOpenText,
  Braces,
  Check,
  ChevronDown,
  CircleDot,
  Copy,
  FileCode2,
  GitBranch,
  History,
  Link2,
  Network,
  PanelLeftClose,
  PanelLeftOpen,
  PanelRightClose,
  PanelRightOpen,
  Plus,
  Search,
  ShieldCheck,
  X,
} from 'lucide-react';
import {
  SyntheticEvent,
  useCallback,
  useEffect,
  useMemo,
  useRef,
  useState,
} from 'react';
import EvidenceGraphViewport, {
  renderFormulaHtml,
  type GraphViewportEdge,
  type GraphViewportNode,
} from '@/app/evidence-graph-viewport';
import { Badge } from '@/components/ui/badge';
import { Button } from '@/components/ui/button';
import { Input } from '@/components/ui/input';
import type {
  EvidenceGraphEdge,
  EvidenceGraphNode,
  EvidenceGraphSnapshotResponse,
  EvidenceSearchHit,
  EvidenceSearchResponse,
  WorkspaceImportResponse,
} from '@/lib/import-types';
import { normalizeArxivHtmlUrl, PaperUrlError } from '@/lib/paper-url';

type FormulaNode = {
  id: string;
  label: string;
  formula: string;
  type: 'evidence' | 'concept' | 'hypothesis';
  x: number;
  y: number;
  source: string;
  relation: string;
};

type ResearchWorkspaceProps = {
  user: {
    displayName: string;
    email: string;
  };
};

type InspectorRecord = {
  id: string;
  label: string;
  expression: string;
  tone: FormulaNode['type'];
  kind: string;
  confidence: number | null;
  source: string;
  relation: string;
  paperLabel: string;
  verificationStatus: string;
  episodeCount: number;
  anchor: string;
  anchorIsSource: boolean;
  section: string;
  extractionMethod: string;
  sourceHref: string;
  sourceText: string;
  validAt: string;
  episodeIds: string[];
  relations: Array<{
    id: string;
    direction: 'incoming' | 'outgoing';
    relation: string;
    neighbor: string;
    episodeCount: number;
    validAt: string;
  }>;
  superseded: boolean;
  formulaAnalysis: {
    status: string;
    canonicalHash: string;
    requiresReview: boolean;
    domainStatus: string;
    issueCount: number;
    contracts: Array<{
      name: string;
      category: string;
      shape: string;
      inferenceConfidence: number;
      reviewRequired: boolean;
    }>;
  } | null;
};

const demoNodes: FormulaNode[] = [
  {
    id: 'dot-product',
    label: 'Dot-product attention',
    formula: 'A(Q,K,V) = softmax(QKᵀ)V',
    type: 'evidence',
    x: 10,
    y: 42,
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
    source: 'Hypothesis H-004',
    relation: 'combines',
  },
];

const demoConnections: GraphViewportEdge[] = [
  {
    id: 'demo-scaled',
    source: 'dot-product',
    target: 'scaled',
    relation: 'derived_from',
  },
  {
    id: 'demo-heads',
    source: 'scaled',
    target: 'multi-head',
    relation: 'generalizes',
  },
  {
    id: 'demo-kernel',
    source: 'dot-product',
    target: 'kernel',
    relation: 'approximates',
  },
  {
    id: 'demo-mashup-kernel',
    source: 'kernel',
    target: 'mashup',
    relation: 'derived_from',
  },
  {
    id: 'demo-mashup-heads',
    source: 'multi-head',
    target: 'mashup',
    relation: 'derived_from',
  },
];

const demoPaperSections = [
  { label: '3.2.1 Scaled Dot-Product', count: 1, active: true },
  { label: '3.2 Multi-Head Attention', count: 4, active: false },
  { label: '3.3 Feed-Forward Networks', count: 1, active: false },
  { label: '3.5 Positional Encoding', count: 2, active: false },
  { label: '5.3 Optimizer', count: 1, active: false },
];

function authorsLabel(authors: string[]): string {
  if (authors.length === 0) return 'Authors unavailable';
  if (authors.length === 1) return authors[0];
  return `${authors[0]} et al.`;
}

function payloadString(
  payload: Record<string, unknown>,
  fields: string[],
  fallback: string,
): string {
  for (const field of fields) {
    const value = payload[field];
    if (typeof value === 'string' && value.trim()) return value;
  }
  return fallback;
}

function graphNodeLabel(node: EvidenceGraphNode): string {
  if (node.kind === 'Equation') {
    const number = node.payload.equation_number;
    if (typeof number === 'string' && number.trim())
      return `Equation ${number}`;
  }
  return payloadString(
    node.payload,
    ['title', 'section', 'statement', 'semantic_name', 'description'],
    `${node.kind} · ${node.logical_id}`,
  );
}

function graphNodeExpression(node: EvidenceGraphNode): string {
  return payloadString(
    node.payload,
    ['latex', 'statement', 'notation', 'description', 'arxiv_id'],
    node.logical_id,
  );
}

function graphNodeMeta(node: EvidenceGraphNode): string {
  const anchor = node.payload.anchor;
  if (typeof anchor === 'string' && anchor.trim()) return `#${anchor}`;
  return node.version ? `${node.paper_id}v${node.version}` : node.paper_id;
}

function graphTone(kind: EvidenceGraphNode['kind']): FormulaNode['type'] {
  if (kind === 'Hypothesis') return 'hypothesis';
  if (kind === 'Concept') return 'concept';
  return 'evidence';
}

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === 'object' && value !== null && !Array.isArray(value);
}

function formulaAnalysisFromPayload(
  payload: Record<string, unknown>,
): InspectorRecord['formulaAnalysis'] {
  const analysis = payload.formula_analysis;
  if (!isRecord(analysis)) return null;
  const canonicalHash = analysis.canonical_hash;
  const status = analysis.status;
  if (typeof canonicalHash !== 'string' || typeof status !== 'string')
    return null;
  const contracts = Array.isArray(analysis.contracts)
    ? analysis.contracts.flatMap((value) => {
        if (!isRecord(value)) return [];
        const inferenceConfidence =
          typeof value.inference_confidence === 'number'
            ? value.inference_confidence
            : value.confidence;
        const reviewRequired =
          typeof value.review_required === 'boolean'
            ? value.review_required
            : value.confirmed === false;
        if (
          typeof value.name !== 'string' ||
          typeof value.category !== 'string' ||
          typeof inferenceConfidence !== 'number' ||
          typeof reviewRequired !== 'boolean'
        ) {
          return [];
        }
        const shape = Array.isArray(value.shape)
          ? value.shape.map(String).join(' × ') || 'scalar'
          : 'n/a';
        return [
          {
            name: value.name,
            category: value.category,
            shape,
            inferenceConfidence,
            reviewRequired,
          },
        ];
      })
    : [];
  const shapeErrors = Array.isArray(analysis.shape_errors)
    ? analysis.shape_errors.length
    : 0;
  const domainAssessment = isRecord(analysis.domain_assessment)
    ? analysis.domain_assessment
    : null;
  const domainObligations = Array.isArray(domainAssessment?.obligations)
    ? domainAssessment.obligations.filter(
        (value) =>
          isRecord(value) &&
          ['unresolved', 'contradictory', 'unsupported'].includes(
            String(value.status),
          ),
      ).length
    : Array.isArray(analysis.domain_errors)
      ? analysis.domain_errors.length
      : 0;
  return {
    status: status.replaceAll('_', ' '),
    canonicalHash,
    requiresReview:
      analysis.requires_review === true ||
      analysis.requires_confirmation === true,
    domainStatus:
      typeof domainAssessment?.status === 'string'
        ? domainAssessment.status.replaceAll('_', ' ')
        : domainObligations > 0
          ? 'unresolved'
          : 'not assessed',
    issueCount: shapeErrors + domainObligations,
    contracts,
  };
}

export function buildEvidenceInspector(
  node: EvidenceGraphNode | EvidenceSearchHit,
  sourceUrl: string,
  nodes: EvidenceGraphNode[],
  edges: EvidenceGraphEdge[],
): InspectorRecord {
  const anchor = payloadString(node.payload, ['anchor', 'source_anchor'], '');
  const section = payloadString(node.payload, ['section', 'title'], '—');
  const confidenceValue = node.payload.confidence;
  const confidence =
    typeof confidenceValue === 'number' && Number.isFinite(confidenceValue)
      ? Math.min(1, Math.max(0, confidenceValue))
      : null;
  const matchingEdges = edges.filter(
    (edge) => edge.source_uuid === node.uuid || edge.target_uuid === node.uuid,
  );
  const relation =
    'match_sources' in node
      ? node.match_sources.join(' + ')
      : (matchingEdges[0]?.relation.replaceAll('_', ' ') ?? 'source-bound');
  const pinnedSource =
    sourceUrl ||
    `https://arxiv.org/html/${node.paper_id}${node.version ? `v${node.version}` : ''}`;
  const anchorIsSource = node.payload.anchor_is_source === true;
  return {
    id: node.uuid,
    label: graphNodeLabel(node),
    expression: graphNodeExpression(node),
    tone: graphTone(node.kind),
    kind: node.kind,
    confidence,
    source: `${section}${anchor ? ` · #${anchor}` : ''}`,
    relation,
    paperLabel: `${node.paper_id}${node.version ? `v${node.version}` : ''}`,
    verificationStatus: node.verification_status.replaceAll('_', ' '),
    episodeCount: node.episode_uuids.length,
    anchor: anchor || '—',
    anchorIsSource,
    section,
    extractionMethod: payloadString(
      node.payload,
      ['extraction_method'],
      'exact evidence',
    ).replaceAll('_', ' '),
    sourceHref: `${pinnedSource}${anchorIsSource && anchor ? `#${encodeURIComponent(anchor)}` : ''}`,
    sourceText:
      [node.payload.preceding_text, node.payload.following_text]
        .filter(
          (value): value is string =>
            typeof value === 'string' && Boolean(value.trim()),
        )
        .join(' ') ||
      payloadString(
        node.payload,
        ['text', 'statement'],
        'Source text unavailable',
      ),
    validAt: node.valid_at ?? 'Unknown source time',
    episodeIds: node.episode_uuids,
    relations: matchingEdges.map((edge) => {
      const direction =
        edge.source_uuid === node.uuid ? 'outgoing' : 'incoming';
      const neighborId =
        direction === 'outgoing' ? edge.target_uuid : edge.source_uuid;
      const neighbor = nodes.find((candidate) => candidate.uuid === neighborId);
      return {
        id: edge.uuid,
        direction,
        relation: edge.relation.replaceAll('_', ' '),
        neighbor: neighbor ? graphNodeLabel(neighbor) : neighborId,
        episodeCount: edge.episode_uuids.length,
        validAt: edge.valid_at ?? 'Unknown relation time',
      };
    }),
    superseded: matchingEdges.some(
      (edge) =>
        edge.relation === 'supersedes' && edge.target_uuid === node.uuid,
    ),
    formulaAnalysis: formulaAnalysisFromPayload(node.payload),
  };
}

function demoInspector(node: FormulaNode): InspectorRecord {
  return {
    id: node.id,
    label: node.label,
    expression: node.formula,
    tone: node.type,
    kind: node.type,
    confidence: null,
    source: node.source,
    relation: node.relation,
    paperLabel: '1706.03762v7',
    verificationStatus: 'curated demo',
    episodeCount: 1,
    anchor: 'S3.SS2',
    anchorIsSource: true,
    section: '3.2 Scaled Dot-Product Attention',
    extractionMethod: 'curated demo',
    sourceHref: 'https://arxiv.org/html/1706.03762v7',
    sourceText:
      'The attention output is a weighted sum of values, with weights derived from query–key compatibility.',
    validAt: 'Curated demonstration',
    episodeIds: ['demo-attention-episode'],
    relations: [],
    superseded: false,
    formulaAnalysis: null,
  };
}

function importErrorMessage(code?: string): string {
  switch (code) {
    case 'AUTH_REQUIRED':
      return 'Your sign-in expired · reload to continue';
    case 'DATABASE_UNAVAILABLE':
      return 'Workspace storage is temporarily unavailable';
    case 'EXTRACTION_FAILED':
      return 'This paper could not be extracted safely';
    case 'GRAPH_API_NOT_CONFIGURED':
      return 'The graph service is not connected in this environment';
    case 'GRAPH_API_RESPONSE_TOO_LARGE':
      return 'The extracted paper exceeds the current import limit';
    case 'GRAPH_API_UNAVAILABLE':
      return 'The graph service is temporarily unavailable';
    default:
      return `Import stopped · ${code ?? 'unknown error'}`;
  }
}

function searchErrorMessage(code?: string): string {
  switch (code) {
    case 'AUTH_REQUIRED':
      return 'Your sign-in expired · reload to continue';
    case 'DATABASE_UNAVAILABLE':
      return 'Workspace search is temporarily unavailable';
    case 'GRAPH_API_NOT_CONFIGURED':
      return 'The graph service is not connected in this environment';
    case 'GRAPH_API_RESPONSE_TOO_LARGE':
      return 'Search returned more evidence than this view can safely display';
    case 'GRAPH_API_UNAVAILABLE':
      return 'The graph service is temporarily unavailable';
    case 'SEARCH_REJECTED':
      return 'The search request was rejected by the evidence service';
    default:
      return `Search stopped · ${code ?? 'unknown error'}`;
  }
}

function graphErrorMessage(code?: string): string {
  switch (code) {
    case 'AUTH_REQUIRED':
      return 'Your sign-in expired · reload to continue';
    case 'DATABASE_UNAVAILABLE':
      return 'Saved graph metadata is temporarily unavailable';
    case 'GRAPH_API_NOT_CONFIGURED':
      return 'The graph service is not connected in this environment';
    case 'GRAPH_API_RESPONSE_TOO_LARGE':
      return 'The saved graph exceeds the current display limit';
    case 'GRAPH_API_INVALID_RESPONSE':
      return 'The graph service returned an invalid snapshot';
    case 'GRAPH_API_UNAVAILABLE':
    case 'GRAPH_LOAD_FAILED':
      return 'The saved graph is temporarily unavailable';
    default:
      return `Graph restore stopped · ${code ?? 'unknown error'}`;
  }
}

function searchHitTitle(hit: EvidenceSearchHit): string {
  const title = hit.payload.title;
  if (typeof title === 'string' && title.trim()) return title;
  const section = hit.payload.section;
  if (typeof section === 'string' && section.trim()) return section;
  return `${hit.kind} · ${hit.logical_id}`;
}

function searchHitExcerpt(hit: EvidenceSearchHit): string {
  for (const field of ['latex', 'statement', 'text', 'arxiv_id']) {
    const value = hit.payload[field];
    if (typeof value === 'string' && value.trim()) return value;
  }
  return `${hit.paper_id}${hit.version ? `v${hit.version}` : ''}`;
}

function LineageSourceRefs({
  refs,
}: {
  refs: LineageView['evidence']['refs'];
}) {
  const [selected, setSelected] = useState(0);
  const [context, setContext] = useState<ResearchSourceContext | null>(null);
  const [notice, setNotice] = useState('Loading source context…');
  const source = refs[selected];

  useEffect(() => {
    if (!source) return;
    const controller = new AbortController();
    void (async () => {
      try {
        const response = await fetch(
          `/api/research/lineage/sources?source_id=${encodeURIComponent(source.id)}`,
          { signal: controller.signal },
        );
        if (!response.ok) throw new Error('Source unavailable');
        const resolved = parseResearchSourceContext(
          await response.json(),
          source,
        );
        if (!controller.signal.aborted) {
          setContext(resolved);
          setNotice('Source context resolved from this workspace.');
        }
      } catch {
        if (!controller.signal.aborted) {
          setContext(null);
          setNotice(
            'Source context unavailable; assertion remains unverified.',
          );
        }
      }
    })();
    return () => controller.abort();
  }, [source]);

  let sourceHref: string | null = null;
  if (context && source) {
    try {
      sourceHref = `${normalizeArxivHtmlUrl(`https://arxiv.org/html/${context.paperId}v${context.version}`)}#${encodeURIComponent(source.anchor)}`;
    } catch {
      /* An authorized context may lack a supported public HTML URL. */
    }
  }

  return (
    <div className="space-y-1.5">
      <p className="font-medium">Source evidence</p>
      <div className="flex flex-wrap gap-1">
        {refs.map((ref, index) => (
          <button
            key={ref.id}
            type="button"
            aria-pressed={index === selected}
            className="rounded border border-border/60 px-2 py-1 text-[10px] hover:bg-muted/50"
            onClick={() => {
              setSelected(index);
              setContext(null);
              setNotice('Loading source context…');
            }}
          >
            {ref.anchor} · {ref.id}
          </button>
        ))}
      </div>
      <output className="block text-muted-foreground" aria-live="polite">
        {notice}
      </output>
      {context ? (
        <div className="rounded bg-muted/30 p-2 space-y-1">
          <p>
            {context.paperId} v{context.version} · {context.kind} ·{' '}
            {source?.anchor}
          </p>
          <p className="whitespace-pre-wrap break-words">
            {context.text ||
              context.latex ||
              'No extracted text in this source node.'}
          </p>
          {sourceHref ? (
            <a
              href={sourceHref}
              target="_blank"
              rel="noopener noreferrer"
              className="underline"
            >
              Open HTML anchor ↗
            </a>
          ) : (
            <p>
              Public HTML link unavailable; inspect the stored context above.
            </p>
          )}
        </div>
      ) : null}
    </div>
  );
}

export default function ResearchWorkspace({ user }: ResearchWorkspaceProps) {
  const [selectedId, setSelectedId] = useState<string | null>('scaled');
  const [isImporting, setIsImporting] = useState(false);
  const [imported, setImported] = useState<WorkspaceImportResponse | null>(
    null,
  );
  const [graphSnapshot, setGraphSnapshot] =
    useState<EvidenceGraphSnapshotResponse | null>(null);
  const [isGraphLoading, setIsGraphLoading] = useState(false);
  const [graphNotice, setGraphNotice] = useState('Loading saved evidence…');
  const [selectedSearchHit, setSelectedSearchHit] =
    useState<EvidenceSearchHit | null>(null);
  const [paperUrl, setPaperUrl] = useState('https://arxiv.org/html/1706.03762');
  const [notice, setNotice] = useState(
    'Curated demo · import an arXiv HTML paper to replace it',
  );
  const [isSearchOpen, setIsSearchOpen] = useState(false);
  const [isSearching, setIsSearching] = useState(false);
  const [searchQuery, setSearchQuery] = useState('');
  const [searchResult, setSearchResult] =
    useState<EvidenceSearchResponse | null>(null);
  const [searchNotice, setSearchNotice] = useState(
    'Search exact symbols, concepts, claims, and neighboring evidence',
  );
  const [isLeftCollapsed, setIsLeftCollapsed] = useState(false);
  const [isRightCollapsed, setIsRightCollapsed] = useState(false);
  const [isZenMode, setIsZenMode] = useState(false);
  const [isImportExpanded, setIsImportExpanded] = useState(false);

  const toggleZenMode = useCallback(() => {
    setIsZenMode((prev) => {
      const next = !prev;
      setIsLeftCollapsed(next);
      setIsRightCollapsed(next);
      return next;
    });
  }, []);

  useEffect(() => {
    const handleKeyDown = (e: KeyboardEvent) => {
      const target = e.target as HTMLElement | null;
      const isInput =
        target &&
        (target.tagName === 'INPUT' ||
          target.tagName === 'TEXTAREA' ||
          target.isContentEditable);

      if (e.key === 'Escape') {
        if (isSearchOpen) {
          setIsSearchOpen(false);
        } else if (selectedId) {
          setSelectedId(null);
        }
        return;
      }

      if (isInput) return;

      if (
        (e.key.toLowerCase() === 'k' && (e.ctrlKey || e.metaKey)) ||
        e.key === '/'
      ) {
        e.preventDefault();
        setIsSearchOpen((open) => !open);
        return;
      }

      if (
        e.key.toLowerCase() === 'z' &&
        !e.metaKey &&
        !e.ctrlKey &&
        !e.altKey
      ) {
        e.preventDefault();
        toggleZenMode();
      }
    };
    window.addEventListener('keydown', handleKeyDown);
    return () => window.removeEventListener('keydown', handleKeyDown);
  }, [toggleZenMode, isSearchOpen, selectedId]);

  useEffect(() => {
    const rafId = requestAnimationFrame(() => {
      window.dispatchEvent(new Event('resize'));
    });
    return () => cancelAnimationFrame(rafId);
  }, [isLeftCollapsed, isRightCollapsed, isZenMode]);

  // Suppress benign ResizeObserver loop notifications in dev overlays
  useEffect(() => {
    const handleResizeObserverError = (e: ErrorEvent) => {
      if (
        e.message?.includes(
          'ResizeObserver loop completed with undelivered notifications',
        ) ||
        e.message?.includes('ResizeObserver loop limit exceeded')
      ) {
        e.stopImmediatePropagation();
        e.preventDefault();
      }
    };
    window.addEventListener('error', handleResizeObserverError, true);
    return () =>
      window.removeEventListener('error', handleResizeObserverError, true);
  }, []);

  // --- Sprint 5A State & Handlers ---
  const [activeViewTab, setActiveViewTab] = useState<
    'lineage' | 'spec' | 'compat'
  >('spec');
  const activePrimaryView =
    activeViewTab !== 'lineage'
      ? 'research'
      : isLeftCollapsed
        ? 'graph'
        : 'papers';

  function selectPrimaryView(view: 'papers' | 'graph' | 'research') {
    if (view === 'research') {
      setActiveViewTab('spec');
      setIsImportExpanded(false);
      return;
    }

    setActiveViewTab('lineage');
    setIsImportExpanded(false);
    setIsLeftCollapsed(view === 'graph');
  }

  const [copiedLatex, setCopiedLatex] = useState(false);

  const [compatMappings, setCompatMappings] = useState<CompatibilityView[]>([]);
  const [lineageRelations, setLineageRelations] = useState<LineageView[]>([]);
  const [multiPaperCoverage, setMultiPaperCoverage] = useState<CoverageView[]>(
    [],
  );
  const [selectedLineageId, setSelectedLineageId] = useState<string | null>(
    null,
  );
  const [isReviewingCompatId, setIsReviewingCompatId] = useState<string | null>(
    null,
  );
  const [isReviewingLineageId, setIsReviewingLineageId] = useState<
    string | null
  >(null);
  const [reviewNotes, setReviewNotes] = useState<Record<string, string>>({});
  const [researchLoadNotice, setResearchLoadNotice] = useState(
    'Loading research records…',
  );
  const [researchRecordsState, setResearchRecordsState] = useState<
    'loading' | 'loaded' | 'unavailable'
  >('loading');
  const [researchRecordsPartial, setResearchRecordsPartial] = useState(false);
  const [researchRevision, setResearchRevision] = useState(0);

  useEffect(() => {
    const controller = new AbortController();
    async function loadResearch() {
      try {
        const responses = await Promise.all([
          fetch('/api/research/compatibility?limit=100', {
            signal: controller.signal,
          }),
          fetch('/api/research/lineage?limit=100', {
            signal: controller.signal,
          }),
          fetch('/api/research/lineage/coverage', {
            signal: controller.signal,
          }),
        ]);
        if (responses.some((response) => !response.ok))
          throw new Error('Research unavailable');
        const [compatibility, lineage, coverage] = await Promise.all(
          responses.map((r) => r.json()),
        );
        const mappings = parseCompatibilityView(compatibility);
        const relations = parseLineageView(lineage);
        const papers = parseCoverageView(coverage);
        if (controller.signal.aborted) return;
        setCompatMappings(mappings);
        setLineageRelations(relations);
        setMultiPaperCoverage(papers);
        setResearchRecordsState('loaded');
        const partial =
          (isRecord(compatibility) &&
            Number(compatibility.total) > mappings.length) ||
          (isRecord(lineage) && Number(lineage.total) > relations.length);
        setResearchRecordsPartial(partial);
        setResearchLoadNotice(
          partial
            ? 'Partial research view: showing the first 100 records per list.'
            : mappings.length || relations.length || papers.length
              ? 'Research records loaded from the workspace.'
              : 'No saved research records in this workspace.',
        );
      } catch {
        if (!controller.signal.aborted) {
          setResearchRecordsState('unavailable');
          setResearchLoadNotice(
            'Saved research records could not be loaded. The curated demo remains illustrative only.',
          );
        }
      }
    }
    void loadResearch();
    return () => controller.abort();
  }, [researchRevision]);

  const [researchNotice, setResearchNotice] = useState<string | null>(null);
  const reviewRequests = useRef(
    new Map<string, { body: string; key: string }>(),
  );
  const reviewInFlight = useRef(new Set<string>());

  function reviewRequest(
    url: string,
    payload: { decision: string; notes: string },
  ) {
    const body = JSON.stringify(payload);
    const previous = reviewRequests.current.get(url);
    const request =
      previous?.body === body ? previous : { body, key: crypto.randomUUID() };
    reviewRequests.current.set(url, request);
    return fetch(url, {
      method: 'POST',
      headers: {
        'Content-Type': 'application/json',
        'x-idempotency-key': request.key,
      },
      body: request.body,
    });
  }

  const handleReviewBinding = async (mappingId: string) => {
    const notes = reviewNotes[mappingId]?.trim();
    if (!notes) {
      setResearchNotice('Enter the binding and evidence scope before review.');
      return;
    }
    if (reviewInFlight.current.has('compatibility')) return;
    reviewInFlight.current.add('compatibility');
    setIsReviewingCompatId(mappingId);
    setResearchNotice(null);
    try {
      const url =
        '/api/research/compatibility/' + encodeURIComponent(mappingId);
      const response = await reviewRequest(url + '/reviews', {
        decision: 'reviewed',
        notes,
      });
      if (!response.ok) throw new Error('Review rejected');
      const receipt = await response.json();
      if (
        !isRecord(receipt) ||
        receipt.mapping_id !== mappingId ||
        receipt.decision !== 'reviewed' ||
        typeof receipt.review_id !== 'string'
      )
        throw new Error('Invalid review receipt');
      const refreshed = await fetch(url);
      if (!refreshed.ok) throw new Error('Could not reload assessment');
      const [updated] = parseCompatibilityView({
        items: [await refreshed.json()],
      });
      if (updated.mappingId !== mappingId)
        throw new Error('Invalid assessment');
      setCompatMappings((prev) =>
        prev.map((item) =>
          item.mappingId === mappingId
            ? {
                ...updated,
              }
            : item,
        ),
      );
      setResearchNotice('Review saved and assessment reloaded.');
    } catch {
      setResearchNotice(
        'Could not confirm the review. Displayed assessment is unchanged; retry.',
      );
    } finally {
      reviewInFlight.current.delete('compatibility');
      setIsReviewingCompatId(null);
    }
  };

  const handleReviewLineage = async (
    lineageId: string,
    decision: 'reviewed' | 'rejected',
  ) => {
    const notes = reviewNotes[lineageId]?.trim();
    if (!notes) {
      setResearchNotice('Enter the source evidence and review rationale.');
      return;
    }
    if (reviewInFlight.current.has('lineage')) return;
    reviewInFlight.current.add('lineage');
    setIsReviewingLineageId(lineageId);
    setResearchNotice(null);
    try {
      const response = await reviewRequest(
        '/api/research/lineage/' + encodeURIComponent(lineageId) + '/reviews',
        {
          decision,
          notes,
        },
      );
      if (!response.ok) throw new Error('Review rejected');
      const receipt = await response.json();
      if (
        !isRecord(receipt) ||
        receipt.assertion_id !== lineageId ||
        receipt.status !== decision ||
        !isRecord(receipt.review) ||
        typeof receipt.review.review_id !== 'string'
      ) {
        throw new Error('Invalid review receipt');
      }
      setLineageRelations((prev) =>
        prev.map((item) =>
          item.id === lineageId
            ? {
                ...item,
                status: decision,
              }
            : item,
        ),
      );
      setResearchNotice('Source review saved.');
    } catch {
      setResearchNotice(
        'Could not confirm the source review. Displayed status is unchanged; retry.',
      );
    } finally {
      reviewInFlight.current.delete('lineage');
      setIsReviewingLineageId(null);
    }
  };

  const hasPersistedGraph = Boolean(graphSnapshot?.paper);
  const importNotice =
    hasPersistedGraph && notice.startsWith('Curated demo')
      ? 'Saved paper evidence loaded.'
      : notice;
  const evidenceNodes = useMemo(
    () => graphSnapshot?.nodes ?? [],
    [graphSnapshot],
  );
  const evidenceEdges = useMemo(
    () => graphSnapshot?.edges ?? [],
    [graphSnapshot],
  );
  const graphNodes = useMemo<GraphViewportNode[]>(
    () =>
      hasPersistedGraph
        ? evidenceNodes.map((node) => ({
            id: node.uuid,
            kind: node.kind,
            label: graphNodeLabel(node),
            expression: graphNodeExpression(node),
            meta: graphNodeMeta(node),
          }))
        : demoNodes.map((node) => ({
            id: node.id,
            kind:
              node.type === 'hypothesis'
                ? 'Hypothesis'
                : node.type === 'concept'
                  ? 'Concept'
                  : 'Equation',
            label: node.label,
            expression: node.formula,
            meta: node.source,
          })),
    [evidenceNodes, hasPersistedGraph],
  );
  const graphConnections = useMemo<GraphViewportEdge[]>(
    () =>
      hasPersistedGraph
        ? evidenceEdges.map((edge) => ({
            id: edge.uuid,
            source: edge.source_uuid,
            target: edge.target_uuid,
            relation: edge.relation,
          }))
        : demoConnections,
    [evidenceEdges, hasPersistedGraph],
  );
  const selectedEvidence = useMemo(
    () => evidenceNodes.find((node) => node.uuid === selectedId) ?? null,
    [evidenceNodes, selectedId],
  );
  const selectedDemo = useMemo(
    () => demoNodes.find((node) => node.id === selectedId) ?? demoNodes[0],
    [selectedId],
  );
  const persistedVersionNode = useMemo(
    () =>
      evidenceNodes.find(
        (node) =>
          node.kind === 'PaperVersion' &&
          node.version === graphSnapshot?.paper?.version,
      ) ?? null,
    [evidenceNodes, graphSnapshot],
  );
  const inspector = useMemo(() => {
    const sourceUrl =
      graphSnapshot?.paper?.source_url ??
      imported?.paper.source_url ??
      'https://arxiv.org/html/1706.03762v7';
    if (selectedSearchHit?.uuid === selectedId) {
      return buildEvidenceInspector(
        selectedSearchHit,
        sourceUrl,
        evidenceNodes,
        evidenceEdges,
      );
    }
    if (selectedEvidence) {
      return buildEvidenceInspector(
        selectedEvidence,
        sourceUrl,
        evidenceNodes,
        evidenceEdges,
      );
    }
    return hasPersistedGraph ? null : demoInspector(selectedDemo);
  }, [
    evidenceEdges,
    evidenceNodes,
    graphSnapshot,
    hasPersistedGraph,
    imported,
    selectedDemo,
    selectedEvidence,
    selectedId,
    selectedSearchHit,
  ]);
  const paperSections = useMemo(() => {
    if (hasPersistedGraph) {
      const selectedSectionId = selectedEvidence?.payload.section_id;
      return evidenceNodes
        .filter((node) => node.kind === 'Section')
        .map((node) => ({
          id: node.logical_id,
          graphId: node.uuid,
          label: graphNodeLabel(node),
          count: Array.isArray(node.payload.equation_ids)
            ? node.payload.equation_ids.length
            : 0,
          active: selectedSectionId === node.logical_id,
        }));
    }
    return demoPaperSections.map((section, index) => ({
      ...section,
      id: `demo-${index}`,
      graphId: null,
    }));
  }, [evidenceNodes, hasPersistedGraph, selectedEvidence]);
  const persistedAuthors = Array.isArray(persistedVersionNode?.payload.authors)
    ? persistedVersionNode.payload.authors.filter(
        (author): author is string => typeof author === 'string',
      )
    : [];
  const paperTitle =
    graphSnapshot?.paper?.title ??
    imported?.paper.title ??
    'Attention Is All You Need';
  const paperId =
    graphSnapshot?.paper?.paper_id ?? imported?.paper.paper_id ?? '1706.03762';
  const paperVersion =
    graphSnapshot?.paper?.version ?? imported?.paper.version ?? 7;
  const paperAuthors =
    persistedAuthors.length > 0
      ? persistedAuthors
      : (imported?.paper.authors ?? ['Vaswani et al.']);
  const equationCount = hasPersistedGraph
    ? evidenceNodes.filter((node) => node.kind === 'Equation').length
    : (imported?.paper.equations.length ?? 7);

  const loadPersistedGraph = useCallback(
    async (
      signal?: AbortSignal,
    ): Promise<EvidenceGraphSnapshotResponse | undefined> => {
      await Promise.resolve();
      setIsGraphLoading(true);
      setGraphNotice('Loading saved evidence…');
      try {
        const response = await fetch('/api/graph', {
          method: 'GET',
          headers: { accept: 'application/json' },
          signal,
        });
        const result =
          (await response.json()) as Partial<EvidenceGraphSnapshotResponse> & {
            code?: string;
          };
        if (!response.ok) {
          setGraphNotice(graphErrorMessage(result.code));
          return undefined;
        }
        if (
          !Array.isArray(result.nodes) ||
          !Array.isArray(result.edges) ||
          typeof result.truncated !== 'boolean' ||
          !('paper' in result)
        ) {
          setGraphNotice('The graph service returned an invalid snapshot');
          return undefined;
        }
        const completed = result as EvidenceGraphSnapshotResponse;
        setGraphSnapshot(completed);
        if (completed.nodes.length > 0) {
          setSelectedSearchHit(null);
          setSelectedId((current) =>
            completed.nodes.some((node) => node.uuid === current)
              ? current
              : (completed.nodes.find((node) => node.kind === 'Equation')
                  ?.uuid ?? completed.nodes[0].uuid),
          );
        }
        setGraphNotice(
          completed.paper
            ? `${completed.nodes.length} saved nodes · ${completed.edges.length} relations${completed.truncated ? ' · display limited' : ''}`
            : 'No saved graph yet · showing the curated demo',
        );
        return completed;
      } catch (error) {
        if (error instanceof DOMException && error.name === 'AbortError') {
          return undefined;
        }
        setGraphNotice('The saved graph is temporarily unavailable');
        return undefined;
      } finally {
        setIsGraphLoading(false);
      }
    },
    [],
  );

  useEffect(() => {
    const lifecycle = new AbortController();
    const task = window.setTimeout(() => {
      void loadPersistedGraph(lifecycle.signal);
    }, 0);
    return () => {
      window.clearTimeout(task);
      lifecycle.abort();
    };
  }, [loadPersistedGraph]);

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
          setNotice(
            'URL staged by your AI assistant · review and import when ready',
          );
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
      setNotice('Extracting and persisting source-bound evidence…');
      const response = await fetch('/api/imports', {
        method: 'POST',
        headers: { 'content-type': 'application/json' },
        body: JSON.stringify({ url: canonicalUrl }),
      });
      const result =
        (await response.json()) as Partial<WorkspaceImportResponse> & {
          code?: string;
        };
      if (!response.ok) {
        setNotice(importErrorMessage(result.code));
        return;
      }
      if (!result.paper || !result.receipt || !result.job_id) {
        setNotice('Import stopped · invalid server response');
        return;
      }
      const completed = result as WorkspaceImportResponse;
      setImported(completed);
      const refreshedGraph = await loadPersistedGraph();
      setNotice(
        `${completed.paper.equations.length} equations ${
          completed.receipt.replayed ? 'already synchronized' : 'persisted'
        } · ${completed.paper.title}${refreshedGraph ? '' : ' · graph refresh pending'}`,
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

  async function runSearch(cursor?: string) {
    const query = searchQuery.replaceAll(/\s+/g, ' ').trim();
    if (!query) {
      setSearchNotice('Enter a formula symbol or a research concept');
      return;
    }
    setIsSearching(true);
    setSearchNotice(
      cursor ? 'Loading the next evidence page…' : 'Ranking evidence…',
    );
    try {
      const response = await fetch('/api/search', {
        method: 'POST',
        headers: { 'content-type': 'application/json' },
        body: JSON.stringify({
          query,
          limit: 8,
          ...(cursor ? { cursor } : {}),
        }),
      });
      const result =
        (await response.json()) as Partial<EvidenceSearchResponse> & {
          code?: string;
        };
      if (!response.ok) {
        setSearchNotice(searchErrorMessage(result.code));
        return;
      }
      if (
        !Array.isArray(result.hits) ||
        typeof result.semantic_available !== 'boolean'
      ) {
        setSearchNotice('Search stopped · invalid server response');
        return;
      }
      const completed = result as EvidenceSearchResponse;
      setSearchResult((current) =>
        cursor && current
          ? { ...completed, hits: [...current.hits, ...completed.hits] }
          : completed,
      );
      setSearchNotice(
        `${completed.hits.length} evidence matches · ${
          completed.semantic_available
            ? 'semantic + lexical + graph ranking'
            : 'lexical + graph ranking'
        }`,
      );
    } catch {
      setSearchNotice('Workspace search is temporarily unavailable');
    } finally {
      setIsSearching(false);
    }
  }

  function handleSearch(event: SyntheticEvent<HTMLFormElement>) {
    event.preventDefault();
    void runSearch();
  }

  function selectSearchHit(hit: EvidenceSearchHit) {
    setSelectedSearchHit(hit);
    setSelectedId(hit.uuid);
    setNotice(
      `Search evidence · ${hit.paper_id}${hit.version ? `v${hit.version}` : ''} · ${hit.kind}`,
    );
  }

  function selectGraphNode(nodeId: string) {
    setSelectedSearchHit(null);
    setSelectedId(nodeId);
  }

  return (
    <main className="app-shell" suppressHydrationWarning>
      <output className="sr-only" aria-live="polite">
        {researchLoadNotice}
      </output>
      {researchNotice ? (
        <output className="sr-only" aria-live="polite">
          {researchNotice}
        </output>
      ) : null}
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
          <button
            type="button"
            className={`nav-item ${activePrimaryView === 'papers' ? 'nav-item-active' : ''}`}
            aria-current={activePrimaryView === 'papers' ? 'page' : undefined}
            onClick={() => selectPrimaryView('papers')}
          >
            Papers
          </button>
          <button
            type="button"
            className={`nav-item ${activePrimaryView === 'graph' ? 'nav-item-active' : ''}`}
            aria-current={activePrimaryView === 'graph' ? 'page' : undefined}
            onClick={() => selectPrimaryView('graph')}
          >
            Graph
          </button>
          <button
            type="button"
            className={`nav-item ${activePrimaryView === 'research' ? 'nav-item-active' : ''}`}
            aria-current={activePrimaryView === 'research' ? 'page' : undefined}
            onClick={() => selectPrimaryView('research')}
          >
            Research
          </button>
        </nav>

        <div className="top-actions">
          <button
            type="button"
            className={`icon-button ${isSearchOpen ? 'icon-button-active' : ''}`}
            aria-label={isSearchOpen ? 'Close graph search' : 'Search graph'}
            title="Search graph (Ctrl+K or /)"
            aria-expanded={isSearchOpen}
            aria-controls="graph-search-panel"
            onClick={() => setIsSearchOpen((open) => !open)}
            suppressHydrationWarning
          >
            <Search size={18} />
          </button>
          <div className="workspace-pill">
            <span className="workspace-dot" />
            <span title={user.email}>{user.displayName}</span>
            <ChevronDown size={14} />
          </div>
        </div>
      </header>

      {isSearchOpen ? (
        <div className="command-palette-backdrop">
          <dialog
            open
            className="search-command"
            id="graph-search-panel"
            aria-label="Search evidence graph"
            aria-modal="true"
          >
            <div className="search-command-heading">
              <div className="search-command-icon" aria-hidden="true">
                <Search size={17} />
              </div>
              <div>
                <p className="eyebrow">Hybrid retrieval</p>
                <h2>Find evidence across papers</h2>
              </div>
              <button
                className="search-close"
                type="button"
                aria-label="Close graph search"
                onClick={() => setIsSearchOpen(false)}
              >
                <X size={17} />
              </button>
            </div>
            <div className="search-command-body">
              <form className="search-form" onSubmit={handleSearch}>
                <Input
                  id="search-evidence-query"
                  name="query"
                  aria-label="Formula or research concept"
                  value={searchQuery}
                  onChange={(event) => setSearchQuery(event.target.value)}
                  placeholder="Try d_model, scaled similarity, or convergence…"
                  maxLength={500}
                />
                <Button type="submit" disabled={isSearching}>
                  <Search size={15} />
                  {isSearching ? 'Searching…' : 'Search evidence'}
                </Button>
              </form>
              <p className="search-notice" aria-live="polite">
                <CircleDot size={12} />
                {searchNotice}
              </p>
              {searchResult ? (
                <div
                  className="search-results"
                  aria-label="Evidence search results"
                >
                  {searchResult.hits.map((hit) => (
                    <button
                      className="search-result"
                      key={hit.uuid}
                      type="button"
                      onClick={() => selectSearchHit(hit)}
                    >
                      <span className="search-result-topline">
                        <b>{hit.kind}</b>
                        <span>
                          {hit.paper_id}
                          {hit.version ? `v${hit.version}` : ''}
                        </span>
                      </span>
                      <strong>{searchHitTitle(hit)}</strong>
                      <code>{searchHitExcerpt(hit)}</code>
                      <span className="search-result-signals">
                        {hit.match_sources.map((source) => (
                          <i key={source}>{source}</i>
                        ))}
                        <small>score {hit.score}</small>
                      </span>
                    </button>
                  ))}
                  {searchResult.hits.length === 0 ? (
                    <div className="search-empty">
                      <Braces size={19} />
                      <span>No evidence matched this workspace yet.</span>
                    </div>
                  ) : null}
                  {searchResult.next_cursor ? (
                    <Button
                      variant="outline"
                      className="search-more"
                      type="button"
                      disabled={isSearching}
                      onClick={() =>
                        void runSearch(searchResult.next_cursor ?? undefined)
                      }
                    >
                      Load more evidence
                    </Button>
                  ) : null}
                </div>
              ) : (
                <div
                  className="search-suggestions"
                  aria-label="Search suggestions"
                >
                  {['scaled attention', 'd_model', 'convergence'].map(
                    (suggestion) => (
                      <button
                        key={suggestion}
                        type="button"
                        onClick={() => setSearchQuery(suggestion)}
                      >
                        {suggestion}
                      </button>
                    ),
                  )}
                </div>
              )}
            </div>
          </dialog>
        </div>
      ) : null}

      {activeViewTab !== 'spec' ? (
        <section
          className={`import-strip ${hasPersistedGraph && !isImportExpanded ? 'is-compact' : ''}`}
          aria-label="Import a paper"
        >
        {hasPersistedGraph && !isImportExpanded ? (
          <div className="import-compact-row">
            <div className="import-compact-info">
              <Link2 size={14} className="text-primary shrink-0" />
              <span className="font-semibold text-xs text-foreground">
                Active:
              </span>
              <span className="text-xs text-muted-foreground truncate font-medium">
                arXiv:{paperId} &mdash; {paperTitle}
              </span>
              <Badge
                variant="outline"
                className="text-[10px] py-0 h-4 border-emerald-500/40 text-emerald-600 dark:text-emerald-400 bg-emerald-500/10 shrink-0"
              >
                v{paperVersion} · {evidenceNodes.length} nodes
              </Badge>
              <span
                className="import-compact-notice text-xs text-muted-foreground/80 truncate hidden md:inline"
                aria-live="polite"
              >
                · {importNotice}
              </span>
            </div>
            <div className="import-compact-actions flex items-center gap-2">
              <Button
                variant="ghost"
                size="sm"
                className="h-6 text-xs text-muted-foreground hover:text-foreground px-2"
                onClick={() => setIsImportExpanded(true)}
              >
                Change Paper
              </Button>
            </div>
          </div>
        ) : (
          <>
            <div className="import-label">
              <Link2 size={16} />
              HTML source
            </div>
            <form className="import-form" onSubmit={handleImport}>
              <Input
                id="arxiv-paper-url"
                name="paper_url"
                aria-label="arXiv HTML paper URL"
                value={paperUrl}
                onChange={(event) => setPaperUrl(event.target.value)}
                className="paper-url-input"
                spellCheck={false}
              />
              <Button
                className="import-button"
                type="submit"
                disabled={isImporting}
              >
                <Plus size={16} />
                {isImporting ? 'Importing…' : 'Import paper'}
              </Button>
              {hasPersistedGraph ? (
                <Button
                  variant="outline"
                  size="sm"
                  type="button"
                  className="h-10 px-3 text-xs shrink-0"
                  onClick={() => setIsImportExpanded(false)}
                >
                  Collapse
                </Button>
              ) : null}
            </form>
            <p className="import-notice" aria-live="polite">
              <CircleDot size={13} />
              {importNotice}
            </p>
          </>
        )}
        </section>
      ) : null}

      <div
        className={`research-grid ${activeViewTab === 'spec' ? 'is-spec-view' : ''} ${activeViewTab === 'compat' ? 'is-compat-view' : ''} ${isLeftCollapsed ? 'is-left-collapsed' : ''} ${isRightCollapsed ? 'is-right-collapsed' : ''}`}
      >
        <aside
          className={`source-panel ${isLeftCollapsed ? 'is-collapsed' : ''}`}
          aria-label="Paper sources"
        >
          <div className="panel-heading">
            <div>
              <p className="eyebrow">Evidence</p>
              <h2>Paper sources</h2>
            </div>
            <div className="panel-heading-actions">
              <Badge variant="outline" className="count-badge">
                01
              </Badge>
              <Button
                type="button"
                variant="ghost"
                size="sm"
                className="panel-collapse-btn"
                onClick={() => setIsLeftCollapsed(true)}
                title="Collapse paper sources"
                aria-label="Collapse paper sources"
              >
                <PanelLeftClose size={15} />
              </Button>
            </div>
          </div>

          <article className="paper-card">
            <div className="paper-index">P–01</div>
            <Badge className="version-badge">
              {hasPersistedGraph
                ? `v${paperVersion} · persisted`
                : 'v7 · curated demo'}
            </Badge>
            <h3>{paperTitle}</h3>
            <p>
              {hasPersistedGraph || imported
                ? `${authorsLabel(paperAuthors)} · arXiv:${paperId}`
                : 'Vaswani et al. · arXiv:1706.03762'}
            </p>
            <div className="paper-meta">
              <span>
                <Braces size={14} />
                {equationCount} equations
              </span>
              <span>
                <History size={14} />
                {hasPersistedGraph
                  ? `${evidenceNodes.length} evidence nodes`
                  : imported
                    ? `${imported.receipt.node_count} evidence nodes`
                    : '6 versions'}
              </span>
            </div>
          </article>

          <div className="section-list mb-4">
            <p className="list-label">
              Multi-paper lineage ({multiPaperCoverage.length} indexed)
            </p>
            <div className="space-y-1.5 px-2">
              {multiPaperCoverage.map((p, idx) => (
                <div
                  key={p.id}
                  className="p-2 rounded border border-border/40 bg-card/50 text-xs space-y-1"
                >
                  <div className="flex items-center justify-between gap-1">
                    <span className="font-semibold text-[11px] truncate">
                      P–0{idx + 1} · arXiv:{p.id}
                    </span>
                    {p.hasHtml ? (
                      <Badge
                        variant="outline"
                        className="text-[9px] py-0 h-4 border-emerald-500/40 text-emerald-600 dark:text-emerald-400 shrink-0"
                      >
                        HTML ({p.eqCount} eq)
                      </Badge>
                    ) : (
                      <Badge
                        variant="outline"
                        className="text-[9px] py-0 h-4 border-amber-500/40 text-amber-600 dark:text-amber-400 bg-amber-500/10 shrink-0 font-medium"
                      >
                        Metadata Only
                      </Badge>
                    )}
                  </div>
                  <p className="text-[11px] text-muted-foreground line-clamp-1">
                    {p.title}
                  </p>
                  {!p.hasHtml ? (
                    <p className="text-[10px] text-muted-foreground/70 italic">
                      arXiv metadata only — equations not extracted
                    </p>
                  ) : null}
                </div>
              ))}
            </div>
          </div>

          <div className="section-list">
            <p className="list-label">Extracted structure</p>
            {paperSections.map((section) => (
              <button
                className={`section-row ${section.active ? 'section-row-active' : ''}`}
                key={section.id}
                type="button"
                onClick={() => {
                  if (!hasPersistedGraph) return;
                  const firstEquation = evidenceNodes.find(
                    (node) =>
                      node.kind === 'Equation' &&
                      node.payload.section_id === section.id,
                  );
                  if (firstEquation) selectGraphNode(firstEquation.uuid);
                  else if (section.graphId) selectGraphNode(section.graphId);
                }}
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
              <p>
                Every extracted fact retains its paper version and HTML anchor.
              </p>
            </div>
          </div>
        </aside>

        <section
          className="graph-panel"
          aria-label="Formula relationship graph"
        >
          <div className="graph-header">
            <div className="graph-header-left">
              {isLeftCollapsed && activeViewTab !== 'spec' ? (
                <Button
                  type="button"
                  variant="outline"
                  size="sm"
                  className="sidebar-expand-btn"
                  onClick={() => setIsLeftCollapsed(false)}
                  title="Expand paper sources"
                  aria-label="Expand paper sources"
                >
                  <PanelLeftOpen size={14} className="mr-1.5" />
                  Sources
                </Button>
              ) : null}
              <div>
                <p className="eyebrow">
                  {activeViewTab === 'spec'
                    ? 'Research setup'
                    : activeViewTab === 'compat'
                      ? 'Candidate validation'
                      : 'Temporal formula graph'}
                </p>
                <h1>
                  {activeViewTab === 'spec'
                    ? 'Start with your research question'
                    : activeViewTab === 'compat'
                      ? 'Check candidate compatibility'
                      : hasPersistedGraph
                        ? equationCount > 0
                          ? 'Exact evidence snapshot'
                          : 'Paper snapshot'
                        : 'Attention lineage'}
                </h1>
              </div>
              <div
                className="flex flex-wrap items-center gap-1 ml-0 sm:ml-4 bg-muted/60 p-1 rounded-xl border border-border/60 max-w-full min-h-9 shadow-inner"
                role="tablist"
                aria-label="Research Workspace Views"
              >
                <button
                  type="button"
                  role="tab"
                  aria-selected={activeViewTab === 'lineage'}
                  className={`px-3 h-7 text-xs font-medium rounded-lg transition-all whitespace-nowrap shrink-0 flex items-center gap-1.5 ${
                    activeViewTab === 'lineage'
                      ? 'bg-background text-foreground shadow-xs font-semibold'
                      : 'text-muted-foreground hover:text-foreground hover:bg-background/40'
                  }`}
                  onClick={() => selectPrimaryView('papers')}
                >
                  <Network size={13} className={activeViewTab === 'lineage' ? 'text-primary' : 'text-muted-foreground'} />
                  <span>Lineage Graph</span>
                  <span className="ml-1 px-1.5 py-0.5 text-[10px] font-mono rounded-full bg-muted text-muted-foreground">
                    {evidenceNodes.length}
                  </span>
                </button>
                <button
                  type="button"
                  role="tab"
                  aria-selected={activeViewTab === 'spec'}
                  className={`px-3 h-7 text-xs font-medium rounded-lg transition-all inline-flex items-center gap-1.5 whitespace-nowrap shrink-0 ${
                    activeViewTab === 'spec'
                      ? 'bg-background text-foreground shadow-xs font-semibold'
                      : 'text-muted-foreground hover:text-foreground hover:bg-background/40'
                  }`}
                  onClick={() => selectPrimaryView('research')}
                >
                  <FileCode2 size={13} className={activeViewTab === 'spec' ? 'text-primary' : 'text-muted-foreground'} />
                  <span>Problem Spec (G1)</span>
                  <span className="ml-1 px-1.5 py-0.5 text-[10px] font-mono rounded-full bg-primary/10 text-primary font-medium">
                    G1
                  </span>
                </button>
                <button
                  type="button"
                  role="tab"
                  aria-selected={activeViewTab === 'compat'}
                  className={`px-3 h-7 text-xs font-medium rounded-lg transition-all inline-flex items-center gap-1.5 whitespace-nowrap shrink-0 ${
                    activeViewTab === 'compat'
                      ? 'bg-background text-foreground shadow-xs font-semibold'
                      : 'text-muted-foreground hover:text-foreground hover:bg-background/40'
                  }`}
                  onClick={() => {
                    setActiveViewTab('compat');
                    setIsLeftCollapsed(false);
                  }}
                >
                  <ShieldCheck size={13} className={activeViewTab === 'compat' ? 'text-primary' : 'text-muted-foreground'} />
                  <span>Compatibility (G3)</span>
                  <span className="ml-1 px-1.5 py-0.5 text-[10px] font-mono rounded-full bg-muted text-muted-foreground">
                    {compatMappings.length}
                  </span>
                </button>
              </div>
            </div>
            <div className="graph-header-right">
              {activeViewTab === 'lineage' ? (
                <div className="legend" aria-label="Graph legend">
                  <span title="Evidence nodes">
                    <i className="legend-dot evidence-dot" />
                    Evidence
                  </span>
                  <span title="Citation edges (dashed line)">
                    <i className="legend-dot concept-dot" />
                    Citation
                  </span>
                  <span title="Derivation edges (solid line)">
                    <i className="legend-dot hypothesis-dot" />
                    Derivation
                  </span>
                </div>
              ) : null}
              {isRightCollapsed &&
              activeViewTab !== 'spec' &&
              activeViewTab !== 'compat' ? (
                <Button
                  type="button"
                  variant="outline"
                  size="sm"
                  className="sidebar-expand-btn"
                  onClick={() => setIsRightCollapsed(false)}
                  title="Expand inspector"
                  aria-label="Expand inspector"
                >
                  <PanelRightOpen size={14} className="mr-1.5" />
                  Inspector
                </Button>
              ) : null}
            </div>
          </div>

          <div
            hidden={activeViewTab !== 'spec'}
            className="flex-1 overflow-y-auto"
          >
            <ProblemSpecPanel />
          </div>
          <div hidden={activeViewTab === 'spec'} className="shrink-0 mb-2">
            <ResearchCreatePanel
              onCreated={() => setResearchRevision((value) => value + 1)}
            />
          </div>
          {activeViewTab === 'lineage' ? (
            <>
              <div className="graph-stage">
                <EvidenceGraphViewport
                  nodes={graphNodes}
                  edges={graphConnections}
                  selectedId={selectedId}
                  onSelect={selectGraphNode}
                  loading={isGraphLoading}
                  hasSnapshot={hasPersistedGraph}
                  isZenMode={isZenMode}
                  onToggleZen={toggleZenMode}
                />
                <output
                  className="graph-status"
                  aria-live="polite"
                  aria-atomic="true"
                >
                  <span className="pulse-dot" />
                  {hasPersistedGraph
                    ? 'Paper snapshot persisted'
                    : 'Version-aware demo'}
                  <span>{graphNotice}</span>
                </output>
              </div>

              <div className="hypothesis-dock shrink-0">
                <div className="hypothesis-icon">
                  <Braces size={18} />
                </div>
                <div>
                  <p>Research move</p>
                  <strong>
                    A candidate is a hypothesis—not a verification result
                  </strong>
                </div>
                <ResearchMovePanel
                  mappings={compatMappings}
                  mappingsState={researchRecordsState}
                  mappingsPartial={researchRecordsPartial}
                />
              </div>
            </>
          ) : activeViewTab === 'spec' ? null : (
            <div className="flex-1 p-6 overflow-y-auto bg-card/20 space-y-4">
              <div className="flex items-center justify-between p-4 rounded-xl border border-border/50 bg-card shadow-xs">
                <div className="space-y-1">
                  <div className="flex items-center gap-2">
                    <h2 className="text-lg font-semibold tracking-tight">
                      Port Compatibility (G3)
                    </h2>
                    <Badge variant="outline" className="text-xs">
                      Versioned policy
                    </Badge>
                  </div>
                  <p className="text-xs text-muted-foreground">
                    Scoped checks for shape, domain, normalization, masks,
                    causality and reviewed symbol bindings.
                  </p>
                </div>
              </div>

              <div className="space-y-3">
                {compatMappings.length === 0 ? (
                  <div className="space-y-4">
                    <output className="block rounded-lg border border-border/60 bg-card p-3 text-sm text-muted-foreground">
                      {researchLoadNotice ===
                        'Research records loaded from the workspace.' ||
                      researchLoadNotice ===
                        'No saved research records in this workspace.'
                        ? 'No saved port mappings in this workspace.'
                        : researchLoadNotice}
                    </output>

                    <div className="rounded-xl border border-border/60 bg-card/60 backdrop-blur-xs p-5 space-y-4 shadow-xs">
                      <div className="space-y-1">
                        <div className="flex items-center gap-2">
                          <ShieldCheck size={18} className="text-primary" />
                          <h3 className="text-sm font-semibold text-foreground">
                            6-Gate Semantic Compatibility Protocol
                          </h3>
                        </div>
                        <p className="text-xs text-muted-foreground leading-relaxed">
                          Automated cross-formula tensor port verification ensures mathematically sound synthesis before generating speculative hypothesis moves.
                        </p>
                      </div>

                      <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-3 gap-2.5">
                        <div className="p-3 rounded-lg border border-border/40 bg-background/50 space-y-1">
                          <div className="flex items-center justify-between">
                            <span className="text-xs font-medium text-foreground">1. Tensor Shape Gate</span>
                            <Badge variant="outline" className="text-[10px] font-mono">Rank & Dim</Badge>
                          </div>
                          <p className="text-[11px] text-muted-foreground">Validates broadcasting rules and dimension alignment across ports.</p>
                        </div>
                        <div className="p-3 rounded-lg border border-border/40 bg-background/50 space-y-1">
                          <div className="flex items-center justify-between">
                            <span className="text-xs font-medium text-foreground">2. Domain Value Gate</span>
                            <Badge variant="outline" className="text-[10px] font-mono">Bounds</Badge>
                          </div>
                          <p className="text-[11px] text-muted-foreground">Ensures producer codomain is a valid subset of consumer domain.</p>
                        </div>
                        <div className="p-3 rounded-lg border border-border/40 bg-background/50 space-y-1">
                          <div className="flex items-center justify-between">
                            <span className="text-xs font-medium text-foreground">3. Normalization Gate</span>
                            <Badge variant="outline" className="text-[10px] font-mono">Scale</Badge>
                          </div>
                          <p className="text-[11px] text-muted-foreground">Checks softmax temperature, layer-norm variance, and scaling factors.</p>
                        </div>
                        <div className="p-3 rounded-lg border border-border/40 bg-background/50 space-y-1">
                          <div className="flex items-center justify-between">
                            <span className="text-xs font-medium text-foreground">4. Attention Mask Gate</span>
                            <Badge variant="outline" className="text-[10px] font-mono">Sparsity</Badge>
                          </div>
                          <p className="text-[11px] text-muted-foreground">Verifies causal masking and padding mask compatibility.</p>
                        </div>
                        <div className="p-3 rounded-lg border border-border/40 bg-background/50 space-y-1">
                          <div className="flex items-center justify-between">
                            <span className="text-xs font-medium text-foreground">5. Causality DAG Gate</span>
                            <Badge variant="outline" className="text-[10px] font-mono">No Cycle</Badge>
                          </div>
                          <p className="text-[11px] text-muted-foreground">Guarantees acyclic temporal ordering across paper derivations.</p>
                        </div>
                        <div className="p-3 rounded-lg border border-border/40 bg-background/50 space-y-1">
                          <div className="flex items-center justify-between">
                            <span className="text-xs font-medium text-foreground">6. Reviewed Binding Gate</span>
                            <Badge variant="outline" className="text-[10px] font-mono">Human-in-Loop</Badge>
                          </div>
                          <p className="text-[11px] text-muted-foreground">Enforces cryptographic sign-off for ambiguous symbol names.</p>
                        </div>
                      </div>

                      <div className="flex items-center justify-between pt-2 border-t border-border/40">
                        <span className="text-xs text-muted-foreground">
                          Ready to assess candidate ports? Select formulas in the Lineage Graph.
                        </span>
                        <Button
                          type="button"
                          variant="outline"
                          size="sm"
                          className="h-8 text-xs gap-1.5"
                          onClick={() => selectPrimaryView('papers')}
                        >
                          <Network size={13} />
                          <span>View Lineage Graph</span>
                          <ArrowRight size={13} />
                        </Button>
                      </div>
                    </div>
                  </div>
                ) : null}
                {compatMappings.map((m) => (
                  <div
                    key={m.mappingId}
                    className="p-4 rounded-xl border border-border/50 bg-card space-y-3 shadow-xs"
                  >
                    {m.freshness === 'stale' ? (
                      <div
                        className="p-2.5 rounded-lg bg-destructive/10 border border-destructive/30 text-destructive text-xs font-medium flex items-center gap-2"
                        role="alert"
                      >
                        <AlertTriangle size={15} />
                        Dependency changed since assessment — re-verification
                        required
                      </div>
                    ) : null}

                    <div className="flex items-center justify-between">
                      <div className="flex items-center gap-2">
                        <span className="font-mono text-xs font-semibold">
                          {m.mappingId}
                        </span>
                        <Badge
                          variant={
                            m.freshness === 'stale'
                              ? 'secondary'
                              : m.status === 'compatible'
                                ? 'default'
                                : m.status === 'incompatible'
                                  ? 'destructive'
                                  : 'secondary'
                          }
                          className={`text-[10px] font-semibold uppercase ${
                            m.freshness === 'current' &&
                            m.status === 'compatible'
                              ? 'bg-emerald-600 text-white'
                              : m.status === 'unknown'
                                ? 'bg-amber-500/20 text-amber-700 dark:text-amber-300 border-amber-500/40'
                                : ''
                          }`}
                        >
                          {m.freshness === 'stale'
                            ? `historical ${m.status}`
                            : m.status}
                        </Badge>
                        <Badge variant="outline" className="text-[10px]">
                          {m.freshness}
                        </Badge>
                        <span className="text-[10px] text-muted-foreground">
                          {m.policyVersion}
                        </span>
                      </div>

                      {m.freshness === 'current' &&
                      m.status === 'unknown' &&
                      !m.isReviewed ? (
                        <Button
                          size="sm"
                          className="h-7 text-xs flex items-center gap-1"
                          disabled={isReviewingCompatId === m.mappingId}
                          onClick={() => {
                            void handleReviewBinding(m.mappingId);
                          }}
                        >
                          <Check size={13} />
                          {isReviewingCompatId === m.mappingId
                            ? 'Reviewing…'
                            : 'Review Binding'}
                        </Button>
                      ) : null}
                    </div>

                    {m.freshness === 'current' &&
                    m.status === 'unknown' &&
                    !m.isReviewed ? (
                      <label className="block text-xs mt-2">
                        Binding review rationale and evidence scope
                        <textarea
                          maxLength={1000}
                          value={reviewNotes[m.mappingId] ?? ''}
                          onChange={(e) =>
                            setReviewNotes((previous) => ({
                              ...previous,
                              [m.mappingId]: e.target.value,
                            }))
                          }
                          className="block w-full border rounded p-2 mt-1"
                        />
                      </label>
                    ) : null}

                    <div className="grid grid-cols-1 md:grid-cols-2 gap-3 text-xs bg-muted/20 p-3 rounded-lg border border-border/40 font-mono">
                      <div>
                        <span className="text-muted-foreground block font-sans text-[11px] mb-1">
                          PRODUCER PORT:
                        </span>
                        <p className="font-semibold text-foreground">
                          {m.producer.equation} · {m.producer.symbol}
                        </p>
                        <p className="text-muted-foreground text-[11px] mt-0.5">
                          Domain: {m.producer.domain} | Shape:{' '}
                          {m.producer.shape}
                        </p>
                      </div>
                      <div>
                        <span className="text-muted-foreground block font-sans text-[11px] mb-1">
                          CONSUMER PORT:
                        </span>
                        <p className="font-semibold text-foreground">
                          {m.consumer.equation} · {m.consumer.symbol}
                        </p>
                        <p className="text-muted-foreground text-[11px] mt-0.5">
                          Domain: {m.consumer.domain} | Shape:{' '}
                          {m.consumer.shape}
                        </p>
                      </div>
                    </div>

                    <div className="flex flex-wrap items-center gap-1.5 pt-1">
                      <span className="text-[11px] text-muted-foreground">
                        Reasons:
                      </span>
                      {m.reasons.length > 0 ? (
                        m.reasons.map((r) => (
                          <Badge
                            key={r}
                            variant="outline"
                            className="text-[10px] font-mono"
                          >
                            {r}
                          </Badge>
                        ))
                      ) : (
                        <span className="text-[11px] text-muted-foreground italic">
                          none
                        </span>
                      )}
                      {m.unresolved.length > 0 ? (
                        <>
                          <span className="text-[11px] text-amber-600 dark:text-amber-400 ml-2">
                            Unresolved:
                          </span>
                          {m.unresolved.map((u) => (
                            <Badge
                              key={u}
                              variant="outline"
                              className="text-[10px] font-mono border-amber-500/40 text-amber-600 dark:text-amber-400"
                            >
                              {u}
                            </Badge>
                          ))}
                        </>
                      ) : null}
                    </div>
                  </div>
                ))}
              </div>
            </div>
          )}
        </section>

        <aside
          className={`inspector-panel ${isRightCollapsed ? 'is-collapsed' : ''}`}
          aria-label="Formula inspector"
        >
          {inspector ? (
            <>
              <div className="panel-heading inspector-heading">
                <div>
                  <p className="eyebrow">Inspector</p>
                  <h2>{inspector.label}</h2>
                </div>
                <div className="panel-heading-actions">
                  <span className={`type-token type-${inspector.tone}`}>
                    {inspector.kind}
                  </span>
                  <Button
                    type="button"
                    variant="ghost"
                    size="sm"
                    className="panel-collapse-btn"
                    onClick={() => setIsRightCollapsed(true)}
                    title="Collapse inspector"
                    aria-label="Collapse inspector"
                  >
                    <PanelRightClose size={15} />
                  </Button>
                </div>
              </div>

              <div className="formula-display">
                <div className="flex items-center justify-between pb-1">
                  <p className="m-0">
                    {hasPersistedGraph
                      ? 'Extracted expression'
                      : 'Canonical expression'}
                  </p>
                  {inspector.expression ? (
                    <Button
                      type="button"
                      variant="ghost"
                      size="sm"
                      className="h-6 px-2 text-[11px] gap-1 text-muted-foreground hover:text-foreground hover:bg-background/80"
                      onClick={() => {
                        navigator.clipboard?.writeText(inspector.expression)
                          .then(() => {
                            setCopiedLatex(true);
                            setTimeout(() => setCopiedLatex(false), 1500);
                          })
                          .catch(() => setCopiedLatex(false));
                      }}
                      title="Copy LaTeX / expression to clipboard"
                    >
                      {copiedLatex ? (
                        <>
                          <Check size={12} className="text-emerald-500" />
                          <span className="text-emerald-500 font-medium">Copied</span>
                        </>
                      ) : (
                        <>
                          <Copy size={12} />
                          <span>Copy LaTeX</span>
                        </>
                      )}
                    </Button>
                  ) : null}
                </div>
                {(() => {
                  const isMath =
                    inspector.kind === 'Equation' || !hasPersistedGraph;
                  const latexHtml = isMath
                    ? renderFormulaHtml(inspector.expression)
                    : null;
                  return latexHtml ? (
                    <div
                      className="inspector-latex-math"
                      dangerouslySetInnerHTML={{ __html: latexHtml }}
                    />
                  ) : (
                    <code>{inspector.expression}</code>
                  );
                })()}
              </div>

              {inspector.formulaAnalysis ? (
                <section className="inspector-section formula-identity">
                  <div className="section-title">
                    <h3>Formula identity</h3>
                    <span>{inspector.formulaAnalysis.status}</span>
                  </div>
                  <code
                    className="canonical-hash"
                    title="Stable canonical SHA-256"
                  >
                    {inspector.formulaAnalysis.canonicalHash}
                  </code>
                  <div
                    className="contract-grid"
                    aria-label="Inferred symbol contracts"
                  >
                    {inspector.formulaAnalysis.contracts.map((contract) => (
                      <div key={contract.name}>
                        <code>{contract.name}</code>
                        <span>{contract.category}</span>
                        <b>{contract.shape}</b>
                        <i
                          title={`${Math.round(contract.inferenceConfidence * 100)}% inference confidence`}
                        >
                          {contract.reviewRequired
                            ? 'review required'
                            : 'inferred'}
                        </i>
                      </div>
                    ))}
                  </div>
                  <p className="formula-readiness">
                    {inspector.formulaAnalysis.requiresReview
                      ? 'The inferred contract needs human review; confidence never counts as confirmation.'
                      : inspector.formulaAnalysis.issueCount > 0
                        ? `${inspector.formulaAnalysis.issueCount} unresolved type or domain obligation(s).`
                        : `Domain ${inspector.formulaAnalysis.domainStatus}; no human confirmation is implied.`}
                  </p>
                </section>
              ) : null}

              <section className="inspector-section">
                <div className="section-title">
                  <h3>Relation</h3>
                  <span title="Extraction confidence is not type confidence, human review, or verification.">
                    {inspector.confidence === null
                      ? 'Extraction confidence not reported'
                      : `Extraction confidence ${Math.round(inspector.confidence * 100)}%`}
                  </span>
                </div>
                <div className="relation-card">
                  <GitBranch size={17} />
                  <div>
                    <p>{inspector.relation}</p>
                    <strong>{inspector.source}</strong>
                  </div>
                </div>
              </section>

              <section className="inspector-section">
                <div className="section-title">
                  <h3>
                    {hasPersistedGraph ? 'Source context' : 'Symbol contract'}
                  </h3>
                  <span>
                    {hasPersistedGraph ? 'immutable snapshot' : '4 symbols'}
                  </span>
                </div>
                <div className="symbol-table">
                  {hasPersistedGraph || selectedSearchHit ? (
                    <>
                      <div>
                        <code>#</code>
                        <span>anchor</span>
                        <b>{inspector.anchor}</b>
                      </div>
                      <div>
                        <code>§</code>
                        <span>section</span>
                        <b>{inspector.section}</b>
                      </div>
                      <div>
                        <code>↳</code>
                        <span>extractor</span>
                        <b>{inspector.extractionMethod}</b>
                      </div>
                      <div>
                        <code>◫</code>
                        <span>episodes</span>
                        <b>{inspector.episodeCount}</b>
                      </div>
                    </>
                  ) : (
                    <>
                      <div>
                        <code>Q</code>
                        <span>query tensor</span>
                        <b>n × dₖ</b>
                      </div>
                      <div>
                        <code>K</code>
                        <span>key tensor</span>
                        <b>m × dₖ</b>
                      </div>
                      <div>
                        <code>V</code>
                        <span>value tensor</span>
                        <b>m × dᵥ</b>
                      </div>
                      <div>
                        <code>dₖ</code>
                        <span>key dimension</span>
                        <b>ℕ⁺</b>
                      </div>
                    </>
                  )}
                </div>
                <p className="source-context-text">{inspector.sourceText}</p>
              </section>

              <section className="inspector-section">
                <div className="section-title">
                  <h3>Relation history</h3>
                  <span>{inspector.relations.length} visible</span>
                </div>
                {inspector.relations.length > 0 ? (
                  <ol className="relation-history">
                    {inspector.relations.map((relation) => (
                      <li key={relation.id}>
                        <span>
                          {relation.direction === 'incoming' ? '←' : '→'}
                        </span>
                        <div>
                          <strong>{relation.relation}</strong>
                          <small>{relation.neighbor}</small>
                        </div>
                        <i>{relation.episodeCount} ep.</i>
                      </li>
                    ))}
                  </ol>
                ) : (
                  <p className="history-empty">
                    No visible relations for this node.
                  </p>
                )}
              </section>

              <section className="inspector-section">
                <div className="section-title">
                  <h3>Provenance episodes</h3>
                  <span>{inspector.paperLabel}</span>
                </div>
                <ul className="episode-list">
                  {inspector.episodeIds.map((episodeId) => (
                    <li key={episodeId}>
                      <code>{episodeId}</code>
                    </li>
                  ))}
                </ul>
              </section>

              <section className="inspector-section">
                <div className="section-title">
                  <h3>Validation</h3>
                  <span>{inspector.verificationStatus}</span>
                </div>
                <ul className="check-list">
                  <li>
                    <Check size={14} />
                    Exact source snapshot retained
                  </li>
                  <li>
                    <Check size={14} />
                    Paper revision pinned · {inspector.paperLabel}
                  </li>
                  <li>
                    <Check size={14} />
                    {!inspector.anchorIsSource
                      ? 'Generated anchor marked for review'
                      : 'Source anchor resolved'}
                  </li>
                  <li>
                    <Check size={14} />
                    {inspector.superseded
                      ? 'Superseded by a newer revision'
                      : 'No newer revision in view'}
                  </li>
                </ul>
              </section>

              <a
                className="source-link"
                href={inspector.sourceHref}
                target="_blank"
                rel="noopener noreferrer"
              >
                <BookOpenText size={16} />
                {inspector.anchorIsSource
                  ? 'Open evidence in source'
                  : 'Open paper source'}
                <span>↗</span>
              </a>
            </>
          ) : (
            <>
              <div className="panel-heading inspector-heading">
                <div>
                  <p className="eyebrow">Inspector</p>
                  <h2>Abstract</h2>
                </div>
                <div className="panel-heading-actions">
                  <Button
                    type="button"
                    variant="ghost"
                    size="sm"
                    className="panel-collapse-btn"
                    onClick={() => setIsRightCollapsed(true)}
                    title="Collapse inspector"
                    aria-label="Collapse inspector"
                  >
                    <PanelRightClose size={15} />
                  </Button>
                </div>
              </div>
              <div className="inspector-empty">
                <Braces size={22} />
                <strong>No formula selected</strong>
                <span>
                  Import a paper containing display equations to inspect one.
                </span>
              </div>
            </>
          )}
          {activeViewTab === 'lineage' ? (
            <section className="inspector-section lineage-evidence-drawer">
              <div className="section-title">
                <h3>Lineage & Derivations</h3>
                <Badge variant="outline" className="text-[10px]">
                  Multi-paper Evidence
                </Badge>
              </div>
              <div className="space-y-2 mt-2">
                {lineageRelations.map((edge) => (
                  <div
                    key={edge.id}
                    className={`p-2.5 rounded-lg border text-xs transition-colors ${
                      selectedLineageId === edge.id
                        ? 'border-primary/60 bg-primary/5'
                        : 'border-border/40 hover:bg-muted/20'
                    }`}
                  >
                    <button
                      type="button"
                      className="w-full text-left focus:outline-hidden"
                      onClick={() => setSelectedLineageId(edge.id)}
                    >
                      <div className="flex items-center justify-between font-medium">
                        <span className="capitalize">
                          {edge.relationType.replaceAll('_', ' ')}
                        </span>
                        <Badge
                          variant={
                            edge.status === 'reviewed'
                              ? 'default'
                              : edge.status === 'rejected'
                                ? 'destructive'
                                : 'secondary'
                          }
                          className={`text-[9px] py-0 h-4 ${
                            edge.status === 'reviewed'
                              ? 'bg-emerald-600 text-white'
                              : ''
                          }`}
                        >
                          {edge.status}
                        </Badge>
                      </div>
                      <p className="text-[11px] text-muted-foreground mt-0.5 font-mono">
                        {edge.source} &rarr; {edge.target}
                      </p>
                    </button>
                    {selectedLineageId === edge.id ? (
                      <div className="mt-2 pt-2 border-t border-border/40 space-y-2 text-[11px]">
                        <p className="italic text-muted-foreground bg-muted/30 p-2 rounded">
                          Assertion rationale: {edge.evidence.quote}
                        </p>
                        <div className="text-[10px] text-muted-foreground space-y-0.5">
                          <p>
                            <strong>Source Paper:</strong> {edge.evidence.paper}
                          </p>
                          <p>
                            <strong>Section Anchor:</strong>{' '}
                            {edge.evidence.section}
                          </p>
                        </div>
                        <LineageSourceRefs refs={edge.evidence.refs} />
                        {edge.status !== 'reviewed' ? (
                          <div className="space-y-2 pt-1">
                            <label className="block">
                              Source review rationale and scope
                              <textarea
                                maxLength={1000}
                                value={reviewNotes[edge.id] ?? ''}
                                onChange={(e) =>
                                  setReviewNotes((previous) => ({
                                    ...previous,
                                    [edge.id]: e.target.value,
                                  }))
                                }
                                className="block w-full border rounded p-2 mt-1"
                              />
                            </label>
                            <div className="flex items-center gap-1.5">
                              <Button
                                size="sm"
                                className="h-6 text-[11px] px-2 flex items-center gap-1"
                                disabled={isReviewingLineageId === edge.id}
                                onClick={(e) => {
                                  e.stopPropagation();
                                  void handleReviewLineage(edge.id, 'reviewed');
                                }}
                              >
                                <Check size={11} />
                                {isReviewingLineageId === edge.id
                                  ? 'Reviewing…'
                                  : 'Review & Approve'}
                              </Button>
                              <Button
                                size="sm"
                                variant="outline"
                                className="h-6 text-[11px] px-2 text-destructive border-destructive/30 hover:bg-destructive/10"
                                disabled={isReviewingLineageId === edge.id}
                                onClick={(e) => {
                                  e.stopPropagation();
                                  void handleReviewLineage(edge.id, 'rejected');
                                }}
                              >
                                Reject
                              </Button>
                            </div>
                          </div>
                        ) : null}
                      </div>
                    ) : null}
                  </div>
                ))}
              </div>
            </section>
          ) : null}
        </aside>
      </div>
    </main>
  );
}
