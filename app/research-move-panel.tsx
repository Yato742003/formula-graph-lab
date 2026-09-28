'use client';

import { useEffect, useMemo, useRef, useState } from 'react';
import {
  type CompatibilityView,
  type ProposalReviewView,
  type ProposalSourceRef,
  type ProposalSourceContext,
  type ProposalView,
  parseProposalHistory,
  parseProposalSourceContext,
} from '@/lib/research-view';
import { normalizeArxivHtmlUrl } from '@/lib/paper-url';
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogHeader,
  DialogTitle,
  DialogTrigger,
} from '@/components/ui/dialog';

type ProblemOption = {
  specId: string;
  version: number;
  task: string;
  allowsMix: boolean;
  seeds: number[];
};

type ProposalEquationSource = {
  id: string;
  paperId: string;
  version: number;
  anchor: string;
  sourceSpanId: string;
  latex: string;
  symbols: { id: string; notation: string }[];
};

type ProposalGenerationCapability =
  | {
      state: 'configured';
      model: string;
      maxGenerationCostUsd: string;
      dailyMaxCostUsd: string;
    }
  | {
      state:
        | 'disabled'
        | 'provider_not_configured'
        | 'budget_not_configured'
        | 'configuration_incomplete';
    };

function parseProposalGenerationCapability(
  value: unknown,
): ProposalGenerationCapability {
  if (!record(value) || typeof value.state !== 'string') {
    throw new Error('Invalid proposal provider readiness response.');
  }
  if (
    value.state === 'configured' &&
    typeof value.model === 'string' &&
    value.model.length > 0 &&
    value.model.length <= 100 &&
    typeof value.max_generation_cost_usd === 'string' &&
    value.max_generation_cost_usd.length <= 40 &&
    /^\d+(?:\.\d+)?$/.test(value.max_generation_cost_usd) &&
    typeof value.daily_max_cost_usd === 'string' &&
    value.daily_max_cost_usd.length <= 40 &&
    /^\d+(?:\.\d+)?$/.test(value.daily_max_cost_usd)
  ) {
    return {
      state: 'configured',
      model: value.model,
      maxGenerationCostUsd: value.max_generation_cost_usd,
      dailyMaxCostUsd: value.daily_max_cost_usd,
    };
  }
  if (
    value.state === 'disabled' ||
    value.state === 'provider_not_configured' ||
    value.state === 'budget_not_configured' ||
    value.state === 'configuration_incomplete'
  ) {
    return { state: value.state };
  }
  throw new Error('Invalid proposal provider readiness response.');
}

const PENDING_PROPOSAL_KEY = 'fgl.pending-proposal-generation.v1';

class PendingProposalIntentMismatch extends Error {}

function clearPendingProposalKey() {
  try {
    sessionStorage.removeItem(PENDING_PROPOSAL_KEY);
  } catch {
    // Storage can be unavailable in privacy-restricted browser contexts.
  }
}

async function getProposalGenerationKey(body: string): Promise<string> {
  const digest = await crypto.subtle.digest('SHA-256', new TextEncoder().encode(body));
  const fingerprint = Array.from(new Uint8Array(digest), (byte) => byte.toString(16).padStart(2, '0')).join('');
  try {
    const saved: unknown = JSON.parse(sessionStorage.getItem(PENDING_PROPOSAL_KEY) ?? 'null');
    if (
      record(saved) && saved.fingerprint === fingerprint &&
      typeof saved.key === 'string' && /^[A-Za-z0-9-]{1,80}$/.test(saved.key)
    ) return saved.key;
    if (
      record(saved) && typeof saved.fingerprint === 'string' &&
      typeof saved.key === 'string' && /^[A-Za-z0-9-]{1,80}$/.test(saved.key)
    ) {
      throw new PendingProposalIntentMismatch(
        'A previous request may still be active. Re-enter its exact inputs to check it, or review history before starting another paid attempt.',
      );
    }
  } catch (error) {
    if (error instanceof PendingProposalIntentMismatch) throw error;
    // Storage can be unavailable in privacy-restricted browser contexts.
  }
  const key = crypto.randomUUID();
  try {
    sessionStorage.setItem(PENDING_PROPOSAL_KEY, JSON.stringify({ fingerprint, key }));
  } catch {
    // The in-flight ref still prevents duplicate submissions in this mount.
  }
  return key;
}

function ProposalGenerationForm({
  spec,
  onCreated,
}: {
  spec?: ProblemOption;
  onCreated: () => void;
}) {
  const [expanded, setExpanded] = useState(false);
  const [sources, setSources] = useState<ProposalEquationSource[]>([]);
  const [sourceState, setSourceState] = useState<'idle' | 'loading' | 'loaded' | 'unavailable'>('idle');
  const [capability, setCapability] = useState<ProposalGenerationCapability | null>(null);
  const [capabilityState, setCapabilityState] = useState<'idle' | 'loading' | 'loaded' | 'unavailable'>('idle');
  const [selected, setSelected] = useState<string[]>([]);
  const [question, setQuestion] = useState('');
  const [consent, setConsent] = useState(false);
  const [busy, setBusy] = useState(false);
  const [notice, setNotice] = useState('');
  const [uncertain, setUncertain] = useState(false);
  const inFlight = useRef(false);
  const pendingKey = useRef<string | null>(null);

  useEffect(() => {
    if (!expanded || !spec) return;
    const controller = new AbortController();
    void (async () => {
      setCapabilityState('loading');
      setCapability(null);
      setSourceState('idle');
      setSources([]);
      setSelected([]);
      try {
        const capabilityResponse = await fetch(
          '/api/research/proposals/capabilities',
          { cache: 'no-store', signal: controller.signal },
        );
        if (!capabilityResponse.ok) throw new Error('Proposal provider readiness unavailable.');
        const parsedCapability = parseProposalGenerationCapability(
          await capabilityResponse.json(),
        );
        if (controller.signal.aborted) return;
        setCapability(parsedCapability);
        setCapabilityState('loaded');
        if (parsedCapability.state !== 'configured') return;

        setSourceState('loading');
        const response = await fetch(
          '/api/research/lineage/sources?kind=equation&limit=100&offset=0',
          { cache: 'no-store', signal: controller.signal },
        );
        if (!response.ok) throw new Error('Equation source list unavailable.');
        const value: unknown = await response.json();
        if (!record(value) || !Array.isArray(value.items)) throw new Error('Invalid equation source list.');
        const parsed = value.items.flatMap((item): ProposalEquationSource[] => {
          if (
            !record(item) || typeof item.id !== 'string' || typeof item.paper_id !== 'string' ||
            !Number.isInteger(item.version) || typeof item.anchor !== 'string' ||
            typeof item.source_span_id !== 'string' ||
            !/^span_[A-Za-z0-9_-]{1,600}$/.test(item.source_span_id) ||
            typeof item.latex !== 'string' || !Array.isArray(item.symbol_refs)
          ) return [];
          const symbols = item.symbol_refs.flatMap((symbol): ProposalEquationSource['symbols'] =>
            record(symbol) && typeof symbol.id === 'string' && typeof symbol.notation === 'string'
              ? [{ id: symbol.id, notation: symbol.notation }]
              : [],
          );
          return [{
            id: item.id,
            paperId: item.paper_id,
            version: item.version as number,
            anchor: item.anchor,
            sourceSpanId: item.source_span_id,
            latex: item.latex,
            symbols,
          }];
        });
        if (!controller.signal.aborted) {
          setSources(parsed);
          setSourceState('loaded');
        }
      } catch {
        if (!controller.signal.aborted) {
          setCapability(null);
          setCapabilityState('unavailable');
          setSourceState('unavailable');
        }
      }
    })();
    return () => controller.abort();
  }, [expanded, spec]);

  async function generate() {
    if (inFlight.current || !consent || !spec || selected.length < 2 || !question.trim()) return;
    inFlight.current = true;
    setBusy(true);
    setNotice('Sending the selected context to OpenAI for one bounded proposal attempt sequence…');
    setUncertain(false);
    let requestOutcomeUncertain = false;
    let preservePendingRequest = false;
    try {
      const selectedSources = sources.filter((source) => selected.includes(source.id));
      const body = JSON.stringify({
        spec_id: spec.specId,
        parent_ids: selectedSources.map((source) => source.id),
        source_span_ids: selectedSources.map((source) => source.sourceSpanId),
        research_question: question.trim(),
      });
      let idempotencyKey: string;
      try {
        idempotencyKey = pendingKey.current ?? await getProposalGenerationKey(body);
      } catch (error) {
        if (error instanceof PendingProposalIntentMismatch) preservePendingRequest = true;
        throw error;
      }
      pendingKey.current = idempotencyKey;
      requestOutcomeUncertain = true;
      const response = await fetch('/api/research/proposals/generate', {
        method: 'POST',
        headers: {
          'content-type': 'application/json',
          'x-idempotency-key': idempotencyKey,
        },
        body,
      });
      const result: unknown = await response.json().catch(() => null);
      if (!response.ok || !record(result) || !record(result.proposal) ||
          typeof result.proposal.proposal_id !== 'string' ||
          result.proposal.review_state !== 'pending') {
        const reason = record(result) && record(result.detail) && typeof result.detail.reason === 'string'
          ? result.detail.reason
          : null;
        const code = record(result) && record(result.detail) && typeof result.detail.code === 'string'
          ? result.detail.code
          : null;
        const noProviderCall = reason !== null && [
          'PROPOSAL_GENERATION_DISABLED',
          'PROPOSAL_PROVIDER_NOT_CONFIGURED',
          'PROPOSAL_COST_LIMIT_NOT_CONFIGURED',
          'PROPOSAL_COST_CAP_WOULD_BE_EXCEEDED',
          'PROPOSAL_DAILY_CAP_WOULD_BE_EXCEEDED',
          'PROPOSAL_PROMPT_SIZE_INVALID',
          'PROPOSAL_TOKEN_LIMIT_INVALID',
        ].includes(reason) || code === 'PROPOSAL_DAILY_CAP_EXCEEDED' ||
          (typeof result === 'object' && result !== null && 'detail' in result &&
            typeof result.detail === 'string' && result.detail.toLowerCase().includes('disabled'));
        requestOutcomeUncertain = response.ok ||
          code === 'PROPOSAL_GENERATION_IN_PROGRESS' ||
          code === 'PROPOSAL_RETRY_LIMIT_REACHED' ||
          (response.status >= 500 && !noProviderCall);
        const detail = record(result) && record(result.detail) && typeof result.detail.code === 'string'
          ? reason ?? result.detail.code
          : record(result) && typeof result.detail === 'string'
            ? result.detail
            : `Proposal generation unavailable (${response.status}).`;
        throw new Error(detail);
      }
      pendingKey.current = null;
      clearPendingProposalKey();
      setNotice('Saved as an untrusted proposal · pending human review. No proof, benchmark, or verification was created.');
      setQuestion('');
      setSelected([]);
      setConsent(false);
      onCreated();
    } catch (error) {
      setUncertain(requestOutcomeUncertain);
      if (!requestOutcomeUncertain && !preservePendingRequest) {
        pendingKey.current = null;
        clearPendingProposalKey();
      }
      const detail = error instanceof Error ? error.message : 'Proposal generation failed.';
      setNotice(preservePendingRequest
        ? detail
        : requestOutcomeUncertain
          ? `${detail} Check proposal history before requesting another generation; the provider may have completed the request.`
          : `${detail} No provider request was made. Review the inputs or server configuration before trying again.`);
    } finally {
      inFlight.current = false;
      setBusy(false);
    }
  }

  const ready = Boolean(spec && capability?.state === 'configured' &&
    sourceState === 'loaded' && selected.length >= 2 &&
    question.trim() && consent && !busy && !uncertain);
  function allowNewPaidAttempt() {
    pendingKey.current = null;
    clearPendingProposalKey();
    setUncertain(false);
    setNotice('A new request will have a new key and may incur another provider charge. Reconfirm consent before sending.');
    setConsent(false);
  }
  return (
    <section className="research-proposal-generator" aria-label="Generate an AI proposal">
      <button
        type="button"
        aria-expanded={expanded}
        onClick={() => setExpanded((value) => !value)}
        disabled={busy}
      >
        {expanded ? 'Close AI proposal generator' : 'Draft a research hypothesis with AI'}
      </button>
      {!expanded ? (
        <p>Optional AI draft · no automatic approval, verification, or experiment.</p>
      ) : <>
      <h3>Explore a research hypothesis</h3>
      <p>
        The model may suggest one declared feature-map mixture. It cannot approve it,
        verify it, alter your ProblemSpec, or run an experiment.
      </p>
      {!spec ? <output>No frozen ProblemSpec with this transformation is available.</output> : (
        <p>Frozen scope: <code>{spec.specId}</code> · {spec.task}</p>
      )}
      {!spec ? <output>No equation sources were loaded or sent.</output> : null}
      {spec && capabilityState === 'loading' ? (
        <output aria-live="polite">Checking provider configuration and request cap…</output>
      ) : null}
      {spec && capabilityState === 'unavailable' ? (
        <output aria-live="polite">Provider readiness is unavailable. Equation sources were not loaded or sent.</output>
      ) : null}
      {spec && capabilityState === 'loaded' && capability?.state !== 'configured' ? (
        <output aria-live="polite">
          AI proposal generation is {capability?.state === 'disabled'
            ? 'disabled'
            : capability?.state === 'provider_not_configured'
              ? 'missing provider configuration'
              : capability?.state === 'budget_not_configured'
                ? 'missing its cost limits'
                : 'not fully configured'}. No equation sources were loaded or sent.
        </output>
      ) : null}
      {capability?.state === 'configured' ? (
        <>
      <p className="research-proposal-cost-disclosure">
        OpenAI · <code>{capability.model}</code> · per-generation ceiling ${capability.maxGenerationCostUsd}
        {' '}(includes up to 3 provider calls) · daily workspace ceiling ${capability.dailyMaxCostUsd}.
        {' '}Available daily budget may be lower. Configuration is present; credentials are checked only when a request is sent.
      </p>
      <fieldset disabled={busy || uncertain || sourceState !== 'loaded' || !spec}>
        <legend>Select 2–8 stored equation parents</legend>
        {sourceState === 'loading' ? <output>Loading source equations…</output> : null}
        {sourceState === 'unavailable' ? <output>Equation sources are unavailable; nothing was sent.</output> : null}
        {sourceState === 'loaded' && sources.length === 0 ? <output>No equations with stored HTML anchors are available.</output> : null}
        <ul>
          {sources.map((source) => {
            const checked = selected.includes(source.id);
            return (
              <li key={source.id}>
                <label>
                  <input
                    type="checkbox"
                    aria-label={`${source.paperId} v${source.version} ${source.anchor}`}
                    checked={checked}
                    disabled={!checked && selected.length >= 8}
                    onChange={() => setSelected((current) => checked
                      ? current.filter((id) => id !== source.id)
                      : [...current, source.id])}
                  />
                  <span>
                    <strong>{source.paperId} v{source.version} · {source.anchor}</strong>
                    <code>{source.latex}</code>
                    <small>{source.symbols.map((symbol) => symbol.notation).join(', ') || 'No linked symbols'}</small>
                  </span>
                </label>
              </li>
            );
          })}
        </ul>
      </fieldset>
      <label>
        Research question
        <textarea
          value={question}
          maxLength={1000}
          onChange={(event) => setQuestion(event.target.value)}
          disabled={busy || uncertain || !spec}
          placeholder="What mathematical hypothesis should this mixture explore?"
        />
      </label>
      <label className="research-proposal-consent">
        <input
          type="checkbox"
          checked={consent}
          onChange={(event) => setConsent(event.target.checked)}
          disabled={busy || uncertain || !spec || selected.length < 2 || !question.trim()}
        />
        <span>
          I agree to send this question, the selected equations and symbol IDs, and the
          frozen ProblemSpec summary to OpenAI. I will not include confidential material.
        </span>
      </label>
      <p className="research-move-note">
        The ceiling above is checked before provider calls. The model response is only
        a proposal; review its sources and assumptions yourself. It has no calibrated
        confidence score; only the separate checker can report scoped verification evidence.
      </p>
      <button type="button" disabled={!ready} onClick={() => void generate()}>
        {busy ? 'Generating proposal…' : uncertain ? 'Check proposal history before retrying' : 'Generate one proposal'}
      </button>
      {uncertain ? (
        <button type="button" disabled={busy} onClick={allowNewPaidAttempt}>
          I checked history — allow a new paid attempt
        </button>
      ) : null}
      {notice ? <output aria-live="polite">{notice}</output> : null}
        </>
      ) : null}
      </>}
    </section>
  );
}

