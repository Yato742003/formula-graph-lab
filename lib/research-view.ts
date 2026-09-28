export type CompatibilityView = {
  mappingId: string;
  producer: {
    equationId: string;
    equation: string;
    symbolId: string | null;
    symbol: string;
    domain: string;
    shape: string;
  };
  consumer: {
    equationId: string;
    equation: string;
    symbolId: string | null;
    symbol: string;
    domain: string;
    shape: string;
  };
  status: 'compatible' | 'incompatible' | 'unknown';
  freshness: 'current' | 'stale';
  policyVersion: string;
  reasons: string[];
  unresolved: string[];
  isReviewed: boolean;
};

export type LineageView = {
  id: string;
  source: string;
  target: string;
  relationType: string;
  status: 'asserted' | 'reviewed' | 'rejected';
  evidence: {
    paper: string;
    section: string;
    quote: string;
    refs: { id: string; anchor: string; sourceHash: string }[];
  };
};

export type ResearchSourceContext = {
  kind: string;
  paperId: string;
  version: number;
  anchor: string | null;
  text: string;
  latex: string;
};

export type CoverageView = {
  id: string;
  title: string;
  authors: string;
  hasHtml: boolean;
  eqCount: number;
};

export type ProposalView = {
  proposalId: string;
  contentHash: string;
  createdAt: string;
  specId: string;
  parents: string[];
  sourceSpanIds: string[];
  sourceRefs: ProposalSourceRef[];
  operator: string;
  operatorVersion: string;
  semantics: 'preserving' | 'approximation' | 'hypothesis_changing';
  transform: Record<string, unknown>;
  assumptions: string[];
  rationale: string;
  expectedEffect: string;
  review: ProposalReviewView | null;
};

export type ProposalReviewView = {
  reviewId: string;
  proposalId: string;
  proposalHash: string;
  reviewerId: string;
  reviewerRole: 'researcher' | 'reviewer' | 'admin';
  decision: 'accept_for_compilation' | 'reject';
  notes: string;
  reviewedAt: string;
};

export type ProposalSourceRef = {
  id: string;
  entityId: string;
  anchor: string;
};

export type ProposalSourceContext = {
  paperId: string;
  version: number;
  anchor: string;
  sourceHash: string;
  text: string;
  latex: string;
};

function record(value: unknown): Record<string, unknown> {
  if (!value || typeof value !== 'object' || Array.isArray(value)) {
    throw new Error('Invalid research record');
  }
  return value as Record<string, unknown>;
}

function text(value: unknown): string {
  if (typeof value !== 'string' || !value.trim())
    throw new Error('Invalid research text');
  return value;
}

function strings(value: unknown): string[] {
  if (
    !Array.isArray(value) ||
    !value.every((item) => typeof item === 'string')
  ) {
    throw new Error('Invalid research diagnostics');
  }
  return value;
}

function version(value: unknown): string {
  if (value == null) return '?';
  if (typeof value !== 'number' || !Number.isInteger(value) || value < 1) {
    throw new Error('Invalid source version');
  }
  return String(value);
}

function items(value: unknown, field = 'items'): unknown[] {
  const list = record(value)[field];
  if (!Array.isArray(list)) throw new Error('Invalid research list');
  return list;
}

function decodeProposalSourceRef(id: string): ProposalSourceRef {
  if (!/^span_[A-Za-z0-9_-]{1,600}$/.test(id)) {
    throw new Error('Invalid proposal source span');
  }
  try {
    const encoded = id.slice(5).replaceAll('-', '+').replaceAll('_', '/');
    const padded = encoded + '='.repeat((4 - (encoded.length % 4)) % 4);
    const bytes = Uint8Array.from(atob(padded), (character) =>
      character.charCodeAt(0),
    );
    const value: unknown = JSON.parse(
      new TextDecoder('utf-8', { fatal: true }).decode(bytes),
    );
    if (
      !Array.isArray(value) ||
      value.length !== 2 ||
      typeof value[0] !== 'string' ||
      !/^[A-Za-z0-9_:-]{1,200}$/.test(value[0]) ||
      typeof value[1] !== 'string' ||
      !value[1].length ||
      value[1].length > 200
    ) {
      throw new Error('Invalid proposal source span');
    }
    const canonical = btoa(
      String.fromCharCode(...new TextEncoder().encode(JSON.stringify(value))),
    )
      .replaceAll('+', '-')
      .replaceAll('/', '_')
      .replace(/=+$/, '');
    if (canonical !== id.slice(5)) throw new Error('Invalid proposal source span');
    return { id, entityId: value[0], anchor: value[1] };
  } catch {
    throw new Error('Invalid proposal source span');
  }
}

