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
  X,
} from 'lucide-react';
import {
  SyntheticEvent,
  useCallback,
  useEffect,
  useMemo,
  useState,
} from 'react';
import EvidenceGraphViewport, {
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

type InspectorRecord = {
  id: string;
  label: string;
  expression: string;
  tone: FormulaNode['type'];
  kind: string;
  confidence: number;
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
    requiresConfirmation: boolean;
    issueCount: number;
    contracts: Array<{
      name: string;
      category: string;
      shape: string;
      confidence: number;
      confirmed: boolean;
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

const demoConnections: GraphViewportEdge[] = [
  { id: 'demo-scaled', source: 'dot-product', target: 'scaled', relation: 'derived_from' },
  { id: 'demo-heads', source: 'scaled', target: 'multi-head', relation: 'generalizes' },
  { id: 'demo-kernel', source: 'dot-product', target: 'kernel', relation: 'approximates' },
  { id: 'demo-mashup-kernel', source: 'kernel', target: 'mashup', relation: 'derived_from' },
  { id: 'demo-mashup-heads', source: 'multi-head', target: 'mashup', relation: 'derived_from' },
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
    if (typeof number === 'string' && number.trim()) return `Equation ${number}`;
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
  if (typeof canonicalHash !== 'string' || typeof status !== 'string') return null;
  const contracts = Array.isArray(analysis.contracts)
    ? analysis.contracts.flatMap((value) => {
        if (!isRecord(value)) return [];
        if (
          typeof value.name !== 'string' ||
          typeof value.category !== 'string' ||
          typeof value.confidence !== 'number' ||
          typeof value.confirmed !== 'boolean'
        ) {
          return [];
        }
        const shape = Array.isArray(value.shape)
          ? value.shape.map(String).join(' × ') || 'scalar'
          : 'n/a';
        return [{
          name: value.name,
          category: value.category,
          shape,
          confidence: value.confidence,
          confirmed: value.confirmed,
        }];
      })
    : [];
  const shapeErrors = Array.isArray(analysis.shape_errors)
    ? analysis.shape_errors.length
    : 0;
  const domainErrors = Array.isArray(analysis.domain_errors)
    ? analysis.domain_errors.length
    : 0;
  return {
    status: status.replaceAll('_', ' '),
    canonicalHash,
    requiresConfirmation: analysis.requires_confirmation === true,
    issueCount: shapeErrors + domainErrors,
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
      : 1;
  const matchingEdges = edges.filter(
    (edge) => edge.source_uuid === node.uuid || edge.target_uuid === node.uuid,
  );
  const relation =
    'match_sources' in node
      ? node.match_sources.join(' + ')
      : (matchingEdges[0]?.relation.replaceAll('_', ' ') ?? 'source-bound');
  const pinnedSource = sourceUrl ||
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
      payloadString(node.payload, ['text', 'statement'], 'Source text unavailable'),
    validAt: node.valid_at ?? 'Unknown source time',
    episodeIds: node.episode_uuids,
    relations: matchingEdges.map((edge) => {
      const direction = edge.source_uuid === node.uuid ? 'outgoing' : 'incoming';
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
      (edge) => edge.relation === 'supersedes' && edge.target_uuid === node.uuid,
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
    confidence: node.confidence,
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

export default function ResearchWorkspace({ user }: ResearchWorkspaceProps) {
  const [selectedId, setSelectedId] = useState<string | null>('scaled');
  const [isImporting, setIsImporting] = useState(false);
  const [imported, setImported] = useState<WorkspaceImportResponse | null>(null);
  const [graphSnapshot, setGraphSnapshot] =
    useState<EvidenceGraphSnapshotResponse | null>(null);
  const [isGraphLoading, setIsGraphLoading] = useState(false);
  const [graphNotice, setGraphNotice] = useState('Loading saved evidence…');
  const [selectedSearchHit, setSelectedSearchHit] =
    useState<EvidenceSearchHit | null>(null);
  const [paperUrl, setPaperUrl] = useState(
    'https://arxiv.org/html/1706.03762',
  );
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
  const hasPersistedGraph = Boolean(graphSnapshot?.paper);
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
  const paperSections = useMemo(
    () => {
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
    }, [evidenceNodes, hasPersistedGraph, selectedEvidence],
  );
  const persistedAuthors = Array.isArray(persistedVersionNode?.payload.authors)
    ? persistedVersionNode.payload.authors.filter(
        (author): author is string => typeof author === 'string',
      )
    : [];
  const paperTitle =
    graphSnapshot?.paper?.title ?? imported?.paper.title ?? 'Attention Is All You Need';
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
    async (signal?: AbortSignal): Promise<EvidenceGraphSnapshotResponse | undefined> => {
      await Promise.resolve();
      setIsGraphLoading(true);
      setGraphNotice('Loading saved evidence…');
      try {
        const response = await fetch('/api/graph', {
          method: 'GET',
          headers: { accept: 'application/json' },
          signal,
        });
        const result = (await response.json()) as Partial<EvidenceGraphSnapshotResponse> & {
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
              : (completed.nodes.find((node) => node.kind === 'Equation')?.uuid ??
                completed.nodes[0].uuid),
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
      setNotice('Extracting and persisting source-bound evidence…');
      const response = await fetch('/api/imports', {
        method: 'POST',
        headers: { 'content-type': 'application/json' },
        body: JSON.stringify({ url: canonicalUrl }),
      });
      const result = (await response.json()) as Partial<WorkspaceImportResponse> & {
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
    setSearchNotice(cursor ? 'Loading the next evidence page…' : 'Ranking evidence…');
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
      const result = (await response.json()) as Partial<EvidenceSearchResponse> & {
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
          <button
            className={`icon-button ${isSearchOpen ? 'icon-button-active' : ''}`}
            aria-label={isSearchOpen ? 'Close graph search' : 'Search graph'}
            aria-expanded={isSearchOpen}
            aria-controls="graph-search-panel"
            onClick={() => setIsSearchOpen((open) => !open)}
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
        <section
          className="search-command"
          id="graph-search-panel"
          aria-label="Search evidence graph"
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
              <div className="search-results" aria-label="Evidence search results">
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
                        {hit.paper_id}{hit.version ? `v${hit.version}` : ''}
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
                    onClick={() => void runSearch(searchResult.next_cursor ?? undefined)}
                  >
                    Load more evidence
                  </Button>
                ) : null}
              </div>
            ) : (
              <div className="search-suggestions" aria-label="Search suggestions">
                {['scaled attention', 'd_model', 'convergence'].map((suggestion) => (
                  <button
                    key={suggestion}
                    type="button"
                    onClick={() => setSearchQuery(suggestion)}
                  >
                    {suggestion}
                  </button>
                ))}
              </div>
            )}
          </div>
        </section>
      ) : null}

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
            {isImporting ? 'Importing…' : 'Import paper'}
          </Button>
        </form>
        <p className="import-notice" aria-live="polite">
          <CircleDot size={13} />
          {notice}
        </p>
      </section>

      <div className="research-grid">
        <aside className="source-panel" aria-label="Paper sources">
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
              <p>Every extracted fact retains its paper version and HTML anchor.</p>
            </div>
          </div>
        </aside>

        <section className="graph-panel" aria-label="Formula relationship graph">
          <div className="graph-header">
            <div>
              <p className="eyebrow">Temporal formula graph</p>
              <h1>{hasPersistedGraph ? 'Exact evidence snapshot' : 'Attention lineage'}</h1>
            </div>
            <div className="legend" aria-label="Graph legend">
              <span><i className="legend-dot evidence-dot" />Evidence</span>
              <span><i className="legend-dot concept-dot" />Concept</span>
              <span><i className="legend-dot hypothesis-dot" />Hypothesis</span>
            </div>
          </div>

          <div className="graph-stage">
            <EvidenceGraphViewport
              nodes={graphNodes}
              edges={graphConnections}
              selectedId={selectedId}
              onSelect={selectGraphNode}
              loading={isGraphLoading}
            />
            <div className="graph-status">
              <span className="pulse-dot" />
              {hasPersistedGraph ? 'Exact graph persisted' : 'Version-aware demo'}
              <span>{graphNotice}</span>
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
            <Button
              variant="outline"
              className="mashup-button"
              disabled
              title="Typed formulas are ready; hypothesis mashup begins in Sprint 5"
            >
              <FlaskConical size={16} />
              Mashup in Sprint 5
            </Button>
          </div>
        </section>

        <aside className="inspector-panel" aria-label="Formula inspector">
          {inspector ? (
            <>
              <div className="panel-heading inspector-heading">
                <div>
                  <p className="eyebrow">Inspector</p>
                  <h2>{inspector.label}</h2>
                </div>
                <span className={`type-token type-${inspector.tone}`}>
                  {inspector.kind}
                </span>
              </div>

              <div className="formula-display">
                <p>{hasPersistedGraph ? 'Extracted expression' : 'Canonical expression'}</p>
                <code>{inspector.expression}</code>
              </div>

              {inspector.formulaAnalysis ? (
                <section className="inspector-section formula-identity">
                  <div className="section-title">
                    <h3>Formula identity</h3>
                    <span>{inspector.formulaAnalysis.status}</span>
                  </div>
                  <code className="canonical-hash" title="Stable canonical SHA-256">
                    {inspector.formulaAnalysis.canonicalHash}
                  </code>
                  <div className="contract-grid" aria-label="Inferred symbol contracts">
                    {inspector.formulaAnalysis.contracts.map((contract) => (
                      <div key={contract.name}>
                        <code>{contract.name}</code>
                        <span>{contract.category}</span>
                        <b>{contract.shape}</b>
                        <i title={`${Math.round(contract.confidence * 100)}% confidence`}>
                          {contract.confirmed ? 'confirmed' : 'review'}
                        </i>
                      </div>
                    ))}
                  </div>
                  <p className="formula-readiness">
                    {inspector.formulaAnalysis.requiresConfirmation
                      ? 'Human confirmation is required before this formula can enter a verified mashup.'
                      : inspector.formulaAnalysis.issueCount > 0
                        ? `${inspector.formulaAnalysis.issueCount} type or domain issue(s) need review.`
                        : 'Canonical structure and inferred contracts are ready for typed transformations.'}
                  </p>
                </section>
              ) : null}

              <section className="inspector-section">
                <div className="section-title">
                  <h3>Relation</h3>
                  <span>{Math.round(inspector.confidence * 100)}% confidence</span>
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
                  <h3>{hasPersistedGraph ? 'Source context' : 'Symbol contract'}</h3>
                  <span>{hasPersistedGraph ? 'immutable snapshot' : '4 symbols'}</span>
                </div>
                <div className="symbol-table">
                  {hasPersistedGraph || selectedSearchHit ? (
                    <>
                      <div><code>#</code><span>anchor</span><b>{inspector.anchor}</b></div>
                      <div><code>§</code><span>section</span><b>{inspector.section}</b></div>
                      <div><code>↳</code><span>extractor</span><b>{inspector.extractionMethod}</b></div>
                      <div><code>◫</code><span>episodes</span><b>{inspector.episodeCount}</b></div>
                    </>
                  ) : (
                    <>
                      <div><code>Q</code><span>query tensor</span><b>n × dₖ</b></div>
                      <div><code>K</code><span>key tensor</span><b>m × dₖ</b></div>
                      <div><code>V</code><span>value tensor</span><b>m × dᵥ</b></div>
                      <div><code>dₖ</code><span>key dimension</span><b>ℕ⁺</b></div>
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
                        <span>{relation.direction === 'incoming' ? '←' : '→'}</span>
                        <div>
                          <strong>{relation.relation}</strong>
                          <small>{relation.neighbor}</small>
                        </div>
                        <i>{relation.episodeCount} ep.</i>
                      </li>
                    ))}
                  </ol>
                ) : (
                  <p className="history-empty">No visible relations for this node.</p>
                )}
              </section>

              <section className="inspector-section">
                <div className="section-title">
                  <h3>Provenance episodes</h3>
                  <span>{inspector.paperLabel}</span>
                </div>
                <ul className="episode-list">
                  {inspector.episodeIds.map((episodeId) => (
                    <li key={episodeId}><code>{episodeId}</code></li>
                  ))}
                </ul>
              </section>

              <section className="inspector-section">
                <div className="section-title">
                  <h3>Validation</h3>
                  <span>{inspector.verificationStatus}</span>
                </div>
                <ul className="check-list">
                  <li><Check size={14} />Exact source snapshot retained</li>
                  <li><Check size={14} />Paper revision pinned · {inspector.paperLabel}</li>
                  <li>
                    <Check size={14} />
                    {!inspector.anchorIsSource
                      ? 'Generated anchor marked for review'
                      : 'Source anchor resolved'}
                  </li>
                  <li>
                    <Check size={14} />
                    {inspector.superseded ? 'Superseded by a newer revision' : 'No newer revision in view'}
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
                {inspector.anchorIsSource ? 'Open evidence in source' : 'Open paper source'}
                <span>↗</span>
              </a>
            </>
          ) : (
            <div className="inspector-empty">
              <Braces size={22} />
              <strong>No formula selected</strong>
              <span>Import a paper containing display equations to inspect one.</span>
            </div>
          )}
        </aside>
      </div>
    </main>
  );
}