type CandidateReceipt = {
  candidateId: string;
  activityId: string;
  sourceProposalId: string | null;
  createdAt: string;
  specId: string;
  mappingId: string | null;
  operator: string;
  semantics: 'preserving' | 'approximation' | 'hypothesis_changing';
  obligations: {
    name: string;
    status: 'discharged' | 'conditional' | 'unresolved';
  }[];
  parents: string[];
  check: CandidateCheckReceipt | null;
  admission: AdmissionReceipt | null;
  numericalFixture: NumericalFixtureReceipt | null;
  researchCase: ResearchCaseReceipt | null;
};

type NumericalFixtureReceipt = {
  resultId: string;
  executionImage: string | null;
  outcome:
    | 'passed_suite'
    | 'counterexample'
    | 'unknown'
    | 'unsupported'
    | 'timeout'
    | 'error';
  createdAt: string;
  suiteVersion: string;
  seed: number;
  dtype: string;
  tolerance: number;
  checks: Record<string, boolean>;
  errorCode: string | null;
};

type ResearchCaseReceipt = {
  bindingId: string;
  resultId: string;
  outcome: 'supported_on_protocol' | 'failed_on_protocol' | 'inconclusive';
  holdoutMean: number | null;
  holdoutCi95Low: number | null;
  holdoutCi95High: number | null;
  claimScope: 'synthetic_operator_only_no_product_claim';
  performanceClaim: false;
};

type ResearchCaseEvidenceView = ResearchCaseReceipt & {
  replayStatus: 'replayed' | 'stored_only';
};

type CandidateCheckReceipt = {
  checkId: string;
  checkerVersion:
    | 'candidate-static.v1'
    | 'candidate-static.v2'
    | 'candidate-static.v3'
    | 'candidate-static.v4';
  outcome:
    | 'supported'
    | 'refuted'
    | 'unknown'
    | 'unsupported'
    | 'timeout'
    | 'error';
  scope: 'candidate_structure' | 'feature_kernel_identity';
  parse: 'supported' | 'unsupported' | 'error';
  type: 'well_typed' | 'ill_typed' | 'unknown';
  claim: string;
  assumptions: string[];
  inputHashes: string[];
  domain: string;
};

type AdmissionReceipt = {
  decisionId: string;
  allowed: boolean;
  outcome: 'allowed' | 'conditional' | 'denied';
  ruleId: string;
  reasons: string[];
  inputResultIds: string[];
};

type ReplayReportView = {
  reportHash: string;
  bundleHash: string;
  operator: string;
  operatorVersion: string;
  semantics: CandidateReceipt['semantics'];
  sourceRefs: { equationId: string; paperId: string; version: number; anchor: string; url: string }[];
  lineageAssertionIds: string[];
  compatibilityMappingId: string;
  proposalReviewStatus: 'not_applicable' | 'accepted_for_compilation';
  checks: {
    checkId: string;
    checkerVersion: string;
    claim: string;
    scope: 'candidate_structure' | 'feature_kernel_identity';
    replayStatus: 'replayed' | 'historical';
    outcome: CandidateCheckReceipt['outcome'];
    typeStatus: 'well_typed' | 'ill_typed' | 'unknown';
    domainStatus: 'discharged' | 'conditional' | 'unresolved' | 'contradictory' | 'unsupported';
    numericalStatus: 'passed_suite' | 'counterexample' | 'not_run' | 'timeout' | 'error';
    empiricalStatus: 'supported_on_protocol' | 'failed_on_protocol' | 'inconclusive' | 'not_run';
  }[];
  policyDecisions: {
    decisionId: string;
    action: string;
    outcome: AdmissionReceipt['outcome'];
    reasons: string[];
    replayStatus: 'replayed' | 'stored_only';
  }[];
  numericalFixtures: {
    resultId: string;
    outcome: NumericalFixtureReceipt['outcome'];
    seed: number;
    replayStatus: 'replayed' | 'stored_only';
  }[];
  researchCases: ResearchCaseEvidenceView[];
  empiricalExperiment:
    | 'not_run'
    | 'supported_on_protocol'
    | 'failed_on_protocol'
    | 'inconclusive';
  limitations: string[];
};

function parseReplayReport(
  value: unknown,
  candidateId: string,
  activityId: string,
): ReplayReportView {
  if (
    !record(value) ||
    value.schema_version !== 'compiler-replay-report.v1' ||
    value.status !== 'partial' ||
    value.scope !== 'compiler_replay_only' ||
    value.candidate_id !== candidateId ||
    value.activity_id !== activityId ||
    typeof value.report_hash !== 'string' || !/^[a-f0-9]{64}$/.test(value.report_hash) ||
    typeof value.bundle_hash !== 'string' || !/^[a-f0-9]{64}$/.test(value.bundle_hash) ||
    typeof value.operator !== 'string' || value.operator.length > 100 ||
    typeof value.operator_version !== 'string' || value.operator_version.length > 50 ||
    typeof value.compatibility_mapping_id !== 'string' || value.compatibility_mapping_id.length > 200 ||
    !['preserving', 'approximation', 'hypothesis_changing'].includes(String(value.semantics_class)) ||
    !['not_applicable', 'accepted_for_compilation'].includes(String(value.proposal_review_status)) ||
    value.compiler_replay !== 'reproduced' ||
    !['not_run', 'supported_on_protocol', 'failed_on_protocol', 'inconclusive'].includes(String(value.empirical_experiment)) ||
    !Array.isArray(value.source_refs) || value.source_refs.length < 1 || value.source_refs.length > 16 ||
    !Array.isArray(value.lineage_assertion_ids) || value.lineage_assertion_ids.length > 200 ||
    value.lineage_assertion_ids.some((item) => typeof item !== 'string' || item.length > 200) ||
    !Array.isArray(value.checks) || value.checks.length > 32 ||
    !Array.isArray(value.policy_decisions) || value.policy_decisions.length > 32 ||
    !Array.isArray(value.numerical_fixtures) || value.numerical_fixtures.length > 32 ||
    !Array.isArray(value.research_cases) || value.research_cases.length > 32 ||
    !Array.isArray(value.limitations) || value.limitations.length !== 3 ||
    value.limitations.some((item) => typeof item !== 'string' || !item.trim() || item.length > 500)
  ) throw new Error('Replay report did not match the selected candidate and activity.');

  const sourceRefs = value.source_refs.map((item) => {
    if (
      !record(item) || typeof item.equation_id !== 'string' || item.equation_id.length > 200 ||
      typeof item.paper_id !== 'string' || !/^\d{4}\.\d{4,5}$/.test(item.paper_id) ||
      !Number.isSafeInteger(item.paper_version) || (item.paper_version as number) < 1 ||
      typeof item.anchor !== 'string' || !item.anchor.trim() || item.anchor.length > 200 ||
      typeof item.source_url !== 'string' || item.source_url.length > 2048 ||
      item.anchor_is_source !== true
    ) throw new Error('Replay report contains an invalid source reference.');
    return {
      equationId: item.equation_id,
      paperId: item.paper_id,
      version: item.paper_version as number,
      anchor: item.anchor,
      url: item.source_url,
    };
  });
  const checks = value.checks.map((item) => {
    if (
      !record(item) || typeof item.check_id !== 'string' || !/^chk_[a-f0-9]{32}$/.test(item.check_id) ||
      typeof item.checker_version !== 'string' || item.checker_version.length > 100 ||
      typeof item.claim !== 'string' || item.claim.length > 500 ||
      !['candidate_structure', 'feature_kernel_identity'].includes(String(item.scope)) ||
      !['replayed', 'historical'].includes(String(item.replay_status)) ||
      !['supported', 'refuted', 'unknown', 'unsupported', 'timeout', 'error'].includes(String(item.outcome)) ||
      !['well_typed', 'ill_typed', 'unknown'].includes(String(item.type_status)) ||
      !['discharged', 'conditional', 'unresolved', 'contradictory', 'unsupported'].includes(String(item.domain_status)) ||
      !['passed_suite', 'counterexample', 'not_run', 'timeout', 'error'].includes(String(item.numerical_status)) ||
      !['supported_on_protocol', 'failed_on_protocol', 'inconclusive', 'not_run'].includes(String(item.empirical_status))
    ) throw new Error('Replay report contains an invalid checker result.');
    return {
      checkId: item.check_id,
      checkerVersion: item.checker_version,
      claim: item.claim,
      scope: item.scope as ReplayReportView['checks'][number]['scope'],
      replayStatus: item.replay_status as ReplayReportView['checks'][number]['replayStatus'],
      outcome: item.outcome as CandidateCheckReceipt['outcome'],
      typeStatus: item.type_status as ReplayReportView['checks'][number]['typeStatus'],
      domainStatus: item.domain_status as ReplayReportView['checks'][number]['domainStatus'],
      numericalStatus: item.numerical_status as ReplayReportView['checks'][number]['numericalStatus'],
      empiricalStatus: item.empirical_status as ReplayReportView['checks'][number]['empiricalStatus'],
    };
  });
  const policyDecisions = value.policy_decisions.map((item) => {
    if (
      !record(item) || typeof item.decision_id !== 'string' || !/^pol_[a-f0-9]{32}$/.test(item.decision_id) ||
      typeof item.action !== 'string' || item.action.length > 100 ||
      !['allowed', 'conditional', 'denied'].includes(String(item.outcome)) ||
      !['replayed', 'stored_only'].includes(String(item.replay_status)) ||
      !Array.isArray(item.reasons) || item.reasons.length > 32 ||
      item.reasons.some((reason) => typeof reason !== 'string' || reason.length > 200)
    ) throw new Error('Replay report contains an invalid policy decision.');
    return {
      decisionId: item.decision_id,
      action: item.action,
      outcome: item.outcome as AdmissionReceipt['outcome'],
      reasons: item.reasons as string[],
      replayStatus: item.replay_status as ReplayReportView['policyDecisions'][number]['replayStatus'],
    };
  });
  const numericalFixtures = value.numerical_fixtures.map((item) => {
    if (
      !record(item) || typeof item.result_id !== 'string' || !/^num_[a-f0-9]{32}$/.test(item.result_id) ||
      !['passed_suite', 'counterexample', 'unknown', 'unsupported', 'timeout', 'error'].includes(String(item.outcome)) ||
      !Number.isSafeInteger(item.seed) || (item.seed as number) < 0 ||
      !['replayed', 'stored_only'].includes(String(item.replay_status)) ||
      item.scope !== 'synthetic_feature_kernel_fixture' || item.performance_claim !== false
    ) throw new Error('Replay report contains an invalid diagnostic fixture.');
    return {
      resultId: item.result_id,
      outcome: item.outcome as NumericalFixtureReceipt['outcome'],
      seed: item.seed as number,
      replayStatus: item.replay_status as ReplayReportView['numericalFixtures'][number]['replayStatus'],
    };
  });
  const researchCases = value.research_cases.map((item) => {
    if (
      !record(item) || typeof item.result_id !== 'string' || !/^exp_[a-f0-9]{32}$/.test(item.result_id) ||
      typeof item.binding_id !== 'string' || !/^bind_[a-f0-9]{32}$/.test(item.binding_id) ||
      !['supported_on_protocol', 'failed_on_protocol', 'inconclusive'].includes(String(item.outcome)) ||
      item.claim_scope !== 'synthetic_operator_only_no_product_claim' ||
      item.performance_claim !== false ||
      !['replayed', 'stored_only'].includes(String(item.replay_status)) ||
      (item.holdout_mean !== null && item.holdout_mean !== undefined && !Number.isFinite(item.holdout_mean)) ||
      (item.holdout_ci95_low !== null && item.holdout_ci95_low !== undefined && !Number.isFinite(item.holdout_ci95_low)) ||
      (item.holdout_ci95_high !== null && item.holdout_ci95_high !== undefined && !Number.isFinite(item.holdout_ci95_high))
    ) throw new Error('Replay report contains an invalid registered research case.');
    return {
      bindingId: item.binding_id,
      resultId: item.result_id,
      outcome: item.outcome as ResearchCaseReceipt['outcome'],
      holdoutMean: typeof item.holdout_mean === 'number' ? item.holdout_mean : null,
      holdoutCi95Low: typeof item.holdout_ci95_low === 'number' ? item.holdout_ci95_low : null,
      holdoutCi95High: typeof item.holdout_ci95_high === 'number' ? item.holdout_ci95_high : null,
      claimScope: item.claim_scope as ResearchCaseReceipt['claimScope'],
      performanceClaim: false as const,
      replayStatus: item.replay_status as ResearchCaseEvidenceView['replayStatus'],
    };
  });
  return {
    reportHash: value.report_hash,
    bundleHash: value.bundle_hash,
    operator: value.operator,
    operatorVersion: value.operator_version,
    semantics: value.semantics_class as ReplayReportView['semantics'],
    sourceRefs,
    lineageAssertionIds: value.lineage_assertion_ids as string[],
    compatibilityMappingId: value.compatibility_mapping_id,
    proposalReviewStatus: value.proposal_review_status as ReplayReportView['proposalReviewStatus'],
    checks,
    policyDecisions,
    numericalFixtures,
    researchCases,
    empiricalExperiment: value.empirical_experiment as ReplayReportView['empiricalExperiment'],
    limitations: value.limitations as string[],
  };
}