function port(value: unknown): CompatibilityView['producer'] {
  const p = record(value);
  if (
    p.shape !== null &&
    (!Array.isArray(p.shape) ||
      !p.shape.every(
        (dimension) =>
          typeof dimension === 'string' ||
          (typeof dimension === 'number' &&
            Number.isInteger(dimension) &&
            dimension > 0),
      ))
  )
    throw new Error('Invalid port shape');
  return {
    equationId: text(p.equation_id),
    equation: text(p.equation_id),
    symbolId:
      typeof p.scoped_symbol_id === 'string' && p.scoped_symbol_id.trim()
        ? p.scoped_symbol_id
        : null,
    symbol: text(p.symbol_name),
    domain: text(p.domain),
    shape: p.shape === null ? 'Unknown' : JSON.stringify(p.shape),
  };
}

export function parseCompatibilityView(value: unknown): CompatibilityView[] {
  return items(value).map((item) => {
    const row = record(item),
      mapping = record(row.mapping),
      assessment = record(row.assessment);
    const status = assessment.status,
      freshness = assessment.freshness;
    if (
      (status !== 'compatible' &&
        status !== 'incompatible' &&
        status !== 'unknown') ||
      (freshness !== 'current' && freshness !== 'stale') ||
      typeof mapping.explicit_binding_reviewed !== 'boolean' ||
      mapping.mapping_id !== assessment.mapping_id
    )
      throw new Error('Invalid compatibility');
    return {
      mappingId: text(mapping.mapping_id),
      producer: port(mapping.producer_port),
      consumer: port(mapping.consumer_port),
      status,
      freshness,
      policyVersion: text(assessment.policy_version),
      reasons: strings(assessment.reasons),
      unresolved: strings(assessment.unresolved_requirements),
      isReviewed: mapping.explicit_binding_reviewed,
    };
  });
}

export function parseLineageView(value: unknown): LineageView[] {
  return items(value).map((item) => {
    const row = record(item),
      source = record(row.source),
      target = record(row.target);
    const status = row.status;
    if (
      status !== 'asserted' &&
      status !== 'reviewed' &&
      status !== 'rejected'
    ) {
      throw new Error('Invalid lineage status');
    }
    const evidence = items(row, 'evidence').map(record);
    const refs = evidence.map((e) => {
      const sourceHash = text(e.source_hash);
      if (!/^[a-f0-9]{64}$/i.test(sourceHash))
        throw new Error('Invalid source hash');
      return {
        id: text(e.source_entity_id),
        anchor: text(e.anchor),
        sourceHash,
      };
    });
    return {
      id: text(row.assertion_id),
      source: `${text(source.id)} · v${version(source.version)}`,
      target: `${text(target.id)} · v${version(target.version)}`,
      relationType: text(row.relation_type),
      status,
      evidence: {
        paper: refs.map((e) => e.id).join(', ') || 'No source',
        section: refs.map((e) => e.anchor).join(', ') || 'No anchor',
        quote:
          typeof row.description === 'string'
            ? row.description
            : 'No assertion rationale supplied.',
        refs,
      },
    };
  });
}

export function parseResearchSourceContext(
  value: unknown,
  expected: LineageView['evidence']['refs'][number],
): ResearchSourceContext {
  const list = items(value);
  if (list.length !== 1) throw new Error('Source context unavailable');
  const row = record(list[0]);
  if (
    row.id !== expected.id ||
    row.source_hash !== expected.sourceHash ||
    typeof row.version !== 'number' ||
    !Number.isInteger(row.version) ||
    row.version < 1 ||
    (row.anchor !== null && typeof row.anchor !== 'string') ||
    typeof row.text !== 'string' ||
    typeof row.latex !== 'string'
  ) {
    throw new Error('Source context mismatch');
  }
  return {
    kind: text(row.kind),
    paperId: text(row.paper_id),
    version: row.version,
    anchor: row.anchor,
    text: row.text,
    latex: row.latex,
  };
}

