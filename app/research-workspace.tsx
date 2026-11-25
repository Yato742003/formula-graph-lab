'use client';

import Link from 'next/link';
import ProblemSpecPanel from './problem-spec-panel';
import ResearchCreatePanel from './research-create-panel';
import ResearchMovePanel from './research-move-panel';
import StageNav, {
  WorkbenchLeftSpine,
  useSampleWalkthrough,
  useWorkbenchStageShortcuts,
  useWorkspaceLocation,
  withContext,
} from '@/components/workspace-shell';

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
  Activity,
  AlertTriangle,
  ArrowRight,
  BookOpenText,
  Braces,
  Check,
  ChevronRight,
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
  Pencil,
  Plus,
  Search,
  ShieldCheck,
  Trash2,
  WandSparkles,
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
  stage?: 'graph' | 'spec' | 'compatibility';
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
    case 'INVALID_PAPER_ID':
    case 'INVALID_PAPER_VERSION':
      return 'Invalid arXiv paper identifier or version';
    case 'PAPER_NOT_FOUND':
      return 'This paper has not been imported into your workspace yet';
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

export default function ResearchWorkspace({ user, stage }: ResearchWorkspaceProps) {
  const { params: workspaceParams } = useWorkspaceLocation();
  const { walkthroughEnabled, setWalkthrough } = useSampleWalkthrough();
  useWorkbenchStageShortcuts(workspaceParams);
  const helpHref = withContext('/help', workspaceParams);
  const opsHref = withContext('/ops', workspaceParams);
  const [selectedId, setSelectedId] = useState<string | null>('scaled');
  const [isImporting, setIsImporting] = useState(false);
  const [importPhaseIdx, setImportPhaseIdx] = useState(0);
  const [imported, setImported] = useState<WorkspaceImportResponse | null>(
    null,
  );
  const [graphSnapshot, setGraphSnapshot] =
    useState<EvidenceGraphSnapshotResponse | null>(null);
  const [isGraphLoading, setIsGraphLoading] = useState(false);
  const [graphNotice, setGraphNotice] = useState('Loading saved evidence…');
  const [selectedSearchHit, setSelectedSearchHit] =
    useState<EvidenceSearchHit | null>(null);
  const [localTex, setLocalTex] = useState(false);
  const [texSource, setTexSource] = useState('');
  const [texFilename, setTexFilename] = useState('');
  const [texTitle, setTexTitle] = useState('');
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
  const researchMoveRef = useRef<HTMLDivElement | null>(null);

  useEffect(() => {
    if (!isSearchOpen) return;
    const previousFocus = document.activeElement as HTMLElement | null;
    const dialog = document.getElementById('graph-search-panel') as HTMLDialogElement;
    dialog.showModal();
    document.getElementById('search-evidence-query')?.focus();
    return () => {
      dialog.close();
      previousFocus?.focus();
    };
  }, [isSearchOpen]);

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
      } else if (e.key === '[' && !e.metaKey && !e.ctrlKey && !e.altKey) {
        e.preventDefault();
        setIsLeftCollapsed((prev) => !prev);
      } else if (e.key === ']' && !e.metaKey && !e.ctrlKey && !e.altKey) {
        e.preventDefault();
        setIsRightCollapsed((prev) => !prev);
      }
    };
    window.addEventListener('keydown', handleKeyDown);
    return () => window.removeEventListener('keydown', handleKeyDown);
  }, [toggleZenMode, isSearchOpen, selectedId]);

  useEffect(() => {
    if (!isImporting) return;
    const timer = window.setInterval(() => {
      setImportPhaseIdx((idx) => (idx < 3 ? idx + 1 : idx));
    }, 900);
    return () => window.clearInterval(timer);
  }, [isImporting]);

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
  >(
    stage === 'graph' ? 'lineage' : stage === 'compatibility' ? 'compat' : 'spec',
  );

  function selectPrimaryView(view: 'papers' | 'research') {
    if (view === 'research') {
      setActiveViewTab('spec');
      setIsImportExpanded(false);
      return;
    }

    setActiveViewTab('lineage');
    setIsImportExpanded(false);
  }

  function selectCompatibilityView() {
    setActiveViewTab('compat');
    setIsLeftCollapsed(false);
    setIsImportExpanded(false);
  }

  const focusResearchMove = useCallback(() => {
    setActiveViewTab('lineage');
    setIsLeftCollapsed(false);
    setIsImportExpanded(false);
    window.requestAnimationFrame(() => {
      window.requestAnimationFrame(() => {
        researchMoveRef.current?.scrollIntoView?.({
          behavior: 'smooth',
          block: 'nearest',
        });
      });
    });
  }, []);

  const [copiedLatex, setCopiedLatex] = useState(false);

  const [compatMappings, setCompatMappings] = useState<CompatibilityView[]>([]);
  const [lineageRelations, setLineageRelations] = useState<LineageView[]>([]);
  const [multiPaperCoverage, setMultiPaperCoverage] = useState<CoverageView[]>(
    [],
  );
  const [lineageFilter, setLineageFilter] = useState('');
  const [hiddenPaperIds, setHiddenPaperIds] = useState<Set<string>>(new Set());
  const [paperTitleOverrides, setPaperTitleOverrides] = useState<Map<string, string>>(new Map());
  const [editingPaperId, setEditingPaperId] = useState<string | null>(null);
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
  const filteredCoverage = useMemo(() => {
    const query = lineageFilter.trim().toLowerCase();
    const visible = multiPaperCoverage
      .filter((paper) => !hiddenPaperIds.has(paper.id))
      .map((paper) => {
        const override = paperTitleOverrides.get(paper.id);
        return override ? { ...paper, title: override } : paper;
      });
    if (!query) return visible;
    return visible.filter((paper) =>
      `${paper.id} ${paper.title} ${paper.authors}`.toLowerCase().includes(query),
    );
  }, [lineageFilter, multiPaperCoverage, hiddenPaperIds, paperTitleOverrides]);
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
      targetPaper?: { id: string; version?: number },
    ): Promise<EvidenceGraphSnapshotResponse | undefined> => {
      await Promise.resolve();
      setIsGraphLoading(true);
      setGraphNotice('Loading saved evidence…');
      try {
        const searchParams = new URLSearchParams();
        if (targetPaper) {
          searchParams.set('paper_id', targetPaper.id);
          if (targetPaper.version !== undefined) {
            searchParams.set('version', String(targetPaper.version));
          }
        }
        const query = targetPaper ? `?${searchParams.toString()}` : '';
        const response = await fetch(`/api/graph${query}`, {
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

  const switchActivePaper = useCallback(
    async (paperItem: CoverageView) => {
      if (!paperItem.hasHtml) {
        setNotice(
          `arXiv:${paperItem.id} has metadata only — import its arXiv HTML source to inspect its graph.`,
        );
        return;
      }
      const match = paperItem.id.match(/^(.+?)v(\d+)$/);
      const rawId = match ? match[1] : paperItem.id;
      const rawVer = match ? parseInt(match[2], 10) : undefined;
      setNotice(`Switching to paper ${rawId}…`);
      const completed = await loadPersistedGraph(undefined, {
        id: rawId,
        version: rawVer,
      });
      if (completed?.paper) {
        setPaperUrl(completed.paper.source_url);
        setNotice(`Active paper: ${completed.paper.title} (arXiv:${completed.paper.paper_id})`);
      } else {
        setNotice(
          `Could not switch to arXiv:${rawId}: no imported graph snapshot is available in this workspace.`,
        );
      }
    },
    [loadPersistedGraph],
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
      if (localTex && new TextEncoder().encode(texSource).length > 512 * 1024) {
        setNotice('LaTeX source exceeds 512 KiB.');
        return;
      }
      const payload = localTex
        ? { tex_source: texSource, filename: texFilename || undefined, title: texTitle || undefined }
        : { url: normalizeArxivHtmlUrl(paperUrl) };
      setIsImporting(true);
      setNotice('Extracting and persisting source-bound evidence…');
      const response = await fetch(localTex ? '/api/imports/tex' : '/api/imports', {
        method: 'POST',
        headers: { 'content-type': 'application/json' },
        body: JSON.stringify(payload),
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
      const refreshedGraph = await loadPersistedGraph(undefined, localTex ? {
        id: completed.paper.paper_id, version: completed.paper.version ?? undefined,
      } : undefined);
      setNotice(
        `${completed.paper.equations.length} equations ${
          completed.receipt.replayed ? 'already synchronized' : 'persisted'
        } · ${completed.paper.title}${refreshedGraph ? '' : ' · graph refresh pending'}`,
      );
    } catch (error) {
      setNotice(
        error instanceof PaperUrlError
          ? error.message
          : 'Could not complete the import.',
      );
    } finally {
      setIsImporting(false);
      setImportPhaseIdx(0);
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

  const importPhases = [
    localTex ? '1. Reading LaTeX source' : '1. Fetching arXiv HTML',
    '2. Extracting MathML & LaTeX',
    '3. Checking AST & dimensions',
    '4. Saving SHA-256 snapshot',
  ] as const;

  const searchDialogNode = isSearchOpen ? (
    <dialog
      className="search-command"
      id="graph-search-panel"
      aria-label="Search evidence graph"
      aria-modal="true"
      onCancel={() => setIsSearchOpen(false)}
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
  ) : null;

  const importStripNode =
    activeViewTab === 'lineage' ? (
      <section
        id="paper-import"
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
                {paperId.startsWith('tex_') ? 'local .tex · ' : 'arXiv:'}{paperId} &mdash; {paperTitle}
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
              <fieldset aria-label="Paper source">
                <button type="button" aria-pressed={!localTex} onClick={() => setLocalTex(false)}>arXiv HTML</button>
                <button type="button" aria-pressed={localTex} onClick={() => setLocalTex(true)}>.tex / LaTeX</button>
              </fieldset>
            </div>
            <form className="import-form" onSubmit={handleImport}>
              {localTex ? <div className="tex-import-fields">
                <input type="file" accept=".tex,.txt" aria-label="Upload LaTeX source" onChange={async event => {
                  const file = event.target.files?.[0];
                  if (!file) return;
                  if (file.size > 512 * 1024) { setNotice('LaTeX source exceeds 512 KiB.'); return; }
                  try {
                    setTexSource(await file.text());
                    setTexFilename(file.name);
                  } catch { setNotice('Could not read the selected file.'); }
                }} />
                <textarea aria-label="Paste LaTeX source" required value={texSource}
                  onChange={event => { setTexSource(event.target.value); setTexFilename(''); }} />
                <Input aria-label="Optional document title" placeholder="Document title (optional)"
                  maxLength={2000} value={texTitle} onChange={event => setTexTitle(event.target.value)} />
              </div> : <Input
                id="arxiv-paper-url"
                name="paper_url"
                aria-label="arXiv HTML paper URL"
                value={paperUrl}
                onChange={(event) => setPaperUrl(event.target.value)}
                className="paper-url-input"
                spellCheck={false}
              />}
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
            {isImporting ? (
              <ol className="wb-import-stepper" aria-live="polite">
                {importPhases.map((phase, idx) => (
                  <li
                    key={phase}
                    className={
                      idx < importPhaseIdx
                        ? 'is-done'
                        : idx === importPhaseIdx
                          ? 'is-active'
                          : ''
                    }
                  >
                    <span className="wb-status-dot" aria-hidden="true" />
                    <span>{phase}</span>
                  </li>
                ))}
              </ol>
            ) : (
              <p className="import-notice" aria-live="polite">
                <CircleDot size={13} />
                {importNotice}
              </p>
            )}
          </>
        )}
      </section>
    ) : null;

  const paperSourcesBody = (
    <>

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
                ? `${authorsLabel(paperAuthors)} · ${paperId.startsWith('tex_') ? 'local .tex · ' : 'arXiv:'}${paperId}`
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

          <details
            key={multiPaperCoverage.length > 0 ? 'lineage-populated' : 'lineage-empty'}
            className="source-section-disclosure mb-4"
            open={multiPaperCoverage.length > 0 || undefined}
          >
            <summary className="list-label">
              Multi-paper lineage ({multiPaperCoverage.length} indexed)
              <span className="source-disclosure-hint">Filter</span>
            </summary>
            <div className="source-section-body">
              <Input
                id="source-lineage-filter"
                name="lineage_filter"
                aria-label="Filter multi-paper lineage"
                value={lineageFilter}
                onChange={(event) => setLineageFilter(event.target.value)}
                placeholder="Filter papers"
                className="source-lineage-filter"
              />
            <div className="space-y-1.5">
              {filteredCoverage.map((p, idx) => {
                const isCurrentActive = graphSnapshot?.paper
                  ? `${graphSnapshot.paper.paper_id}v${graphSnapshot.paper.version}` === p.id
                  : paperId === p.id.replace(/v\d+$/, '');
                const displayTitle = paperTitleOverrides.get(p.id) ?? p.title;

                return (
                  <div
                    key={p.id}
                    className={`p-2.5 rounded-lg border text-xs space-y-1.5 transition-all ${
                      isCurrentActive
                        ? 'border-primary/80 bg-primary/5 shadow-xs ring-1 ring-primary/20'
                        : 'border-border/60 bg-card/60 hover:bg-muted/30'
                    }`}
                  >
                    {/* Row 1: Paper Number / ID & Eq Count Badge */}
                    <div className="flex items-center justify-between gap-1">
                      <span className="font-semibold text-[11px] font-mono text-foreground truncate">
                        P–0{idx + 1} · {p.id.startsWith('tex_') ? 'local .tex · ' : 'arXiv:'}{p.id}
                      </span>
                      {p.hasHtml ? (
                        <Badge
                          variant="outline"
                          className="text-[9px] py-0 h-4 border-emerald-500/40 text-emerald-600 dark:text-emerald-400 bg-emerald-500/10 shrink-0 font-medium"
                        >
                          {p.id.startsWith('tex_') ? 'local .tex' : 'HTML'} ({p.eqCount} eq)
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

                    {/* Row 2: Title or Inline Rename Input */}
                    {editingPaperId === p.id ? (
                      <div className="space-y-1">
                        <input
                          id={`rename-paper-${p.id}`}
                          name="paper_display_title"
                          aria-label={`Rename ${p.id}`}
                          className="w-full text-xs px-2 py-1 rounded border border-primary bg-background text-foreground outline-none shadow-xs"
                          defaultValue={displayTitle}
                          onClick={(e) => e.stopPropagation()}
                          onKeyDown={(e) => {
                            if (e.key === 'Enter') {
                              const val = (e.target as HTMLInputElement).value.trim();
                              if (val && val !== p.title) {
                                setPaperTitleOverrides((prev) => new Map(prev).set(p.id, val));
                              }
                              setEditingPaperId(null);
                            } else if (e.key === 'Escape') {
                              setEditingPaperId(null);
                            }
                          }}
                          onBlur={(e) => {
                            const val = e.target.value.trim();
                            if (val && val !== p.title) {
                              setPaperTitleOverrides((prev) => new Map(prev).set(p.id, val));
                            }
                            setEditingPaperId(null);
                          }}
                        />
                        <p className="text-[10px] text-muted-foreground">Press Enter to save, Esc to cancel</p>
                      </div>
                    ) : (
                      <p className="text-[11px] text-muted-foreground line-clamp-2 leading-snug" title={displayTitle}>
                        {displayTitle}
                      </p>
                    )}

                    {!p.hasHtml ? (
                      <p className="text-[10px] text-muted-foreground/70 italic">
                        arXiv metadata only — equations not extracted. Import HTML to open its graph.
                      </p>
                    ) : null}

                    {/* Row 3: Action Toolbar (Select / Active, Rename, Hide) */}
                    <div className="flex items-center justify-between gap-1 pt-1 border-t border-border/40 text-[11px]">
                      {isCurrentActive ? (
                        <span className="inline-flex items-center gap-1 font-semibold text-[10px] text-emerald-600 dark:text-emerald-400 bg-emerald-500/10 px-1.5 py-0.5 rounded border border-emerald-500/20">
                          <Check size={11} /> Active
                        </span>
                      ) : (
                        <button
                          type="button"
                          disabled={!p.hasHtml}
                          className="inline-flex items-center gap-1 text-[10px] font-semibold px-2 py-0.5 rounded bg-primary/10 text-primary hover:bg-primary/20 transition-colors disabled:opacity-50 disabled:cursor-not-allowed disabled:hover:bg-primary/10"
                          onClick={() => void switchActivePaper(p)}
                          title={
                            p.hasHtml
                              ? `Load ${p.id} into the graph workspace`
                              : `${p.id} has metadata only — import HTML to open its graph`
                          }
                        >
                          Open graph
                        </button>
                      )}

                      <div className="flex items-center gap-1">
                        <button
                          type="button"
                          className="inline-flex items-center gap-0.5 px-1.5 py-0.5 rounded text-[10px] text-muted-foreground hover:text-foreground hover:bg-muted/60 transition-colors border border-border/40"
                          title="Rename display label for this session"
                          onClick={() => setEditingPaperId(editingPaperId === p.id ? null : p.id)}
                        >
                          <Pencil size={10} />
                          <span>Rename</span>
                        </button>
                        <button
                          type="button"
                          className="inline-flex items-center gap-0.5 px-1.5 py-0.5 rounded text-[10px] text-muted-foreground hover:text-destructive hover:bg-destructive/10 transition-colors border border-border/40"
                          title="Hide paper from this list"
                          onClick={() => setHiddenPaperIds((prev) => new Set(prev).add(p.id))}
                        >
                          <Trash2 size={10} />
                          <span>Hide</span>
                        </button>
                      </div>
                    </div>
                  </div>
                );
              })}
              {!filteredCoverage.length ? (
                <p className="source-disclosure-empty">No paper matches this filter.</p>
              ) : null}
            </div>
            </div>
          </details>

          <details className="source-section-disclosure">
            <summary className="list-label">
              Extracted structure
              <span className="source-disclosure-hint">{paperSections.length}</span>
            </summary>
            <div className="source-section-body">
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
          </details>

          <div className="provenance-note" title="Every extracted fact retains its paper version and HTML anchor.">
            <ShieldCheck size={14} className="text-primary shrink-0" />
            <p>Source-bound provenance verified</p>
          </div>
    </>
  );

  const graphPanelNode = (
        <section
          className="graph-panel"
          aria-label="Formula relationship graph"
        >
          <div className="graph-header">
            <div className="graph-header-left">
              {!stage && isLeftCollapsed && activeViewTab !== 'spec' ? (
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
                    ? 'Step 02 of 05 · Research setup'
                    : activeViewTab === 'compat'
                      ? 'Step 03 of 05 · Symbol compatibility'
                      : 'Step 01 of 05 · Temporal formula graph'}
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
              {!stage ? <div
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
                  onClick={selectCompatibilityView}
                >
                  <ShieldCheck size={13} className={activeViewTab === 'compat' ? 'text-primary' : 'text-muted-foreground'} />
                  <span>Compatibility (G3)</span>
                  <span className="ml-1 px-1.5 py-0.5 text-[10px] font-mono rounded-full bg-muted text-muted-foreground">
                    {compatMappings.length}
                  </span>
                </button>
              </div> : null}
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
              {!stage && isRightCollapsed &&
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

          {!stage ? <nav className="workspace-flow" aria-label="Research workflow">
            <div className="workspace-flow-status" aria-live="polite">
              <span className="eyebrow">Current stage</span>
              <strong>
                {activeViewTab === 'spec'
                  ? '01 · Scope'
                  : activeViewTab === 'compat'
                    ? '03 · Verify'
                    : '02 · Trace'}
              </strong>
              <small>
                {activeViewTab === 'spec'
                  ? 'G1'
                  : activeViewTab === 'compat'
                    ? 'G3'
                    : 'G2'}
              </small>
            </div>
            {!stage ? <button
              type="button"
              className="workspace-flow-action"
              aria-label="Open research move"
              onClick={focusResearchMove}
            >
              <Braces size={14} aria-hidden="true" />
              <span className="workspace-flow-action-copy">
                <strong>Open move</strong>
                <small>Hypothesis only</small>
              </span>
              <ArrowRight size={14} aria-hidden="true" />
            </button> : null}
          </nav> : null}

          <div
            hidden={activeViewTab !== 'spec'}
            className="flex-1 overflow-y-auto"
          >
            <ProblemSpecPanel showEvolution={stage !== 'spec'} />
          </div>
          <div hidden={activeViewTab === 'spec'} className="shrink-0 mb-2">
            <ResearchCreatePanel
              key={activeViewTab === 'compat' ? 'compat' : 'lineage'}
              id={activeViewTab === 'compat' ? 'compatibility-add-evidence' : 'graph-add-evidence'}
              defaultMode={activeViewTab === 'compat' ? 'compatibility' : 'lineage'}
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

              {!stage ? <div className="hypothesis-dock shrink-0" ref={researchMoveRef}>
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
              </div> : null}
            </>
          ) : activeViewTab === 'spec' ? null : (
            <div className="flex-1 p-5 overflow-y-auto bg-card/20 space-y-4">
              <div className="p-4 rounded-xl border border-border/60 bg-card shadow-xs space-y-3">
                <div className="flex flex-wrap items-center justify-between gap-2">
                  <div className="space-y-0.5">
                    <div className="flex items-center gap-2">
                      <h2 className="text-base font-semibold tracking-tight">
                        Port Compatibility (G3)
                      </h2>
                      <Badge variant="outline" className="text-[11px]">
                        Versioned policy
                      </Badge>
                    </div>
                    <p className="text-xs text-muted-foreground">
                      Scoped checks for shape, domain, normalization, masks,
                      causality and reviewed symbol bindings.
                    </p>
                  </div>
                  <button
                    type="button"
                    className="inline-flex items-center gap-1.5 h-8 px-3 rounded-lg border border-primary/40 bg-primary/8 text-primary hover:bg-primary/15 text-xs font-semibold transition-colors cursor-pointer"
                    onClick={() => {
                      const panel = document.getElementById('compatibility-add-evidence') as HTMLDetailsElement | null;
                      if (panel) {
                        panel.open = true;
                        panel.scrollIntoView({ behavior: 'smooth', block: 'nearest' });
                        document.getElementById('create-record-type')?.focus();
                      }
                    }}
                  >
                    <Plus size={13} aria-hidden="true" />
                    <span>Map two formula symbols</span>
                  </button>
                </div>
                <ol className="grid gap-2 sm:grid-cols-3 pt-2 border-t border-border/40 text-[11px] text-muted-foreground">
                  <li className="rounded-lg bg-muted/30 px-2.5 py-1.5">
                    <strong className="text-foreground">1. Pick two symbols</strong> — Open Add evidence above and choose a producer &amp; consumer formula.
                  </li>
                  <li className="rounded-lg bg-muted/30 px-2.5 py-1.5">
                    <strong className="text-foreground">2. Automatic 6-gate check</strong> — Server checks shape, domain, normalization, masks, causality, and binding.
                  </li>
                  <li className="rounded-lg bg-muted/30 px-2.5 py-1.5">
                    <strong className="text-foreground">3. Confirm binding</strong> — Record a review rationale to unlock Step 4 (Proposals).
                  </li>
                </ol>
              </div>

              <div className="space-y-3">
                {compatMappings.length === 0 ? (
                  <div className="space-y-4">
                    {researchRecordsState === 'loading' ? (
                      <output className="block rounded-lg border border-border/60 bg-card p-3 text-sm text-muted-foreground">
                        Loading research records…
                      </output>
                    ) : researchRecordsState === 'unavailable' ? (
                      <output role="alert" className="block rounded-lg border border-destructive/30 bg-destructive/5 p-3 text-sm text-destructive">
                        {researchLoadNotice}
                      </output>
                    ) : (
                      <>
                        <output className="block rounded-lg border border-border/60 bg-card p-3 text-sm text-muted-foreground">
                          {researchLoadNotice ===
                            'Research records loaded from the workspace.' ||
                          researchLoadNotice ===
                            'No saved research records in this workspace.'
                            ? 'No saved port mappings in this workspace.'
                            : researchLoadNotice}
                        </output>

                        <div className="compatibility-empty-card rounded-xl border border-border/60 bg-card/60 p-4 space-y-4 shadow-xs">
                          <div className="flex items-start justify-between gap-3">
                            <div className="flex items-center gap-2">
                              <ShieldCheck size={18} className="text-primary" />
                              <div>
                                <h3 className="text-sm font-semibold text-foreground">No mapping yet</h3>
                                <p className="text-xs text-muted-foreground">Not run · gates need a reviewed mapping.</p>
                              </div>
                            </div>
                            <Badge variant="outline" className="text-[10px]">G3</Badge>
                          </div>
                          <div className="compatibility-gate-chips" aria-label="Compatibility gates">
                            {['Shape', 'Domain', 'Normalization', 'Mask', 'Causality', 'Binding'].map((gate) => (
                              <span key={gate} title={`${gate}: not run`}>{gate}</span>
                            ))}
                          </div>

                          <div className="flex items-center justify-between gap-3 pt-2 border-t border-border/40">
                            <span className="text-xs text-muted-foreground">Select a formula pair to add a mapping.</span>
                            {stage ? (
                              <Link
                                href={withContext('/graph', workspaceParams)}
                                className="inline-flex items-center h-8 px-3 rounded-md border border-border/60 bg-background hover:bg-muted text-xs font-medium gap-1.5 transition-colors"
                              >
                                <Network size={13} />
                                <span>Open Graph</span>
                                <ArrowRight size={13} />
                              </Link>
                            ) : (
                              <Button
                                type="button"
                                variant="outline"
                                size="sm"
                                className="h-8 text-xs gap-1.5"
                                onClick={() => selectPrimaryView('papers')}
                              >
                                <Network size={13} />
                                <span>Open Graph</span>
                                <ArrowRight size={13} />
                              </Button>
                            )}
                          </div>
                        </div>
                      </>
                    )}
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
                      <label htmlFor={`binding-review-note-${m.mappingId}`} className="block text-xs mt-2">
                        Binding review rationale and evidence scope
                        <textarea
                          id={`binding-review-note-${m.mappingId}`}
                          name="binding_review_rationale"
                          maxLength={1000}
                          placeholder="Explain why these two reviewed ports match in shape, domain, and causality…"
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

                    {m.status === 'compatible' ? (
                      <div className="flex items-center justify-between pt-2 border-t border-border/40">
                        <span className="text-xs text-emerald-600 dark:text-emerald-400 font-medium flex items-center gap-1.5">
                          <Check size={14} /> Approved for candidate compilation
                        </span>
                        <Link
                          href={withContext('/proposals', workspaceParams)}
                          className="inline-flex items-center gap-1.5 px-3 py-1.5 rounded-lg bg-primary text-primary-foreground text-xs font-semibold hover:bg-primary/90 transition-colors shadow-xs"
                        >
                          <span>Proceed to Step 4: Proposals</span>
                          <ArrowRight size={13} />
                        </Link>
                      </div>
                    ) : null}
                  </div>
                ))}
              </div>
            </div>
          )}
        </section>
  );

  const formulaInspectorInner = (
    <>
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
                <div className="flex items-center gap-1.5 pt-2 border-t border-border/40 mt-2.5">
                  {stage ? (
                    <>
                      <Link
                        href={withContext('/compatibility', workspaceParams)}
                        className="inline-flex items-center justify-center h-7 px-2 rounded-md border border-border/60 bg-background hover:bg-muted text-xs gap-1.5 flex-1 font-medium hover:border-primary/50 transition-colors whitespace-nowrap min-w-0"
                        title="Open compatibility; select and review a mapping there"
                      >
                        <ShieldCheck size={13} className="text-primary shrink-0" />
                        <span className="truncate">Port Check</span>
                      </Link>
                      <Link
                        href={withContext('/spec', workspaceParams)}
                        className="inline-flex items-center justify-center h-7 px-2 rounded-md border border-border/60 bg-background hover:bg-muted text-xs gap-1.5 flex-1 font-medium hover:border-primary/50 transition-colors whitespace-nowrap min-w-0"
                        title="Open the research question and experiment manifest"
                      >
                        <FileCode2 size={13} className="text-primary shrink-0" />
                        <span className="truncate">Spec</span>
                      </Link>
                    </>
                  ) : (
                    <>
                      <Button
                        type="button"
                        variant="outline"
                        size="sm"
                        className="h-7 px-2 text-xs gap-1.5 flex-1 font-medium hover:border-primary/50 transition-colors whitespace-nowrap min-w-0"
                        onClick={() => {
                          selectCompatibilityView();
                        }}
                        title="Open compatibility; select and review a mapping there"
                      >
                        <ShieldCheck size={13} className="text-primary shrink-0" />
                        <span className="truncate">Port Check</span>
                      </Button>
                      <Button
                        type="button"
                        variant="outline"
                        size="sm"
                        className="h-7 px-2 text-xs gap-1.5 flex-1 font-medium hover:border-primary/50 transition-colors whitespace-nowrap min-w-0"
                        onClick={() => {
                          selectPrimaryView('research');
                        }}
                        title="Open the research question and experiment manifest"
                      >
                        <FileCode2 size={13} className="text-primary shrink-0" />
                        <span className="truncate">Spec</span>
                      </Button>
                    </>
                  )}
                </div>
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
                {inspector.sourceText ? (
                  <p className="source-context-text">{inspector.sourceText}</p>
                ) : null}
              </section>

              <details className="inspector-section inspector-disclosure">
                <summary className="section-title">
                  <span>Relation history</span>
                  <span>{inspector.relations.length} visible</span>
                </summary>
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
              </details>

              <details className="inspector-section inspector-disclosure">
                <summary className="section-title">
                  <span>Provenance episodes</span>
                  <span>{inspector.paperLabel}</span>
                </summary>
                <ul className="episode-list">
                  {inspector.episodeIds.map((episodeId) => (
                    <li key={episodeId}>
                      <code>{episodeId}</code>
                    </li>
                  ))}
                </ul>
              </details>

              <details className="inspector-section inspector-disclosure">
                <summary className="section-title">
                  <span>Validation</span>
                  <span>{inspector.verificationStatus}</span>
                </summary>
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
              </details>

              {paperId.startsWith('tex_') ? <span className="source-link">local .tex · {inspector.anchor}</span> : <a
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
              </a>}
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
          {activeViewTab === 'lineage' && lineageRelations.length > 0 ? (
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
                            <label htmlFor={`lineage-review-note-${edge.id}`} className="block">
                              Source review rationale and scope
                              <textarea
                                id={`lineage-review-note-${edge.id}`}
                                name="lineage_review_rationale"
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
    </>
  );

  if (stage) {
    const stageBreadcrumb =
      stage === 'spec'
        ? '02 Spec · Freeze rules'
        : stage === 'compatibility'
          ? '03 Compatibility · Match symbols'
          : '01 Graph · Extract formulas';

    const nextStageLink =
      stage === 'graph' ? (
        <Link
          href={withContext('/spec', workspaceParams)}
          className="wb-primary-cta"
        >
          <span>Next: Step 2 · Spec</span>
          <ArrowRight size={13} aria-hidden="true" />
        </Link>
      ) : stage === 'spec' ? (
        <Link
          href={withContext('/compatibility', workspaceParams)}
          className="wb-primary-cta"
        >
          <span>Next: Step 3 · Compatibility</span>
          <ArrowRight size={13} aria-hidden="true" />
        </Link>
      ) : (
        <Link
          href={withContext('/proposals', workspaceParams)}
          className="wb-primary-cta"
        >
          <span>Next: Step 4 · Proposals</span>
          <ArrowRight size={13} aria-hidden="true" />
        </Link>
      );

    return (
      <main
        className={`app-shell app-shell-${stage} wb-shell${
          isLeftCollapsed ? ' is-left-collapsed' : ''
        }${isRightCollapsed ? ' is-right-collapsed' : ''}${
          isZenMode ? ' is-zen-mode' : ''
        }`}
        suppressHydrationWarning
      >
        <output className="sr-only" aria-live="polite">
          {researchLoadNotice}
        </output>

        {searchDialogNode}

        <WorkbenchLeftSpine
          user={user}
          collapsed={isLeftCollapsed}
          onCollapse={() => setIsLeftCollapsed(true)}
          activePaperLabel={
            hasPersistedGraph
              ? `${paperId} · v${paperVersion}`
              : stage === 'spec'
                ? undefined
                : '1706.03762 · demo'
          }
          completedSteps={{
            '/graph': hasPersistedGraph,
            '/compatibility': compatMappings.some((m) => m.status === 'compatible'),
          }}
          onOpenSearch={() => setIsSearchOpen((open) => !open)}
          isSearchOpen={isSearchOpen}
          showWalkthroughToggle={stage === 'spec' || stage === 'compatibility'}
        >
          {stage !== 'spec' ? (
            <section aria-label="Paper sources" className="wb-sources-drawer">
              {paperSourcesBody}
            </section>
          ) : null}
        </WorkbenchLeftSpine>

        <div className="wb-center-column">
          <header className="wb-subbar">
            <div className="wb-subbar-left">
              {isLeftCollapsed ? (
                <button
                  type="button"
                  className="wb-icon-btn"
                  onClick={() => setIsLeftCollapsed(false)}
                  title="Expand sidebar ([)"
                  aria-label="Expand paper sources"
                >
                  <PanelLeftOpen size={14} aria-hidden="true" />
                </button>
              ) : null}
              <span className="wb-breadcrumb-root">Workspace</span>
              <ChevronRight size={13} className="text-muted-foreground shrink-0" aria-hidden="true" />
              <span className="wb-breadcrumb-current">{stageBreadcrumb}</span>
            </div>
            <div className="wb-subbar-right">
              {nextStageLink}
              <button
                type="button"
                className="wb-inspector-toggle"
                onClick={() => setIsRightCollapsed((v) => !v)}
                title={isRightCollapsed ? 'Expand inspector (])' : 'Collapse inspector (])'}
                aria-label={isRightCollapsed ? 'Expand inspector' : 'Collapse inspector'}
              >
                {isRightCollapsed ? (
                  <PanelRightOpen size={14} aria-hidden="true" />
                ) : (
                  <PanelRightClose size={14} aria-hidden="true" />
                )}
                <span>Inspector</span>
                <kbd aria-hidden="true">]</kbd>
              </button>
            </div>
          </header>

          <div className={`wb-main-scroll${stage === 'graph' ? ' is-graph-canvas' : ''}`}>
            {importStripNode}
            {researchNotice ? (
              <output
                aria-live="polite"
                className="mx-4 mt-2 block rounded-lg border border-border/60 bg-card px-3 py-2 text-xs text-foreground"
              >
                {researchNotice}
              </output>
            ) : null}

            {walkthroughEnabled && stage === 'spec' ? (
              <section
                aria-label="Illustrative spec walkthrough"
                className="wb-walkthrough-card mx-4 mt-3"
              >
                <div className="wb-walkthrough-header">
                  <div className="flex items-center gap-2">
                    <span className="wb-status-dot is-ready" aria-hidden="true" />
                    <strong className="text-xs font-semibold text-foreground">
                      Illustrative sample walkthrough · Step 02 frozen spec example
                    </strong>
                  </div>
                  <div className="flex items-center gap-2">
                    <span className="wb-inline-note">Read-only preview · not stored evidence</span>
                    <button
                      type="button"
                      onClick={() => setWalkthrough(false)}
                      className="wb-text-link"
                    >
                      Dismiss
                    </button>
                  </div>
                </div>
                <div className="grid gap-3 md:grid-cols-3 text-xs mt-2.5">
                  <div className="wb-mini-tile">
                    <span className="wb-mini-label">Question & objective</span>
                    <p className="mt-1 text-[11px] text-foreground m-0">
                      Minimize <code>perplexity</code> on WikiText-103 while keeping{' '}
                      <code>latency_ms ≤ 12.5</code>.
                    </p>
                  </div>
                  <div className="wb-mini-tile">
                    <span className="wb-mini-label">Allowed transforms</span>
                    <p className="mt-1 font-mono text-[11px] text-foreground m-0">
                      kernel_swap · mixture · rank_projection
                    </p>
                  </div>
                  <div className="wb-mini-tile">
                    <span className="wb-mini-label">Sealed evaluation</span>
                    <p className="mt-1 text-[11px] text-muted-foreground m-0">
                      Holdout locked from search · deterministic seed <code>42</code>
                    </p>
                  </div>
                </div>
              </section>
            ) : null}

            {walkthroughEnabled && stage === 'compatibility' ? (
              <section
                aria-label="Illustrative compatibility walkthrough"
                className="wb-walkthrough-card mx-4 mt-3"
              >
                <div className="wb-walkthrough-header">
                  <div className="flex items-center gap-2">
                    <span className="wb-status-dot is-evidence" aria-hidden="true" />
                    <strong className="text-xs font-semibold text-foreground">
                      Illustrative sample walkthrough · Step 03 6-gate symbol alignment
                    </strong>
                  </div>
                  <div className="flex items-center gap-2">
                    <span className="wb-inline-note">Read-only preview · not stored evidence</span>
                    <button
                      type="button"
                      onClick={() => setWalkthrough(false)}
                      className="wb-text-link"
                    >
                      Dismiss
                    </button>
                  </div>
                </div>
                <div className="grid gap-3 md:grid-cols-3 text-xs mt-2.5">
                  <div className="wb-mini-tile">
                    <span className="wb-mini-label">Aligned ports</span>
                    <p className="mt-1 font-mono text-[11px] text-foreground m-0">
                      S3.E1 φ_exp(q) ↔ S3.E4 φ_elu(k) · [B,H,N,r]
                    </p>
                  </div>
                  <div className="wb-mini-tile">
                    <span className="wb-mini-label">5/6 gates passed</span>
                    <p className="mt-1 text-[11px] text-foreground m-0">
                      Symbol, shape, unit, role, and causal regime match cleanly.
                    </p>
                  </div>
                  <div className="wb-mini-tile">
                    <span className="wb-mini-label">Conditional domain</span>
                    <p className="mt-1 text-[11px] text-muted-foreground m-0">
                      Requires <code>qᵀφ(k) &gt; 0</code> denominator obligation at Step 04.
                    </p>
                  </div>
                </div>
              </section>
            ) : null}

            {graphPanelNode}
          </div>
        </div>

        <aside
          id="formula-inspector"
          className={`wb-inspector inspector-panel${isRightCollapsed ? ' is-collapsed' : ''}`}
          aria-label="Formula inspector"
        >
          {stage === 'spec' ? (
            <>
              <div className="wb-inspector-header">
                <span className="wb-inspector-title">Spec readiness & guarantees</span>
                <button
                  type="button"
                  className="wb-icon-btn"
                  onClick={() => setIsRightCollapsed(true)}
                  title="Collapse inspector (])"
                  aria-label="Collapse inspector"
                >
                  <PanelRightClose size={14} aria-hidden="true" />
                </button>
              </div>
              <div className="wb-inspector-body space-y-4">
                <section className="wb-inspector-section">
                  <p className="wb-inspector-heading">Stage summary</p>
                  <p className="text-xs text-muted-foreground leading-relaxed m-0">
                    Define and freeze your research question, metric direction, and compute
                    guardrails before compiling any candidate transformation in Step 04.
                  </p>
                  <button
                    type="button"
                    aria-pressed={walkthroughEnabled}
                    onClick={() => setWalkthrough(!walkthroughEnabled)}
                    className={`wb-walkthrough-btn mt-2.5${walkthroughEnabled ? ' is-active' : ''}`}
                  >
                    <WandSparkles size={13} aria-hidden="true" />
                    <span>
                      {walkthroughEnabled ? 'Hide sample walkthrough' : 'Preview sample spec'}
                    </span>
                  </button>
                </section>
                <section className="wb-inspector-section">
                  <p className="wb-inspector-heading">G1 spec invariants</p>
                  <ul className="wb-guarantee-list">
                    <li>
                      <span className="wb-status-dot is-evidence" aria-hidden="true" />
                      <span>Frozen specs lock a canonical SHA-256 digest; edits mint a new version</span>
                    </li>
                    <li>
                      <span className="wb-status-dot is-ready" aria-hidden="true" />
                      <span>Holdout dataset split stays sealed from parent selection and search</span>
                    </li>
                    <li>
                      <span className="wb-status-dot is-derivation" aria-hidden="true" />
                      <span>Step 04 compiler rejects unfrozen or drifted spec identifiers</span>
                    </li>
                    <li>
                      <span className="wb-status-dot is-citation" aria-hidden="true" />
                      <span>Ownership is isolated per authenticated SaaS workspace tenant</span>
                    </li>
                  </ul>
                </section>
                <section className="wb-inspector-section">
                  <p className="wb-inspector-heading">Shortcuts</p>
                  <div className="wb-shortcut-grid">
                    <span>Switch stages</span>
                    <kbd>Alt + 1..5</kbd>
                    <span>Toggle sidebar</span>
                    <kbd>[</kbd>
                    <span>Toggle inspector</span>
                    <kbd>]</kbd>
                  </div>
                </section>
              </div>
            </>
          ) : (
            <>
              {stage === 'compatibility' ? (
                <section className="wb-inspector-section px-4 pt-3 pb-3 border-b border-border/60">
                  <div className="flex items-center justify-between gap-2">
                    <p className="wb-inspector-heading m-0">6-gate compatibility policy</p>
                    <button
                      type="button"
                      aria-pressed={walkthroughEnabled}
                      onClick={() => setWalkthrough(!walkthroughEnabled)}
                      className="wb-text-link"
                    >
                      {walkthroughEnabled ? 'Hide sample' : 'Sample preview'}
                    </button>
                  </div>
                  <p className="text-[11px] text-muted-foreground leading-relaxed mt-1 mb-0">
                    Mappings require symbol, shape, unit, domain, role, and regime alignment plus a
                    written human review rationale before Step 04 compilation.
                  </p>
                </section>
              ) : null}
              <div className="wb-inspector-body">{formulaInspectorInner}</div>
            </>
          )}
        </aside>
      </main>
    );
  }

  return (
    <main
      className={`app-shell${isZenMode ? ' is-zen-mode' : ''}`}
      suppressHydrationWarning
    >
      <header className="topbar">
        <div className="brand-lockup">
          <div className="brand-mark" aria-hidden="true">
            <Network size={18} strokeWidth={2.2} />
          </div>
          <div>
            <div className="flex items-center gap-1.5">
              <p className="brand-name">FormulaGraph</p>
              <span className="pro-tier-badge">LAB</span>
            </div>
            <p className="brand-suffix">Exact Evidence &amp; Research</p>
          </div>
        </div>
        <StageNav orientation="horizontal" />
        <div className="topbar-actions">
          <output className="sr-only" aria-live="polite">
            {researchLoadNotice}
          </output>
          <button
            className={`search-trigger ${isSearchOpen ? 'search-trigger-active' : ''}`}
            type="button"
            aria-label={isSearchOpen ? 'Close graph search' : 'Search graph'}
            title="Search graph (Ctrl+K or /)"
            aria-expanded={isSearchOpen}
            aria-controls="graph-search-panel"
            onClick={() => setIsSearchOpen((open) => !open)}
          >
            <Search size={15} />
            <span>Search</span>
          </button>
          <Link
            className="workspace-help-link"
            href={opsHref}
            aria-label="Open Operations dashboard"
          >
            <Activity size={14} aria-hidden="true" />
            <span>Ops</span>
          </Link>
          <Link
            className="workspace-help-link"
            href={helpHref}
            aria-label="Open FormulaGraph help"
          >
            <BookOpenText size={14} aria-hidden="true" />
            <span>Help</span>
          </Link>
          <div className="workspace-pill" title={user.email ?? user.displayName}>
            <span className="workspace-dot" />
            <span>{user.displayName}</span>
          </div>
        </div>
      </header>

      {searchDialogNode}
      {importStripNode}
      {researchNotice ? (
        <output
          aria-live="polite"
          className="mx-4 my-2 block rounded-lg border border-border/60 bg-card px-3 py-2 text-xs text-foreground"
        >
          {researchNotice}
        </output>
      ) : null}

      <div
        className={`research-grid ${
          activeViewTab === 'spec'
            ? 'is-spec-view'
            : activeViewTab === 'compat'
              ? 'is-compat-view'
              : ''
        } ${
          isLeftCollapsed || activeViewTab === 'spec' ? 'is-left-collapsed' : ''
        } ${
          isRightCollapsed ||
          activeViewTab === 'spec' ||
          activeViewTab === 'compat'
            ? 'is-right-collapsed'
            : ''
        }`}
      >
        <aside
          className={`source-panel ${
            isLeftCollapsed || activeViewTab === 'spec' ? 'is-collapsed' : ''
          }`}
          aria-label="Paper sources"
        >
          <div className="panel-heading">
            <div>
              <p className="eyebrow">Sources</p>
              <h2>Paper corpus</h2>
            </div>
            <div className="panel-heading-actions">
              <span className="count-badge">{multiPaperCoverage.length || 1}</span>
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
          {paperSourcesBody}
        </aside>

        {graphPanelNode}

        <aside
          id="formula-inspector"
          className={`inspector-panel ${isRightCollapsed ? 'is-collapsed' : ''}`}
          aria-label="Formula inspector"
        >
          {formulaInspectorInner}
        </aside>
      </div>
    </main>
  );
}