const MAX_REPLAY_BUNDLE_BYTES = 4 * 1024 * 1024;

async function readBoundedResponseText(response: Response): Promise<string> {
  const declaredLength = response.headers.get('content-length');
  if (declaredLength && /^\d+$/.test(declaredLength)) {
    const declaredBytes = Number(declaredLength);
    if (Number.isSafeInteger(declaredBytes) && declaredBytes > MAX_REPLAY_BUNDLE_BYTES) {
      try {
        await response.body?.cancel();
      } catch {
        // The server may have already closed the response body.
      }
      throw new Error('Replay bundle is too large.');
    }
  }

  const reader = response.body?.getReader();
  if (!reader) throw new Error('Replay bundle body is unavailable.');
  const bytes = new Uint8Array(MAX_REPLAY_BUNDLE_BYTES);
  let size = 0;
  try {
    while (true) {
      const { done, value } = await reader.read();
      if (done) break;
      if (value.byteLength > MAX_REPLAY_BUNDLE_BYTES - size) {
        await reader.cancel();
        throw new Error('Replay bundle is too large.');
      }
      bytes.set(value, size);
      size += value.byteLength;
    }
    return new TextDecoder('utf-8', { fatal: true }).decode(bytes.subarray(0, size));
  } catch (error) {
    try {
      await reader.cancel();
    } catch {
      // The response may already be closed; preserve the original failure.
    }
    throw error;
  } finally {
    reader.releaseLock();
  }
}

function validateReplayBundleExport(
  value: unknown,
  candidateId: string,
  activityId: string,
  bundleHash: string,
): void {
  if (
    !record(value) ||
    value.schema_version !== 'compiler-replay-bundle.v1' ||
    value.bundle_hash !== bundleHash ||
    !record(value.candidate) || value.candidate.candidate_id !== candidateId ||
    !record(value.activity) || value.activity.activity_id !== activityId
  ) throw new Error('Bundle did not match the displayed report.');
}

function replaySourceHref(source: ReplayReportView['sourceRefs'][number]): string | null {
  try {
    return `${normalizeArxivHtmlUrl(source.url)}#${encodeURIComponent(source.anchor)}`;
  } catch {
    return null;
  }
}

function record(value: unknown): value is Record<string, unknown> {
  return value !== null && typeof value === 'object' && !Array.isArray(value);
}

function ProposalSourceEvidence({ source }: { source: ProposalSourceRef }) {
  const [state, setState] = useState<'idle' | 'loading' | 'loaded' | 'unavailable'>('idle');
  const [context, setContext] = useState<ProposalSourceContext | null>(null);

  async function resolveSource() {
    setState('loading');
    try {
      const response = await fetch(
        `/api/research/lineage/sources?source_id=${encodeURIComponent(source.entityId)}`,
        { cache: 'no-store' },
      );
      if (!response.ok) throw new Error('Source unavailable');
      setContext(parseProposalSourceContext(await response.json(), source));
      setState('loaded');
    } catch {
      setContext(null);
      setState('unavailable');
    }
  }

  let sourceHref: string | null = null;
  if (context) {
    try {
      sourceHref = `${normalizeArxivHtmlUrl(`https://arxiv.org/html/${context.paperId}v${context.version}`)}#${encodeURIComponent(context.anchor)}`;
    } catch {
      // Keep the workspace-resolved excerpt available if no supported public URL exists.
    }
  }

  return (
    <li>
      <code>{source.id}</code> · {source.anchor}{' '}
      {state === 'loaded' && context ? (
        <>
          <span>{context.paperId} v{context.version}: </span>
          <span>{context.text || context.latex || 'No extracted source text.'}</span>{' '}
          {sourceHref ? (
            <a href={sourceHref} target="_blank" rel="noopener noreferrer">
              Open paper source ↗
            </a>
          ) : null}
        </>
      ) : (
        <button
          type="button"
          disabled={state === 'loading'}
          onClick={() => void resolveSource()}
        >
          {state === 'loading' ? 'Loading source…' : 'Resolve source'}
        </button>
      )}
      {state === 'unavailable' ? (
        <output aria-live="polite"> Source unavailable; proposal remains unverified.</output>
      ) : null}
    </li>
  );
}

function parseProblemList(value: unknown): {
  items: ProblemOption[];
  partial: boolean;
} {
  if (
    !record(value) ||
    !Array.isArray(value.items) ||
    typeof value.total !== 'number'
  ) {
    throw new Error('Invalid ProblemSpec history response.');
  }
  const items = value.items.flatMap((item): ProblemOption[] => {
    if (
      !record(item) ||
      typeof item.spec_id !== 'string' ||
      !Number.isInteger(item.version) ||
      typeof item.definition !== 'object' ||
      item.definition === null ||
      Array.isArray(item.definition)
    )
      return [];
    const definition = item.definition as Record<string, unknown>;
    const declarations = definition.allowed_transforms;
    const rawSeeds = definition.seeds;
    const allowsMix =
      Array.isArray(declarations) &&
      declarations.some(
        (entry) =>
          record(entry) &&
          entry.name === 'mix_positive_feature_maps' &&
          entry.version === '1',
      );
    const seeds =
      Array.isArray(rawSeeds) &&
      rawSeeds.every(
        (seed) =>
          Number.isSafeInteger(seed) && seed >= 0 && seed <= 2 ** 31 - 1,
      )
        ? (rawSeeds as number[])
        : [];
    return [
      {
        specId: item.spec_id,
        version: item.version as number,
        task:
          typeof definition.task === 'string'
            ? definition.task
            : 'Untitled research question',
        allowsMix,
        seeds,
      },
    ];
  });
  return { items, partial: value.total > items.length };
}

function parseReceipt(
  value: unknown,
): Omit<CandidateReceipt, 'specId' | 'mappingId'> {
  if (!record(value) || !record(value.candidate) || !record(value.activity)) {
    throw new Error('The compiler returned an incomplete receipt.');
  }
  const candidate = value.candidate;
  const activity = value.activity;
  const createdAt = activity.created_at;
  const sourceProposalId = activity.source_proposal_id ?? null;
  if (
    typeof candidate.candidate_id !== 'string' ||
    !/^cand_[a-f0-9]{32}$/.test(candidate.candidate_id) ||
    typeof activity.activity_id !== 'string' ||
    !/^act_[a-f0-9]{32}$/.test(activity.activity_id) ||
    (sourceProposalId !== null &&
      (typeof sourceProposalId !== 'string' ||
        !/^prop_[a-f0-9]{32}$/.test(sourceProposalId))) ||
    typeof createdAt !== 'string' ||
    !Number.isFinite(Date.parse(createdAt)) ||
    typeof candidate.operator !== 'string' ||
    !['preserving', 'approximation', 'hypothesis_changing'].includes(
      String(candidate.semantics_class),
    ) ||
    !Array.isArray(candidate.obligations) ||
    !Array.isArray(candidate.parents)
  ) {
    throw new Error('The compiler returned an invalid candidate receipt.');
  }
  const obligations = candidate.obligations.map((entry) => {
    if (
      !record(entry) ||
      typeof entry.name !== 'string' ||
      !['discharged', 'conditional', 'unresolved'].includes(
        String(entry.status),
      )
    ) {
      throw new Error('The compiler returned an invalid obligation.');
    }
    return {
      name: entry.name,
      status: entry.status as CandidateReceipt['obligations'][number]['status'],
    };
  });
  const parents = candidate.parents.map((entry) => {
    if (!record(entry) || typeof entry.entity_id !== 'string') {
      throw new Error('The compiler returned invalid parentage.');
    }
    return entry.entity_id;
  });
  return {
    candidateId: candidate.candidate_id,
    activityId: activity.activity_id,
    sourceProposalId,
    createdAt: new Date(createdAt).toISOString(),
    operator: candidate.operator,
    semantics: candidate.semantics_class as CandidateReceipt['semantics'],
    obligations,
    parents,
    check: null,
    admission: null,
    numericalFixture: null,
    researchCase: null,
  };
}

function parseCandidateCheck(
  value: unknown,
  candidateId: string,
): CandidateCheckReceipt {
  if (!record(value) || !record(value.check)) {
    throw new Error('The independent checker returned an incomplete result.');
  }
  const check = value.check;
  if (
    typeof check.check_id !== 'string' ||
    !/^chk_[a-f0-9]{32}$/.test(check.check_id) ||
    check.candidate_id !== candidateId ||
    ![
      'candidate-static.v1',
      'candidate-static.v2',
      'candidate-static.v3',
      'candidate-static.v4',
    ].includes(String(check.checker_version)) ||
    ![
      'supported',
      'refuted',
      'unknown',
      'unsupported',
      'timeout',
      'error',
    ].includes(String(check.outcome)) ||
    !['candidate_structure', 'feature_kernel_identity'].includes(
      String(check.scope),
    ) ||
    typeof check.claim !== 'string' ||
    !record(check.vector) ||
    !['supported', 'unsupported', 'error'].includes(
      String(check.vector.parse),
    ) ||
    !['well_typed', 'ill_typed', 'unknown'].includes(
      String(check.vector.type),
    ) ||
    typeof check.vector.domain !== 'string' ||
    !Array.isArray(check.assumptions) ||
    check.assumptions.some((item) => typeof item !== 'string') ||
    !Array.isArray(check.input_hashes) ||
    check.input_hashes.some((item) => typeof item !== 'string')
  ) {
    throw new Error(
      'The independent checker returned an invalid scoped result.',
    );
  }
  return {
    checkId: check.check_id,
    checkerVersion: check.checker_version as CandidateCheckReceipt['checkerVersion'],
    outcome: check.outcome as CandidateCheckReceipt['outcome'],
    scope: check.scope as CandidateCheckReceipt['scope'],
    parse: check.vector.parse as CandidateCheckReceipt['parse'],
    type: check.vector.type as CandidateCheckReceipt['type'],
    claim: check.claim,
    assumptions: check.assumptions,
    inputHashes: check.input_hashes,
    domain: check.vector.domain,
  };
}

function parseAdmission(value: unknown): AdmissionReceipt {
  if (!record(value) || !record(value.decision)) {
    throw new Error('The admission service returned an incomplete decision.');
  }
  const decision = value.decision;
  if (
    typeof decision.decision_id !== 'string' ||
    !/^pol_[a-f0-9]{32}$/.test(decision.decision_id) ||
    typeof decision.allowed !== 'boolean' ||
    !['allowed', 'conditional', 'denied'].includes(String(decision.outcome)) ||
    typeof decision.rule_id !== 'string' ||
    !Array.isArray(decision.reasons) ||
    decision.reasons.some((reason) => typeof reason !== 'string') ||
    !Array.isArray(decision.input_result_ids) ||
    decision.input_result_ids.some((id) => typeof id !== 'string')
  ) {
    throw new Error('The admission service returned an invalid decision.');
  }
  return {
    decisionId: decision.decision_id,
    allowed: decision.allowed,
    outcome: decision.outcome as AdmissionReceipt['outcome'],
    ruleId: decision.rule_id,
    reasons: decision.reasons,
    inputResultIds: decision.input_result_ids,
  };
}

