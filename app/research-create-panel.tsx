'use client';

import { useEffect, useRef, useState, type SyntheticEvent } from 'react';
import { parseCompatibilityView, parseLineageView } from '@/lib/research-view';

type Source = { id: string; kind: 'equation' | 'paper' | 'section'; paper_id: string; version: number; latex: string;
  text: string;
  anchor: string | null; source_span_id: string | null; source_hash: string; symbols: string[] };
function object(value: unknown): value is Record<string, unknown> {
  return !!value && typeof value === 'object' && !Array.isArray(value);
}
function sourceList(value: unknown): { items: Source[]; hasMore: boolean } {
  if (!object(value) || !Array.isArray(value.items) || typeof value.has_more !== 'boolean') throw new Error('Invalid source list');
  const items = value.items.map(item => {
    if (!object(item) || typeof item.id !== 'string' || typeof item.paper_id !== 'string' ||
      typeof item.version !== 'number' || !Number.isInteger(item.version) || item.version < 1 ||
      typeof item.latex !== 'string' || (item.anchor !== null && typeof item.anchor !== 'string') ||
      (item.source_span_id !== undefined && item.source_span_id !== null &&
        (typeof item.source_span_id !== 'string' || !/^span_[A-Za-z0-9_-]{1,600}$/.test(item.source_span_id))) ||
      typeof item.source_hash !== 'string' || !/^[a-f0-9]{64}$/.test(item.source_hash) ||
      !Array.isArray(item.symbols) || !item.symbols.every(s => typeof s === 'string')) throw new Error('Invalid source');
    if (item.kind !== undefined && !['equation', 'paper', 'section'].includes(item.kind as string)) throw new Error('Invalid source kind');
    if (item.text !== undefined && typeof item.text !== 'string') throw new Error('Invalid source text');
    return { ...item, source_span_id: item.source_span_id ?? null, kind: item.kind ?? 'equation', text: item.text ?? '' } as Source;
  });
  return { items, hasMore: value.has_more };
}
const metadataLabels = {
  paper_id: 'Paper identifier', title: 'Paper title', source_reference: 'Metadata source reference',
  version: 'Paper version', first_publication_at: 'First publication date',
  version_published_at: 'Version publication date', venue_published_at: 'Venue publication date',
};

