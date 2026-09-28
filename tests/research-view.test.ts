import { describe, expect, it } from 'vitest';
import { parseCompatibilityView, parseCoverageView, parseLineageView, parseProposalHistory, parseProposalSourceContext, parseResearchSourceContext } from '../lib/research-view';

function sourceSpanId(entityId: string, anchor: string) {
  const bytes = new TextEncoder().encode(JSON.stringify([entityId, anchor]));
  const binary = Array.from(bytes, (byte) => String.fromCharCode(byte)).join('');
  return `span_${btoa(binary).replaceAll('+', '-').replaceAll('/', '_').replace(/=+$/, '')}`;
}

describe('research read boundary', () => {
  it('keeps empty persisted lists empty', () => {
    expect(parseCompatibilityView({ items: [] })).toEqual([]);
    expect(parseLineageView({ items: [] })).toEqual([]);
    expect(parseCoverageView({ indexed_papers: [] })).toEqual([]);
  });

  it('rejects fabricated version objects and invalid coverage counts', () => {
    const paper = { paper_id: 'p', title: 'Paper', has_html: true, equation_count: 1 };
    expect(() => parseCoverageView({ indexed_papers: [{ ...paper, version: {} }] })).toThrow();
    expect(() => parseCoverageView({ indexed_papers: [{ ...paper, equation_count: -1 }] })).toThrow();
  });

  it('rejects a mismatched assessment rather than borrowing its success', () => {
    expect(() => parseCompatibilityView({ items: [{
      mapping: { mapping_id: 'mapping-a', explicit_binding_reviewed: true },
      assessment: { mapping_id: 'mapping-b', status: 'compatible', freshness: 'current' },
    }] })).toThrow();
  });

  it('shows the assessment policy version instead of inventing a current policy', () => {
    const [view] = parseCompatibilityView({ items: [{
      mapping: {
        mapping_id: 'mapping-a', explicit_binding_reviewed: true,
        producer_port: { equation_id: 'a', symbol_name: 'x', domain: 'real', shape: [] },
        consumer_port: { equation_id: 'b', symbol_name: 'y', domain: 'real', shape: [] },
      },
      assessment: {
        mapping_id: 'mapping-a', status: 'unknown', freshness: 'stale',
        policy_version: 'compatibility-policy.v2', reasons: [], unresolved_requirements: [],
      },
    }] });
    expect(view.policyVersion).toBe('compatibility-policy.v2');
    expect(view.freshness).toBe('stale');
  });

  it('retains assertion rationale without presenting it as a source quotation', () => {
    const [view] = parseLineageView({ items: [{
      assertion_id: 'edge-1', relation_type: 'approximation', status: 'asserted',
      source: { id: 'equation', version: 1 }, target: { id: 'equation', version: 2 },
      evidence: [{ source_entity_id: 'source-1', anchor: 'S1.E1', source_hash: 'a'.repeat(64) }],
      description: 'Unreviewed author-reported relationship',
    }] });
    expect(view.source).not.toEqual(view.target);
    expect(view.status).toBe('asserted');
    expect(view.evidence.section).toBe('S1.E1');
  });

  it('accepts only the exact workspace source and hash for an assertion', () => {
    const expected = { id: 'source-1', anchor: 'S1.E1', sourceHash: 'a'.repeat(64) };
    const source = { id: 'source-1', kind: 'equation', paper_id: '1706.03762',
      version: 1, anchor: 'S1.E1', text: 'source text', latex: 'x', source_hash: 'a'.repeat(64) };
    expect(parseResearchSourceContext({ items: [source] }, expected).text).toBe('source text');
    expect(() => parseResearchSourceContext({ items: [{ ...source, source_hash: 'b'.repeat(64) }] }, expected)).toThrow();
    expect(() => parseResearchSourceContext({ items: [] }, expected)).toThrow();
  });

  it('keeps proposal assumptions pending and rejects promoted trust', () => {
    const proposal = {
      proposal_id: 'prop_' + 'a'.repeat(32),
      content_hash: 'b'.repeat(64),
      problem_spec_id: 'spec-1',
      parent_ids: ['equation-1'],
      source_span_ids: [sourceSpanId('source-1', 'S1.E1')],
      operator: 'mix_positive_feature_maps',
      operator_version: '1',
      semantics_class: 'hypothesis_changing',
      transform_json: '{"operator":"mix_positive_feature_maps"}',
      assumptions: [{ text: 'Positive features', origin: 'ai_proposed', discharged: false }],
      rationale: 'Explore this combination.',
      expected_effect: 'Potentially lower latency.',
      review_state: 'pending',
    };
    const result = parseProposalHistory({
      total: 1,
      items: [{ proposal, created_at: '2026-09-25T00:00:00+00:00' }],
    });
    expect(result.items[0]?.assumptions).toEqual(['Positive features']);
    expect(result.items[0]?.semantics).toBe('hypothesis_changing');
    expect(result.items[0]?.sourceRefs).toEqual([{
      id: sourceSpanId('source-1', 'S1.E1'), entityId: 'source-1', anchor: 'S1.E1',
    }]);
    expect(() => parseProposalHistory({
      total: 1,
      items: [{ proposal: {
        ...proposal,
        assumptions: [{ text: 'Positive features', origin: 'ai_proposed', discharged: true }],
      }, created_at: '2026-09-25T00:00:00+00:00' }],
    })).toThrow();
  });

  it('resolves only the exact proposal source entity and anchor', () => {
    const source = {
      id: 'source-1', kind: 'equation', paper_id: '1706.03762', version: 7,
      anchor: 'S1.E1', text: 'Source excerpt', latex: 'x', source_hash: 'a'.repeat(64),
      source_span_id: sourceSpanId('source-1', 'S1.E1'),
    };
    const expected = { id: source.source_span_id, entityId: 'source-1', anchor: 'S1.E1' };
    expect(parseProposalSourceContext({ items: [source] }, expected).text).toBe('Source excerpt');
    expect(() => parseProposalSourceContext({ items: [{ ...source, anchor: 'S1.E2' }] }, expected)).toThrow();
    expect(() => parseProposalSourceContext({ items: [{ ...source, id: 'foreign-source' }] }, expected)).toThrow();
  });
});