function parseCandidateHistory(value: unknown): {
  items: CandidateReceipt[];
  total: number;
} {
  if (
    !record(value) ||
    !Array.isArray(value.items) ||
    !Number.isSafeInteger(value.total) ||
    (value.total as number) < value.items.length
  ) {
    throw new Error('Invalid saved-candidate history response.');
  }
  const items = value.items.map((entry): CandidateReceipt => {
    if (
      !record(entry) ||
      !record(entry.candidate) ||
      !record(entry.activity) ||
      typeof entry.candidate.problem_spec_id !== 'string' ||
      (entry.candidate.mapping_id !== undefined &&
        entry.candidate.mapping_id !== null &&
        typeof entry.candidate.mapping_id !== 'string')
    ) {
      throw new Error('Saved candidate has incomplete provenance.');
    }
    const base = parseReceipt({
      candidate: entry.candidate,
      activity: entry.activity,
    });
    return {
      ...base,
      specId: entry.candidate.problem_spec_id,
      mappingId:
        typeof entry.candidate.mapping_id === 'string'
          ? entry.candidate.mapping_id
          : null,
      check:
        entry.check === null
          ? null
          : parseCandidateCheck({ check: entry.check }, base.candidateId),
      admission:
        entry.admission === null
          ? null
          : parseAdmission({ decision: entry.admission }),
      numericalFixture:
        entry.numerical_fixture === undefined ||
        entry.numerical_fixture === null
          ? null
          : parseNumericalFixture(
              entry.numerical_fixture,
              base.candidateId,
              entry.candidate.problem_spec_id,
            ),
      researchCase:
        entry.research_case === undefined || entry.research_case === null
          ? null
          : parseResearchCase(
              entry.research_case,
              base.candidateId,
              entry.candidate.problem_spec_id,
            ),
    };
  });
  return { items, total: value.total as number };
}

function parseNumericalFixture(
  value: unknown,
  candidateId: string,
  expectedSpecId?: string,
  expectedSeed?: number,
): NumericalFixtureReceipt {
  if (!record(value)) throw new Error('Invalid synthetic fixture receipt.');
  const result = record(value.result) ? value.result : value;
  const createdAt = result.created_at;
  const outcome = String(result.outcome);
  const checks = result.checks;
  if (
    typeof result.result_id !== 'string' ||
    !/^num_[a-f0-9]{32}$/.test(result.result_id) ||
    typeof result.result_hash !== 'string' ||
    !/^[a-f0-9]{64}$/.test(result.result_hash) ||
    result.candidate_id !== candidateId ||
    typeof result.problem_spec_id !== 'string' ||
    (expectedSpecId !== undefined &&
      result.problem_spec_id !== expectedSpecId) ||
    !Number.isSafeInteger(result.seed) ||
    (expectedSeed !== undefined && result.seed !== expectedSeed) ||
    !['float32', 'float64', 'bfloat16'].includes(String(result.dtype)) ||
    !Number.isFinite(result.tolerance) ||
    (result.tolerance as number) <= 0 ||
    result.suite_version !== 'feature-kernel-fixture.v1' ||
    (result.execution_image !== undefined &&
      result.execution_image !== null &&
      (typeof result.execution_image !== 'string' ||
        !/^sha256:[a-f0-9]{64}$/.test(result.execution_image))) ||
    typeof createdAt !== 'string' ||
    !Number.isFinite(Date.parse(createdAt)) ||
    ![
      'passed_suite',
      'counterexample',
      'unknown',
      'unsupported',
      'timeout',
      'error',
    ].includes(outcome) ||
    !record(checks) ||
    Object.keys(checks).length > 16 ||
    Object.values(checks).some((passed) => typeof passed !== 'boolean') ||
    result.fixture_scope !== 'synthetic_feature_kernel_fixture' ||
    result.performance_claim !== false ||
    (result.error_code !== null &&
      result.error_code !== undefined &&
      typeof result.error_code !== 'string')
  ) {
    throw new Error('Synthetic fixture receipt has invalid scope or content.');
  }
  return {
    resultId: result.result_id,
    executionImage:
      typeof result.execution_image === 'string' ? result.execution_image : null,
    outcome: outcome as NumericalFixtureReceipt['outcome'],
    createdAt: new Date(createdAt).toISOString(),
    suiteVersion: 'feature-kernel-fixture.v1',
    seed: result.seed as number,
    dtype: result.dtype as string,
    tolerance: result.tolerance as number,
    checks: checks as Record<string, boolean>,
    errorCode: typeof result.error_code === 'string' ? result.error_code : null,
  };
}

function parseResearchCase(
  value: unknown,
  candidateId: string,
  expectedSpecId?: string,
): ResearchCaseReceipt {
  if (!record(value)) throw new Error('Invalid registered research-case receipt.');
  const result = record(value.result) ? value.result : value;
  if (
    typeof result.result_id !== 'string' || !/^exp_[a-f0-9]{32}$/.test(result.result_id) ||
    typeof result.result_hash !== 'string' || !/^[a-f0-9]{64}$/.test(result.result_hash) ||
    result.candidate_id !== candidateId ||
    typeof result.problem_spec_id !== 'string' ||
    (expectedSpecId !== undefined && result.problem_spec_id !== expectedSpecId) ||
    typeof result.binding_id !== 'string' || !/^bind_[a-f0-9]{32}$/.test(result.binding_id) ||
    typeof result.binding_hash !== 'string' || !/^[a-f0-9]{64}$/.test(result.binding_hash) ||
    !['supported_on_protocol', 'failed_on_protocol', 'inconclusive'].includes(String(result.outcome)) ||
    result.claim_scope !== 'synthetic_operator_only_no_product_claim' ||
    result.performance_claim !== false ||
    !Array.isArray(result.trials) || result.trials.length !== 5 ||
    (result.holdout_mean !== null && result.holdout_mean !== undefined && !Number.isFinite(result.holdout_mean)) ||
    (result.holdout_ci95_low !== null && result.holdout_ci95_low !== undefined && !Number.isFinite(result.holdout_ci95_low)) ||
    (result.holdout_ci95_high !== null && result.holdout_ci95_high !== undefined && !Number.isFinite(result.holdout_ci95_high))
  ) throw new Error('Registered research-case receipt has invalid scope or content.');
  const binding = record(value.binding) ? value.binding : null;
  if (
    !binding || binding.candidate_id !== candidateId ||
    binding.binding_id !== result.binding_id ||
    binding.binding_hash !== result.binding_hash
  ) throw new Error('Registered research-case binding does not match its result.');
  return {
    bindingId: result.binding_id,
    resultId: result.result_id,
    outcome: result.outcome as ResearchCaseReceipt['outcome'],
    holdoutMean: typeof result.holdout_mean === 'number' ? result.holdout_mean : null,
    holdoutCi95Low: typeof result.holdout_ci95_low === 'number' ? result.holdout_ci95_low : null,
    holdoutCi95High: typeof result.holdout_ci95_high === 'number' ? result.holdout_ci95_high : null,
    claimScope: result.claim_scope,
    performanceClaim: false,
  };
}

function admissionReason(reason: string): string {
  const labels: Record<string, string> = {
    mapping_unknown:
      'The compiled record has no mapping handle for a freshness re-check.',
    mapping_stale:
      'The compatibility mapping or one of its source snapshots has changed.',
    mapping_not_usable:
      'The current mapping is not compatible and human-reviewed.',
    budget_or_quota_unavailable: 'No authorized compute budget is available.',
    verification_vector_missing:
      'No independent verification results are attached.',
    symbolic_result_ref_missing:
      'The decision has no persisted symbolic-check receipt.',
    runtime_artifacts_not_verified:
      'Runtime artifacts are not verified and pinned.',
    domain_gate_not_passed: 'The mathematical domain remains unresolved.',
    static_parse_or_type_gate_not_passed:
      'Static parsing or type checks have not passed.',
    unresolved_obligation: 'A proof obligation remains unresolved.',
    experiment_artifacts_or_protocol_unverified:
      'The experiment protocol or runtime artifacts are not pinned.',
    numerical_suite_not_passed: 'A numerical suite has not passed.',
    numerical_result_ref_missing:
      'No persisted numerical-suite receipt is attached.',
    empirical_protocol_not_supported:
      'No supported empirical result exists for this protocol.',
    human_scope_review_not_accepted:
      'A human has not accepted the scope of this claim.',
    frozen_quality_constraints_not_met:
      'The frozen quality constraints are not met.',
    scientific_refutation_is_not_retryable:
      'A scientific refutation cannot be retried as infrastructure failure.',
    retry_limit_reached: 'The bounded retry allowance is exhausted.',
  };
  return labels[reason] ?? reason.replaceAll('_', ' ');
}

function checkSummary(check: CandidateCheckReceipt | null) {
  if (!check) return { label: 'Not run', state: 'not-run' };
  if (check.outcome === 'supported') {
    return check.scope === 'feature_kernel_identity'
      ? { label: 'Scoped identity support', state: 'supported' }
      : { label: 'Structure only · no identity proof', state: 'unknown' };
  }
  return {
    label: check.outcome
      .replaceAll('_', ' ')
      .replace(/^./, (first) => first.toUpperCase()),
    state: check.outcome,
  };
}

function policySummary(admission: AdmissionReceipt | null) {
  if (!admission) return { label: 'Not recorded', state: 'not-run' };
  return {
    label: admission.outcome
      .replaceAll('_', ' ')
      .replace(/^./, (first) => first.toUpperCase()),
    state: admission.outcome,
  };
}

function comparisonEvidence(candidate: CandidateReceipt) {
  const check = checkSummary(candidate.check);
  const policy = policySummary(candidate.admission);
  const numerical = candidate.numericalFixture;
  return [
    { label: 'Independent check', state: check.state, value: check.label },
    { label: 'Policy', state: policy.state, value: policy.label },
    {
      label: 'Numerical',
      state: numerical?.outcome ?? 'not-run',
      value: numerical
        ? `Diagnostic only · ${numerical.outcome.replaceAll('_', ' ')}`
        : 'Not run',
    },
    { label: 'Empirical', state: 'not-run', value: 'Not run' },
  ];
}