export default function ResearchCreatePanel({ onCreated }: { onCreated: () => void }) {
  const [mode, setMode] = useState('lineage');
  const [sources, setSources] = useState<Source[]>([]);
  const [papers, setPapers] = useState<Source[]>([]);
  const [sections, setSections] = useState<Source[]>([]);
  const [page, setPage] = useState(0), [refresh, setRefresh] = useState(0);
  const [paperPage, setPaperPage] = useState(0), [paperHasMore, setPaperHasMore] = useState(false);
  const [sectionPage, setSectionPage] = useState(0), [sectionHasMore, setSectionHasMore] = useState(false);
  const [hasMore, setHasMore] = useState(false);
  const [sourceNotice, setSourceNotice] = useState('Loading source equations…');
  const [ends, setEnds] = useState({ source: '', target: '', producer: '', consumer: '', evidence: '' });
  const [relation, setRelation] = useState('mathematical_derivation');
  const [rationale, setRationale] = useState('');
  const [contract, setContract] = useState({ decision: 'accepted', category: '', domain: '',
    shape: 'null', normalization: 'unknown', mask: 'missing', causal: 'unknown',
    resourceClass: 'unknown', featureRank: '' });
  const [metadata, setMetadata] = useState<Record<string, string>>(
    Object.fromEntries(Object.keys(metadataLabels).map(key => [key, ''])),
  );
  const [notice, setNotice] = useState(''), [busy, setBusy] = useState(false);
  const flight = useRef(false), retries = useRef(new Map<string, { body: string; key: string }>());
  const paperRelation = mode === 'lineage' && ['citation', 'revision'].includes(relation);
  const endpoints = paperRelation ? papers : sources;
  const source = endpoints.find(s => s.id === ends.source), target = endpoints.find(s => s.id === ends.target);
  const evidenceSource = paperRelation ? sections.find(s => s.id === ends.evidence) : source;
  useEffect(() => {
    const controller = new AbortController();
    void (async () => {
      try {
        const response = await fetch(`/api/research/lineage/sources?limit=100&offset=${page * 100}`, { signal: controller.signal });
        if (!response.ok) throw new Error('Source list unavailable');
        const result = sourceList(await response.json());
        if (controller.signal.aborted) return;
        setSources(previous => page === 0 ? result.items : [...new Map([...previous, ...result.items].map(s => [s.id, s])).values()]);
        setHasMore(result.hasMore);
        setSourceNotice(result.items.length ? 'Source equations loaded.' : 'Import HTML to add source equations.');
      } catch { if (!controller.signal.aborted) setSourceNotice('Source list unavailable. Refresh to retry.'); }
    })();
    return () => controller.abort();
  }, [page, refresh]);
  useEffect(() => {
    const controller = new AbortController();
    void (async () => {
      try {
        const response = await fetch(`/api/research/lineage/sources?kind=paper&limit=100&offset=${paperPage * 100}`, { signal: controller.signal });
        if (!response.ok) throw new Error('Paper list unavailable');
        const result = sourceList(await response.json());
        if (controller.signal.aborted) return;
        setPapers(previous => paperPage === 0 ? result.items : [...new Map([...previous, ...result.items].map(s => [s.id, s])).values()]);
        setPaperHasMore(result.hasMore);
      } catch { if (!controller.signal.aborted) setSourceNotice('Paper list unavailable. Refresh to retry.'); }
    })();
    return () => controller.abort();
  }, [paperPage, refresh]);
  useEffect(() => {
    const controller = new AbortController();
    void (async () => {
      try {
        const response = await fetch(`/api/research/lineage/sources?kind=section&limit=100&offset=${sectionPage * 100}`, { signal: controller.signal });
        if (!response.ok) throw new Error('Section list unavailable');
        const result = sourceList(await response.json());
        if (controller.signal.aborted) return;
        setSections(previous => sectionPage === 0 ? result.items : [...new Map([...previous, ...result.items].map(s => [s.id, s])).values()]);
        setSectionHasMore(result.hasMore);
      } catch { if (!controller.signal.aborted) setSourceNotice('Section list unavailable. Refresh to retry.'); }
    })();
    return () => controller.abort();
  }, [sectionPage, refresh]);

  async function submit(event: SyntheticEvent) {
    event.preventDefault();
    if (flight.current) return;
    flight.current = true; setBusy(true);
    try {
      let payload: Record<string, unknown>;
      const url = mode === 'metadata' ? '/api/research/lineage/coverage' :
        mode === 'contract' ? '/api/contract-reviews' : `/api/research/${mode}`;
      if (mode === 'metadata') payload = {
        ...metadata, version: metadata.version ? Number(metadata.version) : null,
        first_publication_at: metadata.first_publication_at || null,
        version_published_at: metadata.version_published_at || null, venue_published_at: metadata.venue_published_at || null,
      };
      else if (mode === 'contract') {
        if (!source || !source.symbols.includes(ends.producer) || !source.anchor || !rationale.trim())
          throw new Error('Select a stored equation, symbol, anchor and review rationale.');
        let shape: unknown;
        try { shape = JSON.parse(contract.shape); } catch { throw new Error('Shape must be JSON: [] for scalar, null for unknown.'); }
        if (shape !== null && (!Array.isArray(shape) || shape.length > 16 ||
          !shape.every(value => (typeof value === 'number' && Number.isInteger(value) && value > 0) ||
            (typeof value === 'string' && value.length > 0 && value.length <= 100))))
          throw new Error('Shape must be null or a bounded array of dimensions.');
        const featureRank = contract.featureRank ? Number(contract.featureRank) : null;
        if (featureRank !== null && (!Number.isInteger(featureRank) || featureRank < 1 || featureRank > 100000))
          throw new Error('Feature rank must be an integer from 1 to 100000.');
        if (featureRank !== null && contract.category !== 'vector')
          throw new Error('Feature rank is only valid for a reviewed vector contract.');
        if (contract.decision === 'accepted' && (!contract.category || !contract.domain))
          throw new Error('Choose the reviewed category and domain.');
        payload = {
          equation_uuid: source.id, symbol_name: ends.producer,
          decision: contract.decision,
          reviewed_contract: contract.decision === 'accepted' ? {
            name: ends.producer, category: contract.category, domain: contract.domain,
            shape, feature_rank: featureRank, constraints: [], normalization: contract.normalization, mask: contract.mask,
            causal: contract.causal === 'unknown' ? null : contract.causal === 'yes',
            resource_class: contract.resourceClass,
          } : null,
          evidence: [`source-anchor:${source.anchor}`, `review-note:${rationale.trim()}`],
        };
      } else {
        if (!source || !target) throw new Error('Select both saved equations.');
        if (mode === 'lineage') {
          if (!evidenceSource?.anchor || !rationale.trim()) throw new Error('Source anchor and rationale required.');
          if (paperRelation && (evidenceSource.paper_id !== target.paper_id ||
            evidenceSource.version !== target.version)) throw new Error('Select context from the target paper version.');
          payload = { relation_type: relation,
            source: { kind: source.kind, id: source.id, version: source.version },
            target: { kind: target.kind, id: target.id, version: target.version },
            evidence: [{ source_entity_id: evidenceSource.id, anchor: evidenceSource.anchor, source_hash: evidenceSource.source_hash }],
            description: rationale.trim() };
        } else {
          if (!source.symbols.includes(ends.producer) || !target.symbols.includes(ends.consumer)) throw new Error('Select both stored symbols.');
          payload = { mapping: {
            producer_port: { equation_id: source.id, version: source.version, symbol_name: ends.producer },
            consumer_port: { equation_id: target.id, version: target.version, symbol_name: ends.consumer },
          } };
        }
      }
      const body = JSON.stringify(payload), previous = retries.current.get(url);
      const request = previous?.body === body ? previous : { body, key: crypto.randomUUID() };
      retries.current.set(url, request);
      const response = await fetch(url, { method: 'POST', body,
        headers: { 'content-type': 'application/json',
          [mode === 'contract' ? 'idempotency-key' : 'x-idempotency-key']: request.key } });
      if (!response.ok) throw new Error(`Server rejected request (${response.status}).`);
      const result: unknown = await response.json();
      if (mode === 'lineage') parseLineageView({ items: [result] });
      else if (mode === 'compatibility') parseCompatibilityView({ items: [result] });
      else if (mode === 'contract') {
        if (!object(result) || typeof result.review_id !== 'string' ||
          result.equation_uuid !== source?.id || result.symbol_name !== ends.producer ||
          result.decision !== contract.decision || typeof result.reviewer_id !== 'string')
          throw new Error('Invalid contract review receipt');
      }
      else if (!object(result) || result.paper_id !== metadata.paper_id.trim() || result.has_html !== false ||
        result.equation_count !== 0 || typeof result.registered_by !== 'string') throw new Error('Invalid metadata receipt');
      setNotice('Record saved. Source attribution and review are not mathematical proof.'); onCreated();
    } catch (error) { setNotice(`Not confirmed; inputs unchanged. ${error instanceof Error ? error.message : 'Retry.'}`); }
    finally { flight.current = false; setBusy(false); }
  }

  return (
    <details className="border border-border/70 rounded-xl bg-card/60 backdrop-blur-xs p-3 text-xs shadow-2xs">
      <summary className="cursor-pointer font-medium text-foreground hover:text-primary transition-colors select-none">
        Create source-backed research records
      </summary>
      <output className="block text-[11px] text-muted-foreground mt-1" aria-live="polite">
        {sourceNotice}
      </output>
      <div className="flex flex-wrap gap-1.5 my-2">
        <button
          type="button"
          disabled={busy}
          onClick={() => {
            setPage(0);
            setPaperPage(0);
            setSectionPage(0);
            setRefresh(value => value + 1);
          }}
          className="text-xs px-2 py-0.5 rounded border border-border/60 hover:bg-muted/50 transition-colors disabled:opacity-50"
        >
          Refresh sources
        </button>
        {hasMore ? (
          <button
            type="button"
            disabled={busy}
            onClick={() => setPage(value => value + 1)}
            className="text-xs px-2 py-0.5 rounded border border-border/60 hover:bg-muted/50 transition-colors disabled:opacity-50"
          >
            Load more sources
          </button>
        ) : null}
        {paperHasMore ? (
          <button
            type="button"
            disabled={busy}
            onClick={() => setPaperPage(value => value + 1)}
            className="text-xs px-2 py-0.5 rounded border border-border/60 hover:bg-muted/50 transition-colors disabled:opacity-50"
          >
            Load more papers
          </button>
        ) : null}
        {sectionHasMore ? (
          <button
            type="button"
            disabled={busy}
            onClick={() => setSectionPage(value => value + 1)}
            className="text-xs px-2 py-0.5 rounded border border-border/60 hover:bg-muted/50 transition-colors disabled:opacity-50"
          >
            Load more sections
          </button>
        ) : null}
      </div>
      <form onSubmit={e => { void submit(e); }} className="space-y-3">
        <fieldset disabled={busy} className="space-y-3">
          <label htmlFor="create-record-type" className="block text-xs font-medium text-foreground">
            Record type
            <select
              id="create-record-type"
              name="record_type"
              value={mode}
              onChange={e => setMode(e.target.value)}
              className="block w-full border border-border/60 rounded-md p-1.5 mt-1 bg-background text-foreground text-xs"
            >
              <option value="lineage">Mathematical lineage assertion</option>
              <option value="compatibility">Typed port mapping</option>
              <option value="contract">Symbol contract review</option>
              <option value="metadata">Metadata-only paper</option>
            </select>
          </label>
          {mode === 'metadata' ? (
            <>
              <p className="text-[11px] text-muted-foreground">
                No HTML/PDF is fetched. No equations are generated from metadata.
              </p>
              {Object.entries(metadataLabels).map(([key, label]) => (
                <label key={key} htmlFor={`metadata-${key}`} className="block text-xs font-medium text-foreground">
                  {label}
                  <input
                    id={`metadata-${key}`}
                    name={key}
                    type={key.endsWith('_at') ? 'date' : key === 'version' ? 'number' : 'text'}
                    min={key === 'version' ? 1 : undefined}
                    required={['paper_id', 'title', 'source_reference'].includes(key)}
                    maxLength={key === 'source_reference' ? 2048 : 500}
                    value={metadata[key]}
                    onChange={e => setMetadata(current => ({ ...current, [key]: e.target.value }))}
                    className="block w-full border border-border/60 rounded-md p-1.5 mt-1 bg-background text-foreground text-xs"
                  />
                </label>
              ))}
            </>
          ) : (
            <>
              {(['source', 'target'] as const)
                .filter(key => mode !== 'contract' || key === 'source')
                .map(key => (
                  <label key={key} htmlFor={`endpoint-${key}`} className="block text-xs font-medium text-foreground">
                    {paperRelation
                      ? (key === 'source' ? 'Cited predecessor paper' : 'Citing descendant paper')
                      : (key === 'source' ? 'Source / producer equation' : 'Target / consumer equation')}
                    <select
                      id={`endpoint-${key}`}
                      name={key}
                      required
                      value={endpoints.some(item => item.id === ends[key]) ? ends[key] : ''}
                      onChange={e => setEnds(current => ({
                        ...current,
                        [key]: e.target.value,
                        [key === 'source' ? 'producer' : 'consumer']: '',
                      }))}
                      className="block w-full border border-border/60 rounded-md p-1.5 mt-1 bg-background text-foreground text-xs"
                    >
                      <option value="">Choose {paperRelation ? 'paper' : 'equation'}</option>
                      {endpoints.map(s => (
                        <option key={s.id} value={s.id}>
                          {s.paper_id} v{s.version} · {s.anchor ?? s.id} · {s.latex}
                        </option>
                      ))}
                    </select>
                  </label>
                ))}
              {mode === 'contract' ? (
                <>
                  <p className="text-[11px] text-muted-foreground">
                    Review the exact equation and symbol. Acceptance confirms only the values entered here.
                  </p>
                  <label htmlFor="contract-reviewed-symbol" className="block text-xs font-medium text-foreground">
                    Reviewed symbol
                    <select
                      id="contract-reviewed-symbol"
                      name="reviewed_symbol"
                      required
                      value={ends.producer}
                      onChange={e => setEnds(current => ({ ...current, producer: e.target.value }))}
                      className="block w-full border border-border/60 rounded-md p-1.5 mt-1 bg-background text-foreground text-xs"
                    >
                      <option value="">Choose symbol</option>
                      {source?.symbols.map(symbol => (
                        <option key={symbol}>{symbol}</option>
                      ))}
                    </select>
                  </label>
                  <label htmlFor="contract-decision" className="block text-xs font-medium text-foreground">
                    Decision
                    <select
                      id="contract-decision"
                      name="decision"
                      value={contract.decision}
                      onChange={e => setContract(current => ({ ...current, decision: e.target.value }))}
                      className="block w-full border border-border/60 rounded-md p-1.5 mt-1 bg-background text-foreground text-xs"
                    >
                      <option value="accepted">Accept stated contract</option>
                      <option value="rejected">Reject inferred contract</option>
                    </select>
                  </label>
                  {contract.decision === 'accepted' ? (
                    <>
                      <label htmlFor="contract-category" className="block text-xs font-medium text-foreground">
                        Category
                        <select
                          id="contract-category"
                          name="category"
                          required
                          value={contract.category}
                          onChange={e => setContract(current => ({ ...current, category: e.target.value }))}
                          className="block w-full border border-border/60 rounded-md p-1.5 mt-1 bg-background text-foreground text-xs"
                        >
                          <option value="">Choose category</option>
                          {['scalar', 'vector', 'matrix', 'tensor', 'function', 'distribution', 'index'].map(value => (
                            <option key={value}>{value}</option>
                          ))}
                        </select>
                      </label>
                      <label htmlFor="contract-domain" className="block text-xs font-medium text-foreground">
                        Domain
                        <select
                          id="contract-domain"
                          name="domain"
                          required
                          value={contract.domain}
                          onChange={e => setContract(current => ({ ...current, domain: e.target.value }))}
                          className="block w-full border border-border/60 rounded-md p-1.5 mt-1 bg-background text-foreground text-xs"
                        >
                          <option value="">Choose domain</option>
                          {['real', 'positive', 'non_negative', 'complex', 'integer'].map(value => (
                            <option key={value}>{value}</option>
                          ))}
                        </select>
                      </label>
                      <label htmlFor="contract-shape" className="block text-xs font-medium text-foreground">
                        Shape JSON
                        <textarea
                          id="contract-shape"
                          name="shape"
                          required
                          maxLength={2000}
                          value={contract.shape}
                          onChange={e => setContract(current => ({ ...current, shape: e.target.value }))}
                          className="block w-full border border-border/60 rounded-md p-1.5 mt-1 bg-background text-foreground text-xs font-mono"
                        />
                      </label>
                      <p className="text-[11px] text-muted-foreground">
                        Use [] for a scalar and null when shape is unknown.
                      </p>
                      <label htmlFor="contract-feature-rank" className="block text-xs font-medium text-foreground">
                        Feature-map output rank
                        <input
                          id="contract-feature-rank"
                          name="feature_rank"
                          type="number"
                          min={1}
                          max={100000}
                          step={1}
                          value={contract.featureRank}
                          onChange={e => setContract(current => ({ ...current, featureRank: e.target.value }))}
                          className="block w-full border border-border/60 rounded-md p-1.5 mt-1 bg-background text-foreground text-xs"
                        />
                      </label>
                      <p className="text-[11px] text-muted-foreground">
                        Optional; only enter this when the reviewed vector is a feature map. It is not inferred from shape.
                      </p>
                      <label htmlFor="contract-normalization" className="block text-xs font-medium text-foreground">
                        Normalization
                        <select
                          id="contract-normalization"
                          name="normalization"
                          value={contract.normalization}
                          onChange={e => setContract(current => ({ ...current, normalization: e.target.value }))}
                          className="block w-full border border-border/60 rounded-md p-1.5 mt-1 bg-background text-foreground text-xs"
                        >
                          {['unknown', 'none', 'l1', 'l2', 'softmax', 'layer_norm', 'rms_norm', 'batch_norm'].map(value => (
                            <option key={value}>{value}</option>
                          ))}
                        </select>
                      </label>
                      <label htmlFor="contract-mask" className="block text-xs font-medium text-foreground">
                        Mask
                        <select
                          id="contract-mask"
                          name="mask"
                          value={contract.mask}
                          onChange={e => setContract(current => ({ ...current, mask: e.target.value }))}
                          className="block w-full border border-border/60 rounded-md p-1.5 mt-1 bg-background text-foreground text-xs"
                        >
                          {['missing', 'none', 'causal', 'padding', 'sliding_window', 'custom'].map(value => (
                            <option key={value}>{value}</option>
                          ))}
                        </select>
                      </label>
                      <label htmlFor="contract-causal" className="block text-xs font-medium text-foreground">
                        Causality
                        <select
                          id="contract-causal"
                          name="causal"
                          value={contract.causal}
                          onChange={e => setContract(current => ({ ...current, causal: e.target.value }))}
                          className="block w-full border border-border/60 rounded-md p-1.5 mt-1 bg-background text-foreground text-xs"
                        >
                          <option value="unknown">Unknown</option>
                          <option value="yes">Causal</option>
                          <option value="no">Not causal</option>
                        </select>
                      </label>
                      <label htmlFor="contract-resource-class" className="block text-xs font-medium text-foreground">
                        Resource applicability
                        <select
                          id="contract-resource-class"
                          name="resource_class"
                          value={contract.resourceClass}
                          onChange={e => setContract(current => ({ ...current, resourceClass: e.target.value }))}
                          className="block w-full border border-border/60 rounded-md p-1.5 mt-1 bg-background text-foreground text-xs"
                        >
                          <option value="unknown">Unknown</option>
                          <option value="not_applicable">No execution resource claim</option>
                          <option value="cpu">CPU</option>
                          <option value="gpu">GPU</option>
                        </select>
                      </label>
                    </>
                  ) : null}
                  <label htmlFor="contract-rationale" className="block text-xs font-medium text-foreground">
                    Review rationale
                    <textarea
                      id="contract-rationale"
                      name="rationale"
                      required
                      maxLength={480}
                      value={rationale}
                      onChange={e => setRationale(e.target.value)}
                      className="block w-full border border-border/60 rounded-md p-1.5 mt-1 bg-background text-foreground text-xs"
                    />
                  </label>
                </>
              ) : mode === 'lineage' ? (
                <>
                  <p className="text-[11px] text-muted-foreground">
                    {paperRelation
                      ? 'Direction: cited predecessor → citing descendant. Select the exact HTML section containing the citation or revision evidence.'
                      : 'This mathematical assertion needs source context; it is not a proof.'}
                  </p>
                  <label htmlFor="lineage-relation" className="block text-xs font-medium text-foreground">
                    Relationship
                    <select
                      id="lineage-relation"
                      name="relation"
                      value={relation}
                      onChange={e => setRelation(e.target.value)}
                      className="block w-full border border-border/60 rounded-md p-1.5 mt-1 bg-background text-foreground text-xs"
                    >
                      {['mathematical_derivation', 'approximation', 'shared_objective', 'implementation', 'citation', 'revision'].map(value => (
                        <option key={value}>{value}</option>
                      ))}
                    </select>
                  </label>
                  {paperRelation ? (
                    <label htmlFor="lineage-evidence-section" className="block text-xs font-medium text-foreground">
                      Source section in the citing or revised paper
                      <select
                        id="lineage-evidence-section"
                        name="evidence_section"
                        required
                        value={
                          sections.some(
                            s => s.id === ends.evidence && s.paper_id === target?.paper_id && s.version === target.version,
                          )
                            ? ends.evidence
                            : ''
                        }
                        onChange={e => setEnds(current => ({ ...current, evidence: e.target.value }))}
                        className="block w-full border border-border/60 rounded-md p-1.5 mt-1 bg-background text-foreground text-xs"
                      >
                        <option value="">Choose source section</option>
                        {sections
                          .filter(s => s.paper_id === target?.paper_id && s.version === target.version)
                          .map(s => (
                            <option key={s.id} value={s.id}>
                              {s.paper_id} v{s.version} · {s.anchor ?? s.id} · {s.text.slice(0, 120)}
                            </option>
                          ))}
                      </select>
                    </label>
                  ) : null}
                  {evidenceSource?.anchor ? (
                    <a
                      className="underline text-xs text-primary hover:text-primary/80 inline-block my-1"
                      target="_blank"
                      rel="noopener noreferrer"
                      href={`https://arxiv.org/html/${evidenceSource.paper_id}v${evidenceSource.version}#${encodeURIComponent(evidenceSource.anchor)}`}
                    >
                      Inspect selected HTML source · {evidenceSource.paper_id} #{evidenceSource.anchor}
                    </a>
                  ) : null}
                  <label htmlFor="lineage-rationale" className="block text-xs font-medium text-foreground">
                    Source rationale
                    <textarea
                      id="lineage-rationale"
                      name="rationale"
                      required
                      maxLength={1000}
                      value={rationale}
                      onChange={e => setRationale(e.target.value)}
                      className="block w-full border border-border/60 rounded-md p-1.5 mt-1 bg-background text-foreground text-xs"
                    />
                  </label>
                </>
              ) : (
                <>
                  {(['producer', 'consumer'] as const).map(key => (
                    <label key={key} htmlFor={`compat-${key}-symbol`} className="block text-xs font-medium text-foreground">
                      {key === 'producer' ? 'Producer symbol' : 'Consumer symbol'}
                      <select
                        id={`compat-${key}-symbol`}
                        name={`${key}_symbol`}
                        required
                        value={ends[key]}
                        onChange={e => setEnds(current => ({ ...current, [key]: e.target.value }))}
                        className="block w-full border border-border/60 rounded-md p-1.5 mt-1 bg-background text-foreground text-xs"
                      >
                        <option value="">Choose symbol</option>
                        {(key === 'producer' ? source : target)?.symbols.map(s => (
                          <option key={s}>{s}</option>
                        ))}
                      </select>
                    </label>
                  ))}
                  <p className="text-[11px] text-muted-foreground">
                    The server resolves contracts. No client-supplied compatibility verdict is accepted.
                  </p>
                </>
              )}
            </>
          )}
          <button
            type="submit"
            disabled={busy}
            className="px-3.5 py-1.5 text-xs font-medium rounded-md bg-primary text-primary-foreground hover:bg-primary/90 transition-colors shadow-2xs cursor-pointer disabled:opacity-50"
          >
            {busy ? 'Saving record…' : 'Save research record'}
          </button>
        </fieldset>
        <output className="block text-xs text-muted-foreground mt-1" aria-live="polite">
          {notice}
        </output>
      </form>
    </details>
  );
}