export function parseCoverageView(value: unknown): CoverageView[] {
  return items(value, 'indexed_papers').map((item) => {
    const row = record(item);
    if (
      typeof row.has_html !== 'boolean' ||
      typeof row.equation_count !== 'number' ||
      !Number.isInteger(row.equation_count) ||
      row.equation_count < 0
    ) {
      throw new Error('Invalid corpus coverage');
    }
    return {
      id: `${text(row.paper_id)}${row.version == null ? '' : `v${version(row.version)}`}`,
      title: text(row.title),
      authors: 'Source metadata',
      hasHtml: row.has_html,
      eqCount: row.equation_count,
    };
  });
}

export function parseProposalHistory(value: unknown): {
  items: ProposalView[];
  total: number;
} {
  const root = record(value);
  const total = root.total;
  if (typeof total !== 'number' || !Number.isSafeInteger(total) || total < 0) {
    throw new Error('Invalid proposal count');
  }
  return {
    total,
    items: items(root).map((entry) => {
      const row = record(entry);
      const proposal = record(row.proposal);
      const semantics = proposal.semantics_class;
      if (
        typeof proposal.proposal_id !== 'string' ||
        !/^prop_[a-f0-9]{32}$/.test(proposal.proposal_id) ||
        typeof proposal.content_hash !== 'string' ||
        !/^[a-f0-9]{64}$/.test(proposal.content_hash) ||
        proposal.review_state !== 'pending' ||
        (semantics !== 'preserving' &&
          semantics !== 'approximation' &&
          semantics !== 'hypothesis_changing') ||
        typeof proposal.transform_json !== 'string' ||
        proposal.transform_json.length > 8192
      ) {
        throw new Error('Invalid untrusted proposal');
      }
      let review: ProposalReviewView | null = null;
      if (row.review !== null && row.review !== undefined) {
        const persistedReview = record(row.review);
        const role = persistedReview.reviewer_role;
        const decision = persistedReview.decision;
        if (
          persistedReview.proposal_id !== proposal.proposal_id ||
          persistedReview.proposal_hash !== proposal.content_hash ||
          (role !== 'researcher' && role !== 'reviewer' && role !== 'admin') ||
          (decision !== 'accept_for_compilation' && decision !== 'reject')
        ) {
          throw new Error('Invalid proposal review scope');
        }
        review = {
          reviewId: text(persistedReview.review_id),
          proposalId: text(persistedReview.proposal_id),
          proposalHash: text(persistedReview.proposal_hash),
          reviewerId: text(persistedReview.reviewer_id),
          reviewerRole: role,
          decision,
          notes: text(persistedReview.notes),
          reviewedAt: text(persistedReview.reviewed_at),
        };
      }
      const assumptions = items(proposal, 'assumptions').map((value) => {
        const assumption = record(value);
        if (assumption.origin !== 'ai_proposed' || assumption.discharged !== false) {
          throw new Error('Invalid proposal assumption trust');
        }
        return text(assumption.text);
      });
      const transform = record(JSON.parse(proposal.transform_json));
      const sourceSpanIds = strings(proposal.source_span_ids);
      const sourceRefs = sourceSpanIds.map(decodeProposalSourceRef);
      return {
        proposalId: text(proposal.proposal_id),
        contentHash: text(proposal.content_hash),
        createdAt: text(row.created_at),
        specId: text(proposal.problem_spec_id),
        parents: strings(proposal.parent_ids),
        sourceSpanIds,
        sourceRefs,
        operator: text(proposal.operator),
        operatorVersion: text(proposal.operator_version),
        semantics,
        transform,
        assumptions,
        rationale: text(proposal.rationale),
        expectedEffect: text(proposal.expected_effect),
        review,
      };
    }),
  };
}

export function parseProposalSourceContext(
  value: unknown,
  expected: ProposalSourceRef,
): ProposalSourceContext {
  const list = items(value);
  if (list.length !== 1) throw new Error('Proposal source unavailable');
  const row = record(list[0]);
  if (
    row.source_span_id !== expected.id ||
    row.id !== expected.entityId ||
    row.anchor !== expected.anchor ||
    typeof row.version !== 'number' ||
    !Number.isInteger(row.version) ||
    row.version < 1 ||
    typeof row.source_hash !== 'string' ||
    !/^[a-f0-9]{64}$/i.test(row.source_hash) ||
    typeof row.text !== 'string' ||
    typeof row.latex !== 'string'
  ) {
    throw new Error('Proposal source does not match its stored span');
  }
  return {
    paperId: text(row.paper_id),
    version: row.version,
    anchor: expected.anchor,
    sourceHash: row.source_hash,
    text: row.text,
    latex: row.latex,
  };
}