export default function ResearchMovePanel({
  mappings,
  mappingsState,
  mappingsPartial,
}: {
  mappings: CompatibilityView[];
  mappingsState: 'loading' | 'loaded' | 'unavailable';
  mappingsPartial: boolean;
}) {
  const [problems, setProblems] = useState<ProblemOption[]>([]);
  const [problemState, setProblemState] = useState<
    'loading' | 'loaded' | 'unavailable'
  >('loading');
  const [partialProblems, setPartialProblems] = useState(false);
  const [specId, setSpecId] = useState('');
  const [mappingId, setMappingId] = useState('');
  const [weight, setWeight] = useState('0.5');
  const [receipt, setReceipt] = useState<CandidateReceipt | null>(null);
  const [replayReportAttempt, setReplayReportAttempt] = useState<{
    candidateId: string;
    activityId: string;
    state: 'loading' | 'loaded' | 'unavailable';
    report?: ReplayReportView;
  } | null>(null);
  const [bundleExportAttempt, setBundleExportAttempt] = useState<{
    candidateId: string;
    activityId: string;
    state: 'loading' | 'downloaded' | 'unavailable';
  } | null>(null);
  const [candidateHistory, setCandidateHistory] = useState<CandidateReceipt[]>(
    [],
  );
  const [comparison, setComparison] = useState<CandidateReceipt[]>([]);
  const [candidateHistoryState, setCandidateHistoryState] = useState<
    'loading' | 'loaded' | 'unavailable'
  >('loading');
  const [candidateHistoryTotal, setCandidateHistoryTotal] = useState(0);
  const [candidateHistoryOffset, setCandidateHistoryOffset] = useState(0);
  const [candidateHistoryRevision, setCandidateHistoryRevision] = useState(0);
  const [proposals, setProposals] = useState<ProposalView[]>([]);
  const [proposalTotal, setProposalTotal] = useState(0);
  const [proposalState, setProposalState] = useState<
    'loading' | 'loaded' | 'unavailable'
  >('loading');
  const [proposalRevision, setProposalRevision] = useState(0);
  const [proposalReviewNotes, setProposalReviewNotes] = useState<Record<string, string>>({});
  const [proposalReviewNotices, setProposalReviewNotices] = useState<Record<string, string>>({});
  const [proposalReviewBusy, setProposalReviewBusy] = useState<string | null>(null);
  const [dialogOpen, setDialogOpen] = useState(false);
  const [notice, setNotice] = useState('Loading frozen ProblemSpecs…');
  const [busy, setBusy] = useState(false);
  const [researchCaseBusy, setResearchCaseBusy] = useState(false);
  const inFlight = useRef(false);
  const researchCaseInFlight = useRef(false);
  const retry = useRef<{ body: string; key: string } | null>(null);
  const verificationRetry = useRef<{ candidateId: string; key: string } | null>(
    null,
  );
  const admissionRetry = useRef<{ candidateId: string; key: string } | null>(
    null,
  );
  const proposalReviewInFlight = useRef(new Set<string>());
  const proposalReviewRetry = useRef(new Map<string, { body: string; key: string }>());
  const replayReportRequest = useRef<AbortController | null>(null);
  const bundleExportRequest = useRef<AbortController | null>(null);

  const eligibleProblems = useMemo(
    () => problems.filter((problem) => problem.allowsMix),
    [problems],
  );
  const eligibleMappings = useMemo(
    () =>
      mappings.filter(
        (mapping) =>
          mapping.status === 'compatible' &&
          mapping.freshness === 'current' &&
          mapping.isReviewed &&
          mapping.producer.symbolId !== null &&
          mapping.consumer.symbolId !== null,
      ),
    [mappings],
  );
  const selectedSpec =
    eligibleProblems.find((problem) => problem.specId === specId) ??
    eligibleProblems[0];
  const selectedMapping =
    eligibleMappings.find((mapping) => mapping.mappingId === mappingId) ??
    eligibleMappings[0];
  const reportAttempt =
    receipt &&
    replayReportAttempt?.candidateId === receipt.candidateId &&
    replayReportAttempt.activityId === receipt.activityId
      ? replayReportAttempt
      : null;
  const bundleAttempt =
    receipt &&
    bundleExportAttempt?.candidateId === receipt.candidateId &&
    bundleExportAttempt.activityId === receipt.activityId
      ? bundleExportAttempt
      : null;
  const parsedWeight = weight.trim() === '' ? Number.NaN : Number(weight);
  const canCompile = Boolean(
    selectedSpec &&
    selectedMapping &&
    Number.isFinite(parsedWeight) &&
    parsedWeight >= 0 &&
    parsedWeight <= 1 &&
    !busy &&
    !researchCaseBusy,
  );
  const canLower = Boolean(
    receipt &&
    receipt.operator === 'mix_positive_feature_maps' &&
    eligibleProblems.some((problem) => problem.specId === receipt.specId) &&
    eligibleMappings.some(
      (mapping) => mapping.mappingId === receipt.mappingId,
    ) &&
    !busy &&
    !researchCaseBusy,
  );
  const canRunResearchCase = Boolean(
    receipt &&
    receipt.operator === 'lower_mixture_to_concatenation' &&
    !receipt.researchCase &&
    !busy &&
    !researchCaseBusy,
  );

  useEffect(() => {
    const controller = new AbortController();
    void (async () => {
      try {
        const response = await fetch(
          '/api/research/problems?limit=100&offset=0',
          { signal: controller.signal },
        );
        if (!response.ok) throw new Error('ProblemSpecs unavailable.');
        const result = parseProblemList(await response.json());
        if (controller.signal.aborted) return;
        setProblems(result.items);
        setPartialProblems(result.partial);
        setProblemState('loaded');
        setSpecId(
          (current) =>
            current ||
            result.items.find((item) => item.allowsMix)?.specId ||
            '',
        );
        setNotice(
          result.items.length
            ? 'Frozen ProblemSpecs loaded.'
            : 'No frozen ProblemSpecs in this workspace.',
        );
      } catch {
        if (!controller.signal.aborted) {
          setProblemState('unavailable');
          setNotice(
            'Research records could not be loaded. Refresh the page before compiling; no candidate has been created.',
          );
        }
      }
    })();
    return () => controller.abort();
  }, []);

  useEffect(() => {
    if (!dialogOpen) return;
    const controller = new AbortController();
    void (async () => {
      try {
        const response = await fetch(
          `/api/research/candidates?limit=20&offset=${candidateHistoryOffset}`,
          { signal: controller.signal },
        );
        if (!response.ok) throw new Error('Candidate history unavailable.');
        const page = parseCandidateHistory(await response.json());
        if (controller.signal.aborted) return;
        setCandidateHistory((current) => {
          if (candidateHistoryOffset === 0) return page.items;
          const known = new Map(
            current.map((item) => [item.candidateId, item]),
          );
          for (const item of page.items) known.set(item.candidateId, item);
          return [...known.values()];
        });
        setCandidateHistoryTotal(page.total);
        setCandidateHistoryState('loaded');
      } catch {
        if (!controller.signal.aborted) setCandidateHistoryState('unavailable');
      }
    })();
    return () => controller.abort();
  }, [candidateHistoryOffset, candidateHistoryRevision, dialogOpen]);

  useEffect(
    () => () => {
      replayReportRequest.current?.abort();
      bundleExportRequest.current?.abort();
    },
    [dialogOpen, receipt?.candidateId, receipt?.activityId],
  );

  useEffect(() => {
    if (!dialogOpen) return;
    const controller = new AbortController();
    void (async () => {
      try {
        const response = await fetch('/api/research/proposals?limit=20&offset=0', {
          signal: controller.signal,
        });
        if (!response.ok) throw new Error('Proposal history unavailable.');
        const page = parseProposalHistory(await response.json());
        if (controller.signal.aborted) return;
        setProposals(page.items);
        setProposalTotal(page.total);
        setProposalState('loaded');
      } catch {
        if (!controller.signal.aborted) setProposalState('unavailable');
      }
    })();
    return () => controller.abort();
  }, [dialogOpen, proposalRevision]);

  async function reviewProposal(
    proposal: ProposalView,
    decision: 'accept_for_compilation' | 'reject',
  ) {
    const notes = proposalReviewNotes[proposal.proposalId]?.trim() ?? '';
    if (
      !notes ||
      proposal.review ||
      proposalReviewInFlight.current.has(proposal.proposalId)
    ) return;
    const body = JSON.stringify({ decision, notes });
    const previous = proposalReviewRetry.current.get(proposal.proposalId);
    const request = previous?.body === body
      ? previous
      : { body, key: crypto.randomUUID() };
    proposalReviewRetry.current.set(proposal.proposalId, request);
    proposalReviewInFlight.current.add(proposal.proposalId);
    setProposalReviewBusy(proposal.proposalId);
    setProposalReviewNotices((current) => ({
      ...current,
      [proposal.proposalId]: 'Saving human review…',
    }));
    try {
      const response = await fetch(
        `/api/research/proposals/${proposal.proposalId}/reviews`,
        {
          method: 'POST',
          headers: {
            'content-type': 'application/json',
            'x-idempotency-key': request.key,
          },
          body,
        },
      );
      const result: unknown = await response.json().catch(() => null);
      if (!response.ok || !record(result) || !record(result.review)) {
        const detail = record(result) && typeof result.detail === 'string'
          ? result.detail
          : record(result) && typeof result.code === 'string'
            ? result.code
            : `Review request rejected (${response.status}).`;
        throw new Error(detail);
      }
      const raw = result.review;
      if (
        raw.proposal_id !== proposal.proposalId ||
        raw.proposal_hash !== proposal.contentHash ||
        raw.decision !== decision ||
        typeof raw.review_id !== 'string' ||
        typeof raw.reviewer_id !== 'string' ||
        (raw.reviewer_role !== 'researcher' &&
          raw.reviewer_role !== 'reviewer' &&
          raw.reviewer_role !== 'admin') ||
        typeof raw.reviewed_at !== 'string' ||
        typeof raw.notes !== 'string'
      ) throw new Error('The review receipt did not match the selected proposal.');
      const review: ProposalReviewView = {
        reviewId: raw.review_id,
        proposalId: raw.proposal_id,
        proposalHash: raw.proposal_hash,
        reviewerId: raw.reviewer_id,
        reviewerRole: raw.reviewer_role,
        decision,
        notes: raw.notes,
        reviewedAt: raw.reviewed_at,
      };
      proposalReviewRetry.current.delete(proposal.proposalId);
      setProposals((current) => current.map((item) =>
        item.proposalId === proposal.proposalId ? { ...item, review } : item,
      ));
      setProposalReviewNotices((current) => ({
        ...current,
        [proposal.proposalId]: decision === 'accept_for_compilation'
          ? 'Accepted for compilation only; no mathematical claim was verified.'
          : 'Proposal rejected. This decision is final for this proposal.',
      }));
    } catch (error) {
      setProposalReviewNotices((current) => ({
        ...current,
        [proposal.proposalId]: error instanceof Error
          ? `${error.message} Your rationale is preserved; retrying unchanged will reuse its request key.`
          : 'Review was not saved. Your rationale is preserved; retry unchanged.',
      }));
    } finally {
      proposalReviewInFlight.current.delete(proposal.proposalId);
      setProposalReviewBusy(null);
    }
  }

  async function compile(parent?: CandidateReceipt, proposal?: ProposalView) {
    const activeSpec = proposal
      ? problems.find((problem) => problem.specId === proposal.specId)
      : parent
      ? eligibleProblems.find((problem) => problem.specId === parent.specId)
      : selectedSpec;
    const proposalBindings = proposal && record(proposal.transform.bindings)
      ? proposal.transform.bindings
      : null;
    const proposalMixtureParent =
      proposalBindings && typeof proposalBindings.mixture === 'string'
        ? candidateHistory.find((candidate) =>
            candidate.candidateId === proposalBindings.mixture,
          )
        : undefined;
    const activeMapping = proposal
      ? proposal.operator === 'mix_positive_feature_maps' && proposalBindings &&
        typeof proposalBindings.left === 'string' &&
        typeof proposalBindings.right === 'string'
        ? eligibleMappings.find((mapping) =>
            mapping.producer.symbolId === proposalBindings.left &&
            mapping.consumer.symbolId === proposalBindings.right,
          )
        : proposal.operator === 'lower_mixture_to_concatenation'
          ? eligibleMappings.find(
              (mapping) => mapping.mappingId === proposalMixtureParent?.mappingId,
            )
          : undefined
      : parent
      ? eligibleMappings.find(
          (mapping) => mapping.mappingId === parent.mappingId,
        )
      : selectedMapping;
    if (
      inFlight.current ||
      busy ||
      !activeSpec ||
      !activeMapping ||
      (proposal && proposal.review?.decision !== 'accept_for_compilation') ||
      (!parent && !proposal &&
        (!Number.isFinite(parsedWeight) ||
          parsedWeight < 0 ||
          parsedWeight > 1))
    ) {
      if (proposal && (!activeSpec || !activeMapping)) {
        setProposalReviewNotices((current) => ({
          ...current,
          [proposal.proposalId]: 'This exact proposal has no current matching ProblemSpec or reviewed mapping; refresh records before compiling.',
        }));
      }
      return;
    }
    inFlight.current = true;
    setBusy(true);
    let savedCandidate = false;
    const operator = proposal?.operator ?? (parent
      ? 'lower_mixture_to_concatenation'
      : 'mix_positive_feature_maps');
    const transform = proposal
      ? proposal.transform
      : parent
      ? {
          operator,
          operator_version: '1',
          target_node_id: parent.candidateId,
          parameters: {},
          bindings: { mixture: parent.candidateId },
        }
      : {
          operator,
          operator_version: '1',
          target_node_id: activeMapping.producer.equationId,
          parameters: { lambda: parsedWeight },
          bindings: {
            left: activeMapping.producer.symbolId,
            right: activeMapping.consumer.symbolId,
          },
        };
    const body = JSON.stringify({
      spec_id: activeSpec.specId,
      mapping_id: activeMapping.mappingId,
      transform,
      ...(proposal ? { proposal_id: proposal.proposalId } : {}),
      ...((proposal?.operator === 'lower_mixture_to_concatenation' &&
        typeof proposal.transform.target_node_id === 'string')
        ? { parent_candidate_id: proposal.transform.target_node_id }
        : parent ? { parent_candidate_id: parent.candidateId } : {}),
    });
    const request =
      retry.current?.body === body
        ? retry.current
        : { body, key: crypto.randomUUID() };
    retry.current = request;
    try {
      const response = await fetch('/api/research/candidates/compile', {
        method: 'POST',
        headers: {
          'content-type': 'application/json',
          'x-idempotency-key': request.key,
        },
        body,
      });
      if (!response.ok) {
        const error: unknown = await response.json().catch(() => null);
        const reason =
          record(error) && typeof error.detail === 'string'
            ? error.detail
            : `Compiler request rejected (${response.status}).`;
        throw new Error(reason);
      }
      const compiled = parseReceipt(await response.json());
      const savedReceipt: CandidateReceipt = {
        ...compiled,
        specId: activeSpec.specId,
        mappingId: activeMapping.mappingId,
      };
      savedCandidate = true;
      setReceipt(savedReceipt);
      retry.current = null;
      setNotice(
        'Candidate recorded. Running its independent, scoped DSL check…',
      );
      const verificationRequest =
        verificationRetry.current?.candidateId === savedReceipt.candidateId
          ? verificationRetry.current
          : { candidateId: savedReceipt.candidateId, key: crypto.randomUUID() };
      verificationRetry.current = verificationRequest;
      let check: CandidateCheckReceipt;
      try {
        const checkResponse = await fetch(
          `/api/research/candidates/${savedReceipt.candidateId}/verify`,
          {
            method: 'POST',
            headers: {
              'content-type': 'application/json',
              'x-idempotency-key': verificationRequest.key,
            },
            body: JSON.stringify({}),
          },
        );
        if (!checkResponse.ok)
          throw new Error('Independent checker unavailable.');
        check = parseCandidateCheck(
          await checkResponse.json(),
          savedReceipt.candidateId,
        );
        verificationRetry.current = null;
      } catch {
        setNotice(
          'Candidate is saved, but its independent check is unavailable. No admission decision or run was requested.',
        );
        return;
      }
      const checkedReceipt = { ...savedReceipt, check };
      setReceipt(checkedReceipt);
      setNotice(
        'Scoped check recorded. Checking the numerical admission policy…',
      );
      const admissionRequest =
        admissionRetry.current?.candidateId === savedReceipt.candidateId
          ? admissionRetry.current
          : { candidateId: savedReceipt.candidateId, key: crypto.randomUUID() };
      admissionRetry.current = admissionRequest;
      try {
        const admissionResponse = await fetch(
          `/api/research/candidates/${savedReceipt.candidateId}/admission`,
          {
            method: 'POST',
            headers: {
              'content-type': 'application/json',
              'x-idempotency-key': admissionRequest.key,
            },
            body: JSON.stringify({ action: 'can_run_numerical' }),
          },
        );
        if (!admissionResponse.ok)
          throw new Error('Policy decision unavailable.');
        const admission = parseAdmission(await admissionResponse.json());
        setReceipt({ ...checkedReceipt, admission });
        admissionRetry.current = null;
        setNotice(
          admission.allowed
            ? 'Policy allows a numerical run; no numerical suite or experiment has run yet.'
            : 'Policy denied numerical evaluation. No numerical suite or experiment was started.',
        );
      } catch {
        setNotice(
          'Candidate is saved, but its admission decision is unavailable. No numerical or empirical run was started.',
        );
      }
    } catch (error) {
      setNotice(
        `Candidate not confirmed; retry preserves this request. ${error instanceof Error ? error.message : 'Try again.'}`,
      );
    } finally {
      inFlight.current = false;
      setBusy(false);
      if (savedCandidate) {
        setCandidateHistoryOffset(0);
        setCandidateHistoryRevision((revision) => revision + 1);
      }
    }
  }

  async function runResearchCase() {
    const activeReceipt = receipt;
    if (
      !activeReceipt ||
      activeReceipt.operator !== 'lower_mixture_to_concatenation' ||
      activeReceipt.researchCase ||
      busy ||
      researchCaseBusy ||
      researchCaseInFlight.current
    )
      return;
    researchCaseInFlight.current = true;
    setResearchCaseBusy(true);
    const idempotencyKey = `research-case:${activeReceipt.candidateId}`;
    try {
      const response = await fetch(
        `/api/research/candidates/${activeReceipt.candidateId}/research-case`,
        {
          method: 'POST',
          headers: {
            'content-type': 'application/json',
            'x-idempotency-key': idempotencyKey,
          },
          body: JSON.stringify({}),
        },
      );
      if (!response.ok) {
        const error: unknown = await response.json().catch(() => null);
        const detail =
          record(error) && typeof error.code === 'string'
            ? error.code.replaceAll('_', ' ').toLowerCase()
            : `server returned ${response.status}`;
        throw new Error(detail);
      }
      const result = parseResearchCase(
        await response.json(),
        activeReceipt.candidateId,
        activeReceipt.specId,
      );
      setReceipt((current) =>
        current?.candidateId === activeReceipt.candidateId
          ? { ...current, researchCase: result }
          : current,
      );
      setNotice(
        `Frozen CPU research case recorded (${result.outcome}). It is synthetic protocol evidence only; no product-performance claim was made.`,
      );
      setCandidateHistoryOffset(0);
      setCandidateHistoryRevision((revision) => revision + 1);
    } catch (error) {
      setNotice(
        `Research case unavailable; no result was confirmed. ${error instanceof Error ? error.message : 'Retry is safe.'}`,
      );
    } finally {
      researchCaseInFlight.current = false;
      setResearchCaseBusy(false);
    }
  }

  async function loadReplayReport() {
    const activeReceipt = receipt;
    if (!activeReceipt) return;
    replayReportRequest.current?.abort();
    const controller = new AbortController();
    replayReportRequest.current = controller;
    setReplayReportAttempt({
      candidateId: activeReceipt.candidateId,
      activityId: activeReceipt.activityId,
      state: 'loading',
    });
    try {
      const response = await fetch(
        `/api/research/candidates/${activeReceipt.candidateId}/activities/${activeReceipt.activityId}/report`,
        { cache: 'no-store', signal: controller.signal },
      );
      if (!response.ok) throw new Error('Replay report unavailable.');
      const report = parseReplayReport(
        await response.json(),
        activeReceipt.candidateId,
        activeReceipt.activityId,
      );
      if (!controller.signal.aborted) {
        setReplayReportAttempt({
          candidateId: activeReceipt.candidateId,
          activityId: activeReceipt.activityId,
          state: 'loaded',
          report,
        });
      }
    } catch {
      if (!controller.signal.aborted) {
        setReplayReportAttempt({
          candidateId: activeReceipt.candidateId,
          activityId: activeReceipt.activityId,
          state: 'unavailable',
        });
      }
    }
  }

  async function downloadReplayBundle() {
    const activeReceipt = receipt;
    const activeReport = reportAttempt?.report;
    if (!activeReceipt || !activeReport) return;

    bundleExportRequest.current?.abort();
    const controller = new AbortController();
    bundleExportRequest.current = controller;
    setBundleExportAttempt({
      candidateId: activeReceipt.candidateId,
      activityId: activeReceipt.activityId,
      state: 'loading',
    });
    try {
      const query = new URLSearchParams({ bundle_hash: activeReport.bundleHash });
      const response = await fetch(
        `/api/research/candidates/${activeReceipt.candidateId}/activities/${activeReceipt.activityId}/bundle?${query}`,
        { cache: 'no-store', signal: controller.signal },
      );
      if (!response.ok || !response.headers.get('content-type')?.includes('application/json')) {
        throw new Error('Replay bundle unavailable.');
      }
      const raw = await readBoundedResponseText(response);
      validateReplayBundleExport(
        JSON.parse(raw),
        activeReceipt.candidateId,
        activeReceipt.activityId,
        activeReport.bundleHash,
      );
      if (controller.signal.aborted) return;

      const objectUrl = URL.createObjectURL(
        new Blob([raw], { type: 'application/json' }),
      );
      const link = document.createElement('a');
      link.href = objectUrl;
      link.download = `fgl-${activeReceipt.candidateId}-${activeReceipt.activityId}-replay-bundle.json`;
      link.hidden = true;
      document.body.appendChild(link);
      link.click();
      link.remove();
      const revoke = URL.revokeObjectURL.bind(URL);
      window.setTimeout(() => revoke(objectUrl), 0);
      setBundleExportAttempt({
        candidateId: activeReceipt.candidateId,
        activityId: activeReceipt.activityId,
        state: 'downloaded',
      });
    } catch {
      if (!controller.signal.aborted) {
        setBundleExportAttempt({
          candidateId: activeReceipt.candidateId,
          activityId: activeReceipt.activityId,
          state: 'unavailable',
        });
      }
    }
  }

  const blockedReason =
    problemState === 'loading' || mappingsState === 'loading'
      ? 'Loading frozen objectives and reviewed mappings…'
      : problemState === 'unavailable' || mappingsState === 'unavailable'
        ? 'Research records are unavailable. Reconnect before compiling; no candidate has been created.'
        : !eligibleProblems.length
          ? 'Freeze a ProblemSpec that explicitly allows mix_positive_feature_maps v1.'
          : !eligibleMappings.length
            ? mappingsPartial
              ? 'No eligible mapping in the first 100 records. Review the partial compatibility list before adding another mapping.'
              : 'Create and review a current compatible mapping with both scoped symbols resolved.'
            : null;

  function toggleComparison(candidate: CandidateReceipt) {
    setComparison((current) => {
      const selected = current.some(
        (item) => item.candidateId === candidate.candidateId,
      );
      if (selected)
        return current.filter(
          (item) => item.candidateId !== candidate.candidateId,
        );
      return current.length < 2 ? [...current, candidate] : current;
    });
  }

  return (
    <section className="research-move-panel">
      <Dialog open={dialogOpen} onOpenChange={setDialogOpen}>
        <DialogTrigger className="research-move-trigger">
          <span>
            <strong>Create research candidate</strong>
            <small>Deterministic DSL · no model judgment</small>
          </span>
          <span className="research-move-summary-state">
            {receipt
              ? receipt.admission?.allowed
                ? 'Policy allows · run not started'
                : receipt.admission
                  ? 'Policy denied · no run'
                  : receipt.check
                    ? `Check: ${receipt.check.outcome}`
                    : 'Not verified'
              : 'No candidate'}
          </span>
        </DialogTrigger>
        <DialogContent className="research-move-dialog">
          <DialogHeader className="research-move-dialog-header">
            <DialogTitle>Create research candidate</DialogTitle>
            <DialogDescription>
              Explore one bounded transformation. The compiler records a
              hypothesis; independent checks and policy decisions remain
              separate.
            </DialogDescription>
          </DialogHeader>
          <div className="research-move-content">
            <p className="research-move-intro">
              Compile a positive feature-map mixture from a frozen objective and
              reviewed ports. This records a hypothesis; it does not prove
              equivalence or run an experiment.
            </p>
            <form
              onSubmit={(event) => {
                event.preventDefault();
                void compile();
              }}
              className="research-move-form"
            >
              <label>
                Frozen research question
                <select
                  aria-label="Frozen research question"
                  value={selectedSpec?.specId ?? ''}
                  onChange={(event) => setSpecId(event.target.value)}
                  disabled={!eligibleProblems.length || busy}
                >
                  {!eligibleProblems.length ? (
                    <option value="">No eligible frozen ProblemSpec</option>
                  ) : null}
                  {eligibleProblems.map((problem) => (
                    <option key={problem.specId} value={problem.specId}>
                      v{problem.version} · {problem.task}
                    </option>
                  ))}
                </select>
              </label>
              <label>
                Reviewed compatible mapping
                <select
                  aria-label="Reviewed compatible mapping"
                  value={selectedMapping?.mappingId ?? ''}
                  onChange={(event) => setMappingId(event.target.value)}
                  disabled={!eligibleMappings.length || busy}
                >
                  {!eligibleMappings.length ? (
                    <option value="">No current reviewed mapping</option>
                  ) : null}
                  {eligibleMappings.map((mapping) => (
                    <option key={mapping.mappingId} value={mapping.mappingId}>
                      {mapping.producer.symbol} ({mapping.producer.domain},{' '}
                      {mapping.producer.shape}) → {mapping.consumer.symbol} (
                      {mapping.consumer.domain}, {mapping.consumer.shape})
                    </option>
                  ))}
                </select>
              </label>
              <label className="research-move-weight">
                Mixture weight λ
                <input
                  id="research-move-weight"
                  aria-label="Mixture weight lambda"
                  type="number"
                  min="0"
                  max="1"
                  step="0.05"
                  required
                  value={weight}
                  onChange={(event) => setWeight(event.target.value)}
                  disabled={busy}
                />
                <small>
                  Must remain in [0, 1]. Output rank is the sum of the reviewed
                  feature ranks.
                </small>
              </label>
              {selectedMapping &&
              Number.isFinite(parsedWeight) &&
              parsedWeight >= 0 &&
              parsedWeight <= 1 ? (
                <section
                  className="research-move-preview"
                  aria-label="Transformation preview"
                >
                  <div>
                    <p>Hypothesis preview</p>
                    <code>
                      κmix(q,k) = {parsedWeight.toFixed(2)} ·
                      ⟨φleft(q),φleft(k)⟩ + {(1 - parsedWeight).toFixed(2)} ·
                      ⟨φright(q),φright(k)⟩
                    </code>
                  </div>
                  <p>
                    Uses the reviewed ports{' '}
                    <code>{selectedMapping.producer.symbol}</code> and{' '}
                    <code>{selectedMapping.consumer.symbol}</code>. This bounded
                    compile/check does not change source formulas or start a
                    numerical or empirical run; results may remain unknown or
                    conditional.
                  </p>
                  <p>
                    Estimated cost: lightweight deterministic CPU checks only;
                    no AI model, benchmark, or training compute.
                  </p>
                </section>
              ) : null}
              {blockedReason ? (
                <output className="research-move-blocked">
                  {blockedReason}
                </output>
              ) : null}
              <button type="submit" disabled={!canCompile}>
                {busy ? 'Compiling…' : 'Compile hypothesis'}
              </button>
            </form>
            <section
              className="research-candidate-history"
              aria-label="Saved AI proposals"
            >
              <header>
                <div>
                  <strong>AI proposals</strong>
                  <small aria-live="polite">
                    {proposalState === 'loaded'
                      ? `${proposalTotal} recorded · unreviewed drafts`
                      : proposalState === 'loading'
                        ? 'Loading proposal history…'
                        : 'Proposal history unavailable'}
                  </small>
                </div>
                <button
                  type="button"
                  className="research-history-refresh"
                  disabled={proposalState === 'loading'}
                  onClick={() => {
                    setProposalState('loading');
                    setProposalRevision((revision) => revision + 1);
                  }}
                >
                  Refresh
                </button>
              </header>
              <ProposalGenerationForm
                spec={selectedSpec}
                onCreated={() => {
                  setProposalState('loading');
                  setProposalRevision((revision) => revision + 1);
                }}
              />
              {proposalState === 'loading' ? (
                <output aria-live="polite">Loading saved AI proposals…</output>
              ) : proposalState === 'unavailable' ? (
                <output aria-live="polite">
                  Proposal history is unavailable. Existing candidates and checks are unchanged.
                </output>
              ) : proposals.length === 0 ? (
                <output aria-live="polite">
                  No saved proposals yet. Generated drafts will appear here for human review.
                </output>
              ) : (
                <ul>
                  {proposals.map((proposal) => (
                    <li key={proposal.proposalId}>
                      <article>
                        <p>
                          <strong>
                            {proposal.review?.decision === 'accept_for_compilation'
                              ? 'Human accepted for compilation only'
                              : proposal.review?.decision === 'reject'
                                ? 'Human rejected · final for this proposal'
                                : 'AI-proposed · pending human review'}
                          </strong>{' '}
                          <code>{proposal.proposalId}</code>
                        </p>
                        <p>
                          {proposal.operator} v{proposal.operatorVersion} ·{' '}
                          {proposal.semantics.replaceAll('_', ' ')}
                        </p>
                        <p>ProblemSpec: <code>{proposal.specId}</code></p>
                        <p>Parents: {proposal.parents.map((parent) => <code key={parent}>{parent} </code>)}</p>
                        <details>
                          <summary>Proposal rationale, assumptions, and source IDs</summary>
                          <p>{proposal.rationale}</p>
                          <p>Expected effect: {proposal.expectedEffect}</p>
                          <p>Proposed assumptions — not discharged:</p>
                          {proposal.assumptions.length ? (
                            <ul>{proposal.assumptions.map((assumption) => <li key={assumption}>{assumption}</li>)}</ul>
                          ) : <p>None stated.</p>}
                          <p>Source evidence:</p>
                          <ul>
                            {proposal.sourceRefs.map((source) => (
                              <ProposalSourceEvidence key={source.id} source={source} />
                            ))}
                          </ul>
                          <pre>{JSON.stringify(proposal.transform, null, 2)}</pre>
                        </details>
                        <p>
                          This is an untrusted proposal, not a candidate or verification result. It does not alter policy or authorize a run.
                        </p>
                        <p>
                          No calibrated confidence score is available; use the separate scoped checker and review the cited sources.
                        </p>
                        {proposal.review ? (
                          <p>
                            Review by {proposal.review.reviewerRole} ·{' '}
                            {proposal.review.reviewedAt}: {proposal.review.notes}
                          </p>
                        ) : (
                          <div className="research-proposal-review">
                            <label>
                              Human review rationale
                              <textarea
                                value={proposalReviewNotes[proposal.proposalId] ?? ''}
                                maxLength={2000}
                                onChange={(event) => setProposalReviewNotes((current) => ({
                                  ...current,
                                  [proposal.proposalId]: event.target.value,
                                }))}
                                disabled={proposalReviewBusy === proposal.proposalId}
                              />
                            </label>
                            <div>
                              <button
                                type="button"
                                disabled={!proposalReviewNotes[proposal.proposalId]?.trim() || proposalReviewBusy !== null}
                                onClick={() => void reviewProposal(proposal, 'accept_for_compilation')}
                              >
                                Accept for compilation only
                              </button>
                              <button
                                type="button"
                                disabled={!proposalReviewNotes[proposal.proposalId]?.trim() || proposalReviewBusy !== null}
                                onClick={() => void reviewProposal(proposal, 'reject')}
                              >
                                Reject proposal
                              </button>
                            </div>
                          </div>
                        )}
                        {proposal.review?.decision === 'accept_for_compilation' ? (
                          <button
                            type="button"
                            disabled={busy || proposalReviewBusy !== null}
                            onClick={() => void compile(undefined, proposal)}
                          >
                            Compile this stored proposal
                          </button>
                        ) : null}
                        {proposalReviewNotices[proposal.proposalId] ? (
                          <output aria-live="polite">
                            {proposalReviewNotices[proposal.proposalId]}
                          </output>
                        ) : null}
                      </article>
                    </li>
                  ))}
                </ul>
              )}
              {proposalState === 'loaded' && proposalTotal > proposals.length ? (
                <p className="research-move-note">
                  Showing the newest {proposals.length} of {proposalTotal} proposals.
                </p>
              ) : null}
            </section>
            {partialProblems ? (
              <p className="research-move-note">
                Showing the newest 100 ProblemSpecs. Open Problem Spec history
                for earlier versions.
              </p>
            ) : null}
            <section
              className="research-candidate-history"
              aria-label="Saved candidate history"
            >
              <header>
                <div>
                  <strong>Saved candidates</strong>
                  <small aria-live="polite">
                    {candidateHistoryTotal} recorded · {comparison.length}/2
                    selected to compare
                  </small>
                </div>
                <button
                  type="button"
                  className="research-history-refresh"
                  disabled={candidateHistoryState === 'loading'}
                  onClick={() => {
                    setCandidateHistoryOffset(0);
                    setCandidateHistoryRevision((revision) => revision + 1);
                  }}
                >
                  Refresh
                </button>
              </header>
              {candidateHistoryState === 'loading' ? (
                <output aria-live="polite">Loading saved candidates…</output>
              ) : candidateHistoryState === 'unavailable' ? (
                <output aria-live="polite">
                  Saved history is unavailable. No candidate was created by this
                  request.
                </output>
              ) : candidateHistory.length === 0 ? (
                <output aria-live="polite">No saved candidates yet.</output>
              ) : (
                <ul>
                  {candidateHistory.map((item) => (
                    <li
                      key={item.candidateId}
                      className="research-candidate-row"
                    >
                      <button
                        type="button"
                        className="research-candidate-open"
                        aria-pressed={receipt?.candidateId === item.candidateId}
                        disabled={busy}
                        onClick={() => {
                          setReceipt(item);
                          setSpecId(item.specId);
                          if (item.mappingId) setMappingId(item.mappingId);
                          setNotice(
                            'Loaded saved candidate history. No new check, admission decision, or run was started.',
                          );
                        }}
                      >
                        <span>
                          <strong>{item.operator.replaceAll('_', ' ')}</strong>
                          <small>
                            {item.semantics.replaceAll('_', ' ')} ·{' '}
                            {item.candidateId}
                          </small>
                          <time dateTime={item.createdAt}>
                            Recorded{' '}
                            {item.createdAt
                              .replace('T', ' ')
                              .replace(/\.\d{3}Z$/, ' UTC')}
                          </time>
                          {item.sourceProposalId ? (
                            <small>Compiled from proposal {item.sourceProposalId}</small>
                          ) : null}
                        </span>
                        <span>
                          {item.check
                            ? `Check: ${item.check.outcome}`
                            : 'No check'}
                          {' · '}
                          {item.admission
                            ? `Policy recorded: ${item.admission.outcome}`
                            : 'No policy decision'}
                          {item.numericalFixture
                            ? ` · Fixture: ${item.numericalFixture.outcome}`
                            : ''}
                        </span>
                      </button>
                      <button
                        type="button"
                        className="research-candidate-compare-toggle"
                        aria-pressed={comparison.some(
                          (selected) =>
                            selected.candidateId === item.candidateId,
                        )}
                        aria-label={`${comparison.some((selected) => selected.candidateId === item.candidateId) ? 'Remove' : 'Add'} ${item.operator.replaceAll('_', ' ')} candidate ${item.candidateId} to comparison`}
                        disabled={
                          comparison.length >= 2 &&
                          !comparison.some(
                            (selected) =>
                              selected.candidateId === item.candidateId,
                          )
                        }
                        onClick={() => toggleComparison(item)}
                      >
                        {comparison.some(
                          (selected) =>
                            selected.candidateId === item.candidateId,
                        )
                          ? 'Selected'
                          : 'Compare'}
                      </button>
                    </li>
                  ))}
                </ul>
              )}
              {candidateHistoryState === 'loaded' &&
              candidateHistory.length < candidateHistoryTotal ? (
                <button
                  type="button"
                  className="research-history-more"
                  disabled={busy}
                  onClick={() =>
                    setCandidateHistoryOffset(candidateHistory.length)
                  }
                >
                  Load older candidates
                </button>
              ) : null}
              {comparison.length ? (
                <section
                  className="research-candidate-comparison"
                  aria-label="Candidate comparison"
                >
                  <header>
                    <div>
                      <strong>Evidence comparison</strong>
                      <small>
                        Recorded structure and checks · no quality ranking
                      </small>
                    </div>
                    <button type="button" onClick={() => setComparison([])}>
                      Clear
                    </button>
                  </header>
                  {comparison.length === 1 ? (
                    <p>
                      Select one more saved candidate to compare its lineage and
                      recorded evidence.
                    </p>
                  ) : null}
                  <div className="research-candidate-comparison-grid">
                    {comparison.map((item, index) => (
                      <article
                        key={item.candidateId}
                        aria-label={`Candidate ${index + 1}`}
                      >
                        <header>
                          <div>
                            <p>Candidate {index + 1}</p>
                            <code>{item.candidateId}</code>
                          </div>
                          <span className="research-candidate-semantics">
                            {item.semantics.replaceAll('_', ' ')}
                          </span>
                        </header>
                        <section
                          className="research-evidence-summary"
                          aria-label="Evidence status"
                        >
                          {comparisonEvidence(item).map((status) => (
                            <div key={status.label} data-state={status.state}>
                              <small>{status.label}</small>
                              <strong>{status.value}</strong>
                            </div>
                          ))}
                        </section>
                        <dl>
                          <div>
                            <dt>Transformation</dt>
                            <dd>{item.operator.replaceAll('_', ' ')}</dd>
                          </div>
                          <div>
                            <dt>Frozen ProblemSpec</dt>
                            <dd>{item.specId}</dd>
                          </div>
                          <div>
                            <dt>Reviewed mapping</dt>
                            <dd>{item.mappingId ?? 'Not recorded'}</dd>
                          </div>
                          <div>
                            <dt>Parents</dt>
                            <dd>
                              {item.parents.length
                                ? item.parents.map((parent) => (
                                    <code key={parent}>{parent}</code>
                                  ))
                                : 'None recorded'}
                            </dd>
                          </div>
                          <div>
                            <dt>Open obligations</dt>
                            <dd>
                              {item.obligations.length ? (
                                <ul className="research-obligation-list">
                                  {item.obligations.map((entry) => (
                                    <li key={entry.name}>
                                      <span>
                                        {entry.name.replaceAll('_', ' ')}
                                      </span>
                                      <span data-state={entry.status}>
                                        {entry.status.replaceAll('_', ' ')}
                                      </span>
                                    </li>
                                  ))}
                                </ul>
                              ) : (
                                'None recorded'
                              )}
                            </dd>
                          </div>
                          {item.check ? (
                            <div>
                              <dt>Check scope</dt>
                              <dd>
                                {item.check.scope.replaceAll('_', ' ')} · domain{' '}
                                {item.check.domain.replaceAll('_', ' ')}
                              </dd>
                            </div>
                          ) : null}
                          {item.admission ? (
                            <div>
                              <dt>Recorded policy rule</dt>
                              <dd>{item.admission.ruleId}</dd>
                            </div>
                          ) : null}
                        </dl>
                      </article>
                    ))}
                  </div>
                  {new Set(comparison.map((item) => item.specId)).size > 1 ? (
                    <p role="note">
                      Different frozen ProblemSpecs are selected. Their outcomes
                      are not directly comparable across objectives.
                    </p>
                  ) : null}
                  <p>
                    These records do not establish model quality, latency,
                    memory, or a winning candidate.
                  </p>
                </section>
              ) : null}
            </section>
            <output className="research-move-notice" aria-live="polite">
              {notice}
            </output>
            {receipt ? (
              <section
                className="research-candidate-receipt"
                aria-label="Compiled candidate receipt"
              >
                <header>
                  <div>
                    <p>Candidate · {receipt.semantics.replaceAll('_', ' ')}</p>
                    <code>{receipt.candidateId}</code>
                  </div>
                  <span
                    className="research-evidence-badge"
                    data-state={checkSummary(receipt.check).state}
                  >
                    {checkSummary(receipt.check).label}
                  </span>
                </header>
                <p>
                  Recorded{' '}
                  <time dateTime={receipt.createdAt}>
                    {receipt.createdAt
                      .replace('T', ' ')
                      .replace(/\.\d{3}Z$/, ' UTC')}
                  </time>
                </p>
                <p>
                  Operator: <code>{receipt.operator}</code> · activity{' '}
                  <code>{receipt.activityId}</code>
                </p>
                {receipt.sourceProposalId ? (
                  <p>Compiled from AI proposal <code>{receipt.sourceProposalId}</code>.</p>
                ) : null}
                <p>
                  Parents:{' '}
                  {receipt.parents.map((parentId) => (
                    <code key={parentId}>{parentId}</code>
                  ))}
                </p>
                {receipt.admission ? (
                  <section aria-label="Numerical admission decision">
                    <p>
                      Stored policy decision: {receipt.admission.outcome} ·{' '}
                      <code>{receipt.admission.ruleId}</code> · decision{' '}
                      <code>{receipt.admission.decisionId}</code>
                    </p>
                    <p>
                      Evidence receipts:{' '}
                      {receipt.admission.inputResultIds.length
                        ? receipt.admission.inputResultIds.map((id) => (
                            <code key={id}>{id} </code>
                          ))
                        : 'none'}
                    </p>
                    <ul>
                      {receipt.admission.reasons.map((reason) => (
                        <li key={reason}>{admissionReason(reason)}</li>
                      ))}
                    </ul>
                  </section>
                ) : (
                  <p>
                    Admission decision is unavailable. No run is authorized.
                  </p>
                )}
                {receipt.check ? (
                  <section aria-label="Independent candidate check">
                    <p>
                      Independent check: {receipt.check.outcome} ·{' '}
                      {receipt.check.scope} · checker {receipt.check.checkerVersion} ·{' '}
                      domain {receipt.check.domain} ·{' '}
                      <code>{receipt.check.checkId}</code>
                    </p>
                    <p className="research-check-scope-note">
                      Verification vector: parse {receipt.check.parse} · type{' '}
                      {receipt.check.type} · domain {receipt.check.domain} ·
                      symbolic {receipt.check.outcome}
                    </p>
                    <p>{receipt.check.claim}</p>
                    <p className="research-check-scope-note">
                      {receipt.check.scope === 'feature_kernel_identity'
                        ? 'This supports only the displayed kernel identity under the listed assumptions. It does not clear the candidate’s remaining obligations or establish benchmark quality or empirical performance.'
                        : 'This checks the declared structure only; it did not establish a symbolic identity.'}
                    </p>
                    {receipt.check.assumptions.length ? (
                      <ul>
                        {receipt.check.assumptions.map((assumption) => (
                          <li key={assumption}>{assumption}</li>
                        ))}
                      </ul>
                    ) : null}
                  </section>
                ) : null}
                <ul>
                  {receipt.obligations.map((obligation) => (
                    <li key={obligation.name}>
                      <span>{obligation.name.replaceAll('_', ' ')}</span>
                      <b>{obligation.status}</b>
                    </li>
                  ))}
                </ul>
                {receipt.operator === 'lower_mixture_to_concatenation' ? (
                  <details className="research-case-panel" open>
                    <summary>Frozen CPU research case · protocol evidence</summary>
                    <div>
                      <p>
                        Runs the registered synthetic matched-control protocol with
                        frozen seeds, split assignments, rank-matched parents and a
                        protected holdout. This is server reference code only; it is
                        not author code or a product-performance benchmark.
                      </p>
                      {receipt.researchCase ? (
                        <dl>
                          <div>
                            <dt>Outcome</dt>
                            <dd data-state={receipt.researchCase.outcome}>
                              {receipt.researchCase.outcome.replaceAll('_', ' ')}
                            </dd>
                          </div>
                          <div>
                            <dt>Holdout mean</dt>
                            <dd>{receipt.researchCase.holdoutMean ?? 'Not available'}</dd>
                          </div>
                          <div>
                            <dt>95% CI</dt>
                            <dd>
                              {receipt.researchCase.holdoutCi95Low !== null && receipt.researchCase.holdoutCi95High !== null
                                ? `${receipt.researchCase.holdoutCi95Low} … ${receipt.researchCase.holdoutCi95High}`
                                : 'Not available'}
                            </dd>
                          </div>
                          <div>
                            <dt>Result</dt>
                            <dd><code>{receipt.researchCase.resultId}</code></dd>
                          </div>
                        </dl>
                      ) : (
                        <button
                          type="button"
                          className="research-move-secondary"
                          disabled={!canRunResearchCase}
                          onClick={() => void runResearchCase()}
                        >
                          {researchCaseBusy ? 'Running frozen protocol…' : 'Run frozen CPU research case'}
                        </button>
                      )}
                      {researchCaseBusy ? (
                        <output aria-live="polite">
                          Running frozen seeds and holdout; no model API or paid provider is called…
                        </output>
                      ) : null}
                    </div>
                  </details>
                ) : null}
                {receipt.numericalFixture ? (
                  <details className="research-synthetic-fixture">
                    <summary>Worker fixture · diagnostic only</summary>
                    <div>
                      <p>
                        Recorded outcome:{' '}
                        <code>{receipt.numericalFixture.outcome}</code>
                      </p>
                      <p>
                        {receipt.numericalFixture.suiteVersion} · seed{' '}
                        {receipt.numericalFixture.seed} ·{' '}
                        {receipt.numericalFixture.dtype} · tolerance{' '}
                        {receipt.numericalFixture.tolerance}
                      </p>
                      <p>
                        Sandbox image:{' '}
                        <code className="break-all">
                          {receipt.numericalFixture.executionImage ?? 'not recorded (legacy receipt)'}
                        </code>
                      </p>
                      <p>
                        This fixed synthetic fixture exercises registered worker
                        plumbing; it does not run a paper implementation,
                        establish model quality, prove equivalence, or support a
                        performance claim. Admission is unchanged.
                      </p>
                      <ul>
                        {Object.entries(receipt.numericalFixture.checks).map(
                          ([name, passed]) => (
                            <li key={name}>
                              <span>{name.replaceAll('_', ' ')}</span>
                              <b>{passed ? 'check passed' : 'check failed'}</b>
                            </li>
                          ),
                        )}
                      </ul>
                      {receipt.numericalFixture.errorCode ? (
                        <p>
                          Diagnostic status:{' '}
                          {receipt.numericalFixture.errorCode}
                        </p>
                      ) : null}
                      <p>
                        <code>{receipt.numericalFixture.resultId}</code>
                      </p>
                    </div>
                  </details>
                ) : null}
                <p className="research-move-note">
                  {receipt.researchCase
                    ? 'The registered CPU research case is synthetic protocol evidence only; no paper implementation or product-performance claim was tested.'
                    : receipt.numericalFixture
                      ? 'No paper implementation or empirical experiment has been tested.'
                      : 'No numerical suite or research case has run.'}
                </p>
                {receipt.operator === 'mix_positive_feature_maps' ? (
                  <button
                    type="button"
                    className="research-move-secondary"
                    disabled={!canLower}
                    onClick={() => {
                      void compile(receipt);
                    }}
                  >
                    Add separate concatenation representation
                  </button>
                ) : null}
                <section
                  className="research-replay-report"
                  aria-label="Compiler replay report"
                >
                  <div className="research-replay-report-action">
                    <div>
                      <strong>Replayable evidence</strong>
                      <small>Source lineage, scoped checks, and saved policy decisions</small>
                    </div>
                    <button
                      type="button"
                      className="research-move-secondary"
                      aria-expanded={Boolean(reportAttempt)}
                      aria-controls={`replay-report-${receipt.candidateId}`}
                      disabled={reportAttempt?.state === 'loading'}
                      onClick={() => void loadReplayReport()}
                    >
                      {reportAttempt?.state === 'loading'
                        ? 'Replaying…'
                        : reportAttempt?.state === 'loaded'
                          ? 'Refresh report'
                          : reportAttempt?.state === 'unavailable'
                            ? 'Retry report'
                            : 'Open replay report'}
                    </button>
                  </div>
                  <p>
                    Loads this exact saved activity and makes no AI or experiment call.
                  </p>
                  <div
                    className="research-replay-report-content"
                    id={`replay-report-${receipt.candidateId}`}
                  >
                    {reportAttempt?.state === 'loading' ? (
                      <output aria-live="polite">
                        Replaying the saved compiler inputs and collecting recorded evidence…
                      </output>
                    ) : null}
                    {reportAttempt?.state === 'unavailable' ? (
                      <output aria-live="polite">
                        Report unavailable or did not match this candidate. No new check or run was started.
                      </output>
                    ) : null}
                    {reportAttempt?.state === 'loaded' && reportAttempt.report ? (
                      <div className="research-replay-report-body">
                      <header>
                        <div>
                          <small>Compiler replay · {reportAttempt.report.operator} v{reportAttempt.report.operatorVersion}</small>
                          <strong>Replay report · {reportAttempt.report.empiricalExperiment.replaceAll('_', ' ')}</strong>
                        </div>
                        <span data-state={reportAttempt.report.empiricalExperiment}>
                          {reportAttempt.report.empiricalExperiment === 'not_run'
                            ? 'No research case'
                            : 'Protocol evidence'}
                        </span>
                      </header>
                      <p>
                        The compiler reproduced this recorded transformation. That does not establish general mathematical correctness, model quality, or performance; each checker result below has its own scope.
                      </p>
                      <dl>
                        <div>
                          <dt>Proposal review</dt>
                          <dd>{reportAttempt.report.proposalReviewStatus.replaceAll('_', ' ')}</dd>
                        </div>
                        <div>
                          <dt>Compatibility mapping</dt>
                          <dd><code>{reportAttempt.report.compatibilityMappingId}</code></dd>
                        </div>
                        <div>
                          <dt>Lineage assertions</dt>
                          <dd>{reportAttempt.report.lineageAssertionIds.length || 'None recorded'}</dd>
                        </div>
                        <div>
                          <dt>Experiment</dt>
                          <dd>{reportAttempt.report.empiricalExperiment.replaceAll('_', ' ')} · no product-performance claim</dd>
                        </div>
                      </dl>
                      <section aria-label="Report source references">
                        <h4>Paper source anchors</h4>
                        <ul>
                          {reportAttempt.report.sourceRefs.map((source) => {
                            const href = replaySourceHref(source);
                            return (
                              <li key={`${source.equationId}:${source.anchor}`}>
                                <span>{source.paperId} v{source.version} · {source.anchor} · <code>{source.equationId}</code></span>
                                {href ? <a href={href} target="_blank" rel="noopener noreferrer">Open HTML source ↗</a> : <span>Source link unavailable</span>}
                              </li>
                            );
                          })}
                        </ul>
                      </section>
                      <section aria-label="Scoped checker outcomes">
                        <h4>Checker outcomes</h4>
                        {reportAttempt.report.checks.length ? (
                          <ul>
                            {reportAttempt.report.checks.map((check) => (
                              <li key={check.checkId} data-state={check.outcome}>
                                <strong>{check.outcome}</strong>
                                <span>{check.scope.replaceAll('_', ' ')} · domain {check.domainStatus} · type {check.typeStatus}</span>
                                <p>{check.claim}</p>
                                <small>{check.replayStatus === 'replayed' ? 'Replayed; receipt matched' : 'Historical receipt; checker version not rerun'}</small>
                                <small>{check.checkerVersion} · <code>{check.checkId}</code></small>
                              </li>
                            ))}
                          </ul>
                        ) : <p>No checker result is recorded for this activity.</p>}
                      </section>
                      <section aria-label="Policy decisions">
                        <h4>Policy decisions · not mathematical verdicts</h4>
                        {reportAttempt.report.policyDecisions.length ? (
                          <ul>
                            {reportAttempt.report.policyDecisions.map((decision) => (
                              <li key={decision.decisionId} data-state={decision.outcome}>
                                <strong>{decision.outcome}</strong>
                                <span>
                                  {decision.action.replaceAll('_', ' ')} · {decision.replayStatus === 'replayed'
                                    ? 'rerun from frozen policy input'
                                    : 'stored receipt only'}
                                </span>
                                {decision.reasons.map((reason) => <small key={reason}>{admissionReason(reason)}</small>)}
                              </li>
                            ))}
                          </ul>
                        ) : <p>No policy decision is recorded for this activity.</p>}
                      </section>
                      {reportAttempt.report.numericalFixtures.length ? (
                        <section aria-label="Synthetic diagnostic fixtures">
                          <h4>Synthetic diagnostics · not empirical evidence</h4>
                          <ul>
                            {reportAttempt.report.numericalFixtures.map((fixture) => (
                              <li key={fixture.resultId} data-state="not-run">
                                <strong>{fixture.outcome}</strong>
                                <span>
                                  seed {fixture.seed} · {fixture.replayStatus === 'replayed'
                                    ? 'rerun from frozen input'
                                    : 'stored receipt only'} · <code>{fixture.resultId}</code>
                                </span>
                              </li>
                            ))}
                          </ul>
                        </section>
                      ) : null}
                      {reportAttempt.report.researchCases.length ? (
                        <section aria-label="Registered research cases">
                          <h4>Registered CPU research cases</h4>
                          <ul>
                            {reportAttempt.report.researchCases.map((researchCase) => (
                              <li key={researchCase.resultId} data-state={researchCase.outcome}>
                                <strong>{researchCase.outcome.replaceAll('_', ' ')}</strong>
                                <span>
                                  {researchCase.replayStatus === 'replayed' ? 'replayed from frozen protocol' : 'stored receipt only'} · <code>{researchCase.resultId}</code>
                                </span>
                                <small>
                                  Holdout {researchCase.holdoutMean ?? 'n/a'} · CI [{researchCase.holdoutCi95Low ?? 'n/a'}, {researchCase.holdoutCi95High ?? 'n/a'}]
                                </small>
                                <small>No author-code or product-performance claim</small>
                              </li>
                            ))}
                          </ul>
                        </section>
                      ) : null}
                      <section aria-label="Report limitations">
                        <h4>Limitations</h4>
                        <ul>{reportAttempt.report.limitations.map((limitation) => <li key={limitation}>{limitation}</li>)}</ul>
                      </section>
                      <p className="research-replay-report-hash">
                        Report SHA-256 <code>{reportAttempt.report.reportHash}</code><br />
                        Bundle SHA-256 <code>{reportAttempt.report.bundleHash}</code>
                      </p>
                      <div className="research-replay-export">
                      <button
                        type="button"
                        className="research-move-secondary"
                        disabled={bundleAttempt?.state === 'loading'}
                        onClick={() => void downloadReplayBundle()}
                      >
                        {bundleAttempt?.state === 'loading'
                          ? 'Preparing bundle…'
                          : bundleAttempt?.state === 'downloaded'
                            ? 'Download bundle again'
                            : 'Download replay bundle JSON'}
                      </button>
                      {bundleAttempt?.state === 'downloaded' ? (
                        <output aria-live="polite">
                          Bundle downloaded. Verify it with{' '}
                          <code>python -m app.replay_bundle_cli</code> from{' '}
                          <code>services/graph-api</code>.
                        </output>
                      ) : null}
                      {bundleAttempt?.state === 'unavailable' ? (
                        <output aria-live="polite">
                          Bundle unavailable or changed since this report. Refresh the report and try again; no file was downloaded.
                        </output>
                      ) : null}
                      </div>
                      </div>
                    ) : null}
                  </div>
                </section>
              </section>
            ) : null}
          </div>
        </DialogContent>
      </Dialog>
    </section>
  );
}
