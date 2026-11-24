'use client';

import Link from 'next/link';
import { useEffect, useRef, useState, type SyntheticEvent } from 'react';
import {
  ShieldCheck,
  ShieldAlert,
  Lock,
  RefreshCw,
  Copy,
  Check,
  FileCode2,
  Boxes,
  History,
  AlertCircle,
  CheckCircle2,
  ArrowRight,
} from 'lucide-react';
import { Badge } from '@/components/ui/badge';
import { syncWorkspaceQueryParam } from '@/components/workspace-shell';
import EvolutionPanel from './evolution-panel';

const hexPlaceholder = 'a'.repeat(64);
const hexPlaceholderB = 'b'.repeat(64);
const hexPlaceholderC = 'c'.repeat(64);
const hexPlaceholderD = 'd'.repeat(64);
const hexPlaceholderE = 'e'.repeat(64);

const sections = [
  ['metrics', 'Metrics and directions', [{ name: 'rate_distortion_psnr', unit: 'dB', direction: 'maximize' }], 'Name each measured outcome, its unit, and whether lower or higher is better.'],
  ['quality_constraints', 'Quality constraints', [{ metric: 'rate_distortion_psnr', relation: '>=', threshold: 30.0 }], 'Set the maximum permitted quality loss or other guardrails against the baseline.'],
  ['baselines', 'Baseline artifacts', [{ name: 'scaffold_gs_baseline', version: '1.0', sha256: hexPlaceholder, readiness: 'unverified' }], 'Pin every comparison implementation by version and SHA-256; an unverified artifact is not evidence.'],
  ['dataset', 'Dataset and splits', { artifact_hash: hexPlaceholderB, version: '1.0', splits: [{ role: 'search', split_hash: hexPlaceholderB }, { role: 'validation', split_hash: hexPlaceholderC }, { role: 'holdout', split_hash: hexPlaceholderD }] }, 'Pin the dataset and give search, validation, and protected holdout splits distinct identities.'],
  ['model', 'Model artifact or not_applicable with reason', { name: 'scaffold_anchor_model', version: '1.0.0', sha256: hexPlaceholderC, readiness: 'unverified' }, 'Identify the exact model revision, or explain why this experiment does not use one.'],
  ['tokenizer', 'Tokenizer artifact or not_applicable with reason', { status: 'not_applicable', reason: '3DGS compression operates directly on spatial coordinates.' }, 'Identify the exact tokenizer revision, or explain why tokenization is not applicable.'],
  ['hardware', 'Pinned hardware', { target: 'NVIDIA-RTX-4090-24GB' }, 'Record the hardware class and relevant device/runtime details for reproducibility.'],
  ['backend', 'Pinned backend', { name: 'pytorch', version: '2.4.0' }, 'Pin the numerical framework and version used by the evaluator.'],
  ['evaluator', 'Evaluator manifest', { name: 'fgl-psnr-evaluator', protocol_version: 'v1', implementation_hash: hexPlaceholderD, config_hash: hexPlaceholderE }, 'Pin the evaluator protocol, implementation, and configuration hashes.'],
  ['seeds', 'Distinct run seeds', [42, 1337], 'List the exact seeds used for each repeated run; do not reuse one run as multiple samples.'],
  ['budget', 'Search and compute limits', { max_candidates: 10, max_generations: 5, wall_time_ms: 60000, compute_budget: 10.0, compute_unit: 'GPU-seconds' }, 'Bound candidates, generations, elapsed time, and compute; these defaults are placeholders.'],
  ['allowed_transforms', 'Allowed transform names and versions', [{ name: 'mix_positive_feature_maps', version: '1' }, { name: 'lower_mixture_to_concatenation', version: '1' }], 'Allow only registered DSL transformations with explicit versions.'],
  ['stop_conditions', 'Metric stop conditions', [{ metric: 'rate_distortion_psnr', relation: '>=', target_value: 33.5 }], 'Define objective thresholds or conditions that stop the search.'],
] as const;

type Snapshot = {
  spec_id: string; campaign_id: string; content_hash: string; version: number;
  parent_spec_id: string | null; definition: Record<string, unknown>;
};

const REGISTERED_V1_TRANSFORMS = [
  { name: 'mix_positive_feature_maps', version: '1' },
  { name: 'lower_mixture_to_concatenation', version: '1' },
] as const;

function record(value: unknown): value is Record<string, unknown> {
  return value !== null && typeof value === 'object' && !Array.isArray(value);
}

function parseJsonArray(raw: string): { ok: true; items: unknown[] } | { ok: false } {
  try {
    const parsed: unknown = JSON.parse(raw);
    return Array.isArray(parsed) ? { ok: true, items: parsed } : { ok: false };
  } catch {
    return { ok: false };
  }
}

function parseJsonRecord(raw: string): { ok: true; value: Record<string, unknown> } | { ok: false } {
  try {
    const parsed: unknown = JSON.parse(raw);
    return record(parsed) ? { ok: true, value: parsed } : { ok: false };
  } catch {
    return { ok: false };
  }
}

function parseSnapshot(value: unknown): Snapshot {
  if (!record(value) || typeof value.spec_id !== 'string' || !value.spec_id.startsWith('spec_') ||
    typeof value.campaign_id !== 'string' || !value.campaign_id.startsWith('cmp_') ||
    typeof value.content_hash !== 'string' || !/^[a-f0-9]{64}$/.test(value.content_hash) ||
    typeof value.version !== 'number' || !Number.isInteger(value.version) || value.version < 1 ||
    (value.parent_spec_id !== null && typeof value.parent_spec_id !== 'string') ||
    !record(value.definition) || typeof value.definition.task !== 'string' ||
    typeof value.definition.method_family !== 'string' ||
    !['float32', 'float64', 'bfloat16'].includes(String(value.definition.dtype))) {
    throw new Error('Invalid ProblemSpec response');
  }
  const definition = value.definition;
  if (sections.some(([key]) => !(key in definition))) throw new Error('Incomplete definition');
  return value as Snapshot;
}

function initialFields(): Record<string, string> {
  return Object.fromEntries(sections.map(([key, , value]) => [key, JSON.stringify(value, null, 2)]));
}

function summarizeManifest(fields: Record<string, string>) {
  const parse = (key: string): unknown => {
    try { return JSON.parse(fields[key]); } catch { return null; }
  };
  const metrics = parse('metrics');
  const firstMetric = Array.isArray(metrics) && record(metrics[0]) && typeof metrics[0].name === 'string'
    ? `${metrics[0].name} (${typeof metrics[0].direction === 'string' ? metrics[0].direction : 'metric'})`
    : 'Not set';
  const baselines = parse('baselines');
  const baselineLabel = Array.isArray(baselines) && baselines.length > 0
    ? `${baselines.length} pinned (${baselines.filter(b => record(b) && typeof b.name === 'string').map(b => String((b as Record<string, unknown>).name)).slice(0, 2).join(', ')})`
    : '0 baselines';
  const hardware = parse('hardware');
  const backend = parse('backend');
  const runtimeLabel = [
    record(hardware) && typeof hardware.target === 'string' ? hardware.target : null,
    record(backend) && typeof backend.name === 'string' ? backend.name : null,
  ].filter(Boolean).join(' · ') || 'Unset';
  const budget = parse('budget');
  const seeds = parse('seeds');
  const budgetLabel = record(budget) && typeof budget.max_candidates === 'number'
    ? `≤ ${budget.max_candidates} cand · ${Array.isArray(seeds) ? seeds.length : 0} seed(s)`
    : `${Array.isArray(seeds) ? seeds.length : 0} seed(s)`;
  return { firstMetric, baselineLabel, runtimeLabel, budgetLabel };
}

export default function ProblemSpecPanel({ showEvolution = true }: { showEvolution?: boolean }) {
  const [step, setStep] = useState<1 | 2 | 3>(1);
  const [task, setTask] = useState('');
  const [family, setFamily] = useState('');
  const [dtype, setDtype] = useState('float32');
  const [fields, setFields] = useState(initialFields);
  const [snapshot, setSnapshot] = useState<Snapshot | null>(null);
  const [parent, setParent] = useState<string | null>(null);
  const [history, setHistory] = useState<Snapshot[]>([]);
  const [total, setTotal] = useState(0);
  const [offset, setOffset] = useState(0);
  const [refresh, setRefresh] = useState(0);
  const [historyNotice, setHistoryNotice] = useState('Loading saved specs…');
  const [notice, setNotice] = useState('Draft (unfrozen)');
  const [readinessNotice, setReadinessNotice] = useState('Execution is unavailable in Phase 5A.');
  const [confirmed, setConfirmed] = useState(false);
  const [busy, setBusy] = useState(false);
  const [copiedHash, setCopiedHash] = useState(false);
  const [builderDrafts, setBuilderDrafts] = useState<Record<string, { source: string; text: string }>>({});
  const inFlight = useRef(false);
  const retry = useRef<{ body: string; key: string } | null>(null);

  const loadBenchmarkTemplate = () => {
    fillValid3DGSManifest();
  };

  const fillValid3DGSManifest = () => {
    setTask('Compress 3D Gaussian Splatting anchor latent coordinates with deterministic entropy coding (arXiv:2609.30245)');
    setFamily('anchor_compression');
    setDtype('float32');
    const hexA = 'a'.repeat(64);
    const hexB = 'b'.repeat(64);
    const hexC = 'c'.repeat(64);
    const hexD = 'd'.repeat(64);
    const hexE = 'e'.repeat(64);
    setFields({
      metrics: JSON.stringify([{ name: 'rate_distortion_psnr', unit: 'dB', direction: 'maximize' }], null, 2),
      quality_constraints: JSON.stringify([{ metric: 'rate_distortion_psnr', relation: '>=', threshold: 30.0 }], null, 2),
      baselines: JSON.stringify([{ name: 'scaffold_gs_baseline', version: '1.0', sha256: hexA, readiness: 'unverified' }], null, 2),
      dataset: JSON.stringify({ artifact_hash: hexB, version: '1.0', splits: [{ role: 'search', split_hash: hexB }, { role: 'validation', split_hash: hexC }, { role: 'holdout', split_hash: hexD }] }, null, 2),
      model: JSON.stringify({ name: 'scaffold_anchor_model', version: '1.0.0', sha256: hexC, readiness: 'unverified' }, null, 2),
      tokenizer: JSON.stringify({ status: 'not_applicable', reason: '3DGS compression operates directly on spatial coordinates.' }, null, 2),
      hardware: JSON.stringify({ target: 'NVIDIA-RTX-4090-24GB' }, null, 2),
      backend: JSON.stringify({ name: 'pytorch', version: '2.4.0' }, null, 2),
      evaluator: JSON.stringify({ name: 'fgl-psnr-evaluator', protocol_version: 'v1', implementation_hash: hexD, config_hash: hexE }, null, 2),
      seeds: JSON.stringify([42, 1337], null, 2),
      budget: JSON.stringify({ max_candidates: 10, max_generations: 5, wall_time_ms: 60000, compute_budget: 10.0, compute_unit: 'GPU-seconds' }, null, 2),
      allowed_transforms: JSON.stringify([{ name: 'mix_positive_feature_maps', version: '1' }, { name: 'lower_mixture_to_concatenation', version: '1' }], null, 2),
      stop_conditions: JSON.stringify([{ metric: 'rate_distortion_psnr', relation: '>=', target_value: 33.5 }], null, 2),
    });
    setNotice('Draft 3DGS scaffold loaded (contains placeholder SHA-256 hashes; replace before real experiments).');
  };

  const fillValidAttentionManifest = () => {
    setTask('Measure causal attention latency and memory footprint under varying sequence lengths (arXiv:1706.03762)');
    setFamily('FlashAttention-variant');
    setDtype('float32');
    const hexA = 'a'.repeat(64);
    const hexB = 'b'.repeat(64);
    const hexC = 'c'.repeat(64);
    const hexD = 'd'.repeat(64);
    const hexE = 'e'.repeat(64);
    setFields({
      metrics: JSON.stringify([
        { name: 'latency_us', unit: 'microseconds', direction: 'minimize' },
        { name: 'perplexity', unit: 'score', direction: 'minimize' },
      ], null, 2),
      quality_constraints: JSON.stringify([{ metric: 'perplexity', relation: '<=', threshold: 12.5 }], null, 2),
      baselines: JSON.stringify([{ name: 'flash_v2', version: '2.5.6', sha256: hexA, readiness: 'unverified' }], null, 2),
      dataset: JSON.stringify({ artifact_hash: hexB, version: '1.0', splits: [{ role: 'search', split_hash: hexB }, { role: 'validation', split_hash: hexC }, { role: 'holdout', split_hash: hexD }] }, null, 2),
      model: JSON.stringify({ name: 'llama3_8b', version: '1.0.0', sha256: hexC, readiness: 'unverified' }, null, 2),
      tokenizer: JSON.stringify({ status: 'not_applicable', reason: 'Evaluator operates directly on dense token embeddings.' }, null, 2),
      hardware: JSON.stringify({ target: 'NVIDIA-H100-SXM5-80GB' }, null, 2),
      backend: JSON.stringify({ name: 'torch-triton', version: '3.0.0' }, null, 2),
      evaluator: JSON.stringify({ name: 'benchmark_attention', protocol_version: 'v1', implementation_hash: hexD, config_hash: hexE }, null, 2),
      seeds: JSON.stringify([42, 100, 2026], null, 2),
      budget: JSON.stringify({ max_candidates: 20, max_generations: 5, wall_time_ms: 120000, compute_budget: 24.0, compute_unit: 'GPU-hours' }, null, 2),
      allowed_transforms: JSON.stringify([{ name: 'mix_positive_feature_maps', version: '1' }, { name: 'lower_mixture_to_concatenation', version: '1' }], null, 2),
      stop_conditions: JSON.stringify([{ metric: 'latency_us', relation: '<=', target_value: 850.0 }], null, 2),
    });
    setNotice('Draft Scaled Attention scaffold loaded (contains placeholder SHA-256 hashes; replace before real experiments).');
  };

  const fillRegisteredCpuPilotManifest = () => {
    setTask('Compare one frozen feature-map mashup with rank-matched parents.');
    setFamily('linear-attention');
    setDtype('float64');
    setFields({
      metrics: JSON.stringify([{ name: 'candidate_regret_vs_best_parent', unit: 'mean-absolute-error', direction: 'minimize' }], null, 2),
      quality_constraints: JSON.stringify([{ metric: 'candidate_regret_vs_best_parent', relation: '<=', threshold: 0.01 }], null, 2),
      baselines: JSON.stringify([
        { name: 'exact-causal-softmax', version: '1', sha256: 'ada0eb8a2727d421946d8b49566df27f31e33058ddd4d576c0112fbb414a14cf', readiness: 'unverified' },
        { name: 'positive-random-exp', version: '1', sha256: 'c37dd61962774cc7f4fa72b53c1d577e1af361095ee8574fbd7768819eb0e580', readiness: 'unverified' },
        { name: 'positive-elu', version: '1', sha256: '680ab392ec1bf7caa18f1cc92f62c774395c44ef0d76d6b8dcebeb67b005e6be', readiness: 'unverified' },
      ], null, 2),
      dataset: JSON.stringify({
        artifact_hash: 'cde07838f7e77880f2ca4c21f33e5ff0db3c81f2ce057487b0abe117fdeb4054',
        version: '1',
        splits: [
          { role: 'search', split_hash: 'd5228c6a4307e007bf9e252f7dafa205b25959fa4f422d51597b18a8e5c21ccb' },
          { role: 'validation', split_hash: '87da661283fba0ae3981f1d656463184763a9e5d771348e6a43357a079a552aa' },
          { role: 'holdout', split_hash: '064c1e57924d081b61d0d40d97250fd52aade1c6c156eccdbae6e254c7e876e7' },
        ],
      }, null, 2),
      model: JSON.stringify({ status: 'not_applicable', reason: 'Operator-only synthetic case.' }, null, 2),
      tokenizer: JSON.stringify({ status: 'not_applicable', reason: 'No text model is used.' }, null, 2),
      hardware: JSON.stringify({ target: 'cpu' }, null, 2),
      backend: JSON.stringify({ name: 'python-stdlib', version: '3.12' }, null, 2),
      evaluator: JSON.stringify({
        name: 'fgl-attention-mashup',
        protocol_version: 'attention-mashup-cpu-synthetic.v1',
        implementation_hash: 'ac619a7eab5592f7c05c0ad1a0ac4e4f12afcfb2ceb5851744ac01d9b0cc6e5a',
        config_hash: 'f49042111b7688e9b2f441aa3f49be709b4eb9ce4236fb7070708bedf837ffb7',
      }, null, 2),
      seeds: JSON.stringify([42, 43, 44, 45, 46], null, 2),
      budget: JSON.stringify({ max_candidates: 2, max_generations: 2, wall_time_ms: 600000, compute_budget: 60, compute_unit: 'CPU-seconds' }, null, 2),
      allowed_transforms: JSON.stringify([{ name: 'mix_positive_feature_maps', version: '1' }, { name: 'lower_mixture_to_concatenation', version: '1' }], null, 2),
      stop_conditions: JSON.stringify([], null, 2),
    });
    setNotice('Registered CPU synthetic pilot draft loaded (attention-mashup-cpu-synthetic.v1; synthetic operator scope only, no product claim).');
  };

  const fillValidQuantumGravityManifest = () => {
    setTask('Verify Ashtekar-Barbero connection commutators under spatial diffeomorphism invariance');
    setFamily('gauge_gravity');
    setDtype('float64');
    const hexA = 'a'.repeat(64);
    const hexB = 'b'.repeat(64);
    const hexC = 'c'.repeat(64);
    const hexD = 'd'.repeat(64);
    const hexE = 'e'.repeat(64);
    setFields({
      metrics: JSON.stringify([{ name: 'constraint_residual', unit: 'norm', direction: 'minimize' }], null, 2),
      quality_constraints: JSON.stringify([{ metric: 'constraint_residual', relation: '<=', threshold: 1e-12 }], null, 2),
      baselines: JSON.stringify([{ name: 'ashtekar_barbero_baseline', version: '1.0.0', sha256: hexA, readiness: 'unverified' }], null, 2),
      dataset: JSON.stringify({ artifact_hash: hexB, version: '1.0', splits: [{ role: 'search', split_hash: hexB }, { role: 'validation', split_hash: hexC }, { role: 'holdout', split_hash: hexD }] }, null, 2),
      model: JSON.stringify({ name: 'symbolic_cas_kernel', version: '1.0.0', sha256: hexC, readiness: 'unverified' }, null, 2),
      tokenizer: JSON.stringify({ status: 'not_applicable', reason: 'Operates directly on CAS symbolic expressions.' }, null, 2),
      hardware: JSON.stringify({ target: 'AMD-EPYC-9654-128C' }, null, 2),
      backend: JSON.stringify({ name: 'sympy-cas', version: '1.13.2' }, null, 2),
      evaluator: JSON.stringify({ name: 'fgl-symbolic-cas-evaluator', protocol_version: 'v1', implementation_hash: hexD, config_hash: hexE }, null, 2),
      seeds: JSON.stringify([1, 42], null, 2),
      budget: JSON.stringify({ max_candidates: 20, max_generations: 10, wall_time_ms: 120000, compute_budget: 20.0, compute_unit: 'CPU-seconds' }, null, 2),
      allowed_transforms: JSON.stringify([{ name: 'mix_positive_feature_maps', version: '1' }, { name: 'lower_mixture_to_concatenation', version: '1' }], null, 2),
      stop_conditions: JSON.stringify([{ metric: 'constraint_residual', relation: '<=', target_value: 1e-15 }], null, 2),
    });
    setNotice('Draft Quantum Gravity scaffold loaded (contains placeholder SHA-256 hashes; replace before real experiments).');
  };

  useEffect(() => {
    if (!snapshot) return;
    const controller = new AbortController();
    const id = snapshot.spec_id;
    void (async () => {
      try {
        const response = await fetch(`/api/research/problems/readiness?spec_id=${encodeURIComponent(id)}`, { signal: controller.signal });
        if (!response.ok) throw new Error('Readiness unavailable');
        const result: unknown = await response.json();
        if (!record(result) || result.spec_id !== id || result.ready_to_run !== false ||
          !Array.isArray(result.blocked_reasons) || !result.blocked_reasons.every(reason => typeof reason === 'string')) {
          throw new Error('Invalid readiness receipt');
        }
        if (!controller.signal.aborted) setReadinessNotice(`Not ready to run: ${result.blocked_reasons.join(', ')}`);
      } catch { if (!controller.signal.aborted) setReadinessNotice('Readiness unavailable; execution remains disabled.'); }
    })();
    return () => controller.abort();
  }, [snapshot]);

  useEffect(() => {
    const controller = new AbortController();
    async function load() {
      setHistoryNotice('Loading saved specs…');
      try {
        const response = await fetch(`/api/research/problems?limit=20&offset=${offset}`, {
          signal: controller.signal,
        });
        if (!response.ok) throw new Error('History unavailable');
        const result: unknown = await response.json();
        if (!record(result) || !Array.isArray(result.items) || typeof result.total !== 'number' ||
          !Number.isInteger(result.total) || result.total < 0) throw new Error('Invalid history');
        const saved = result.items.map(parseSnapshot);
        if (controller.signal.aborted) return;
        setHistory(saved); setTotal(result.total);
        setHistoryNotice(saved.length ? 'Saved immutable snapshots' : 'No saved specs on this page.');
      } catch {
        if (!controller.signal.aborted) setHistoryNotice('History unavailable. Your draft is unchanged.');
      }
    }
    void load();
    return () => controller.abort();
  }, [offset, refresh]);

  function inspect(saved: Snapshot) {
    if (busy) return;
    setTask(String(saved.definition.task)); setFamily(String(saved.definition.method_family));
    setDtype(String(saved.definition.dtype));
    setFields(Object.fromEntries(sections.map(([key]) => [key, JSON.stringify(saved.definition[key], null, 2)])));
    setReadinessNotice('Checking server readiness…');
    setSnapshot(saved); setParent(saved.parent_spec_id); setConfirmed(false);
    setStep(2);
    syncWorkspaceQueryParam('spec_id', saved.spec_id);
    setNotice('Frozen snapshot loaded. Create a revision to edit; artifacts remain unverified.');
  }

  function continueSetup() {
    if (step === 1 && (!task.trim() || !family.trim())) {
      setNotice('Add a research question and method family before continuing.');
      return;
    }
    if (step === 1) {
      setNotice('');
    }
    if (step === 2) {
      for (const [key, label] of sections) {
        try {
          JSON.parse(fields[key]);
        } catch {
          setNotice(`${label}: enter valid JSON before continuing.`);
          document.getElementById(`problem-spec-section-${key}`)?.focus();
          return;
        }
      }
      setNotice('Manifest syntax is valid. Values and artifacts are still unverified.');
    }
    setStep(current => current === 1 ? 2 : 3);
  }

  function jsonFieldError(key: string): string | null {
    try {
      JSON.parse(fields[key]);
      return null;
    } catch (error) {
      return error instanceof Error ? error.message : 'Invalid JSON syntax.';
    }
  }

  function formatJsonField(key: string, label?: string) {
    try {
      const parsed = JSON.parse(fields[key]);
      setFields(curr => ({ ...curr, [key]: JSON.stringify(parsed, null, 2) }));
      if (label) setNotice(`${label} formatted.`);
    } catch (error) {
      setNotice(`${label ?? key}: Invalid JSON — ${error instanceof Error ? error.message : 'fix syntax before formatting.'}`);
      document.getElementById(`problem-spec-section-${key}`)?.focus();
    }
  }

  function formatAllJsonFields() {
    const next = { ...fields };
    for (const [key, label] of sections) {
      try {
        next[key] = JSON.stringify(JSON.parse(fields[key]), null, 2);
      } catch (error) {
        setNotice(`${label}: Invalid JSON — ${error instanceof Error ? error.message : 'fix syntax before formatting.'}`);
        document.getElementById(`problem-spec-section-${key}`)?.focus();
        return;
      }
    }
    setFields(next);
    setNotice('All 13 manifest JSON fields formatted.');
  }

  async function freeze(event: SyntheticEvent<HTMLFormElement>) {
    event.preventDefault();
    if (step !== 3 || inFlight.current || snapshot || !confirmed) return;
    inFlight.current = true; setBusy(true);
    try {
      const definition: Record<string, unknown> = { task: task.trim(), method_family: family.trim(), dtype };
      for (const [key, label] of sections) {
        try { definition[key] = JSON.parse(fields[key]); }
        catch { throw new Error(`${label}: enter valid JSON.`); }
      }
      const body = JSON.stringify({ definition, parent_spec_id: parent });
      if (new TextEncoder().encode(body).length > 64 * 1024) throw new Error('Manifest exceeds 64 KiB.');
      if (retry.current?.body !== body) retry.current = { body, key: crypto.randomUUID() };
      const response = await fetch('/api/research/problems', {
        method: 'POST', headers: { 'content-type': 'application/json', 'x-idempotency-key': retry.current.key },
        body,
      });
      if (!response.ok) {
        const error: unknown = await response.json().catch(() => null);
        let reason = `Save rejected (${response.status}).`;
        if (record(error) && Array.isArray(error.detail)) {
          reason += ' ' + error.detail.filter(record).map(issue =>
            `${Array.isArray(issue.loc) ? issue.loc.filter(p => typeof p === 'string').join('.') : 'manifest'}: ${typeof issue.msg === 'string' ? issue.msg : 'invalid'}`,
          ).join('; ').slice(0, 1200);
        }
        throw new Error(reason);
      }
      const saved = parseSnapshot(await response.json());
      setReadinessNotice('Checking server readiness…');
      setSnapshot(saved); setRefresh(value => value + 1);
      syncWorkspaceQueryParam('spec_id', saved.spec_id);
      setNotice('ProblemSpec saved. Artifact verification is still pending.');
    } catch (error) {
      setNotice(`Could not save ProblemSpec. Your draft is unchanged. ${error instanceof Error ? error.message : 'Retry when connected.'}`);
    } finally { inFlight.current = false; setBusy(false); }
  }

  function draftValue(draftKey: string, source: string, canonical: string): string {
    const entry = builderDrafts[draftKey];
    return entry && entry.source === source ? entry.text : canonical;
  }

  function clearDraftValue(draftKey: string) {
    setBuilderDrafts(curr => {
      if (!(draftKey in curr)) return curr;
      const next = { ...curr };
      delete next[draftKey];
      return next;
    });
  }

  function updatePrimaryMetric(patch: { name?: string; unit?: string; direction?: 'minimize' | 'maximize' }) {
    setFields(curr => {
      const parsed = parseJsonArray(curr.metrics);
      if (!parsed.ok) return curr;
      const first = record(parsed.items[0]) ? parsed.items[0] : {};
      const prevName = typeof first.name === 'string' ? first.name : '';
      const nextFirst = {
        ...first,
        name: patch.name !== undefined ? patch.name : prevName,
        unit: patch.unit !== undefined ? patch.unit : (typeof first.unit === 'string' ? first.unit : 'unit'),
        direction: patch.direction !== undefined
          ? patch.direction
          : (first.direction === 'minimize' ? 'minimize' : 'maximize'),
      };
      const restMetrics = parsed.items.slice(1);
      const next: Record<string, string> = {
        ...curr,
        metrics: JSON.stringify([nextFirst, ...restMetrics], null, 2),
      };
      if (patch.name !== undefined && patch.name !== prevName) {
        const stillDeclaredElsewhere = restMetrics.some(item => record(item) && item.name === prevName);
        if (!stillDeclaredElsewhere) {
          const qc = parseJsonArray(curr.quality_constraints);
          if (qc.ok && qc.items.some(item => record(item) && item.metric === prevName)) {
            next.quality_constraints = JSON.stringify(
              qc.items.map(item => (record(item) && item.metric === prevName ? { ...item, metric: patch.name } : item)),
              null,
              2,
            );
          }
          const sc = parseJsonArray(curr.stop_conditions);
          if (sc.ok && sc.items.some(item => record(item) && item.metric === prevName)) {
            next.stop_conditions = JSON.stringify(
              sc.items.map(item => (record(item) && item.metric === prevName ? { ...item, metric: patch.name } : item)),
              null,
              2,
            );
          }
        }
      }
      return next;
    });
  }

  function updateGuardrailRelation(relation: string) {
    clearDraftValue('guardrailThreshold');
    setFields(curr => {
      const qc = parseJsonArray(curr.quality_constraints);
      if (!qc.ok) return curr;
      if (relation === 'none') {
        return { ...curr, quality_constraints: JSON.stringify([], null, 2) };
      }
      const metrics = parseJsonArray(curr.metrics);
      const metricName = metrics.ok && record(metrics.items[0]) && typeof metrics.items[0].name === 'string' && metrics.items[0].name
        ? metrics.items[0].name
        : (record(qc.items[0]) && typeof qc.items[0].metric === 'string' ? qc.items[0].metric : 'metric');
      const first = record(qc.items[0]) ? qc.items[0] : {};
      const threshold = typeof first.threshold === 'number' && Number.isFinite(first.threshold) ? first.threshold : 0;
      return {
        ...curr,
        quality_constraints: JSON.stringify([{ ...first, metric: metricName, relation, threshold }, ...qc.items.slice(1)], null, 2),
      };
    });
  }

  function updateGuardrailThreshold(raw: string) {
    const qcNow = parseJsonArray(fields.quality_constraints);
    if (!qcNow.ok) return;
    if (raw.trim() === '') {
      setBuilderDrafts(curr => ({ ...curr, guardrailThreshold: { source: fields.quality_constraints, text: '' } }));
      return;
    }
    const threshold = Number(raw);
    if (!Number.isFinite(threshold)) return;
    const metricsNow = parseJsonArray(fields.metrics);
    const fallbackMetric = metricsNow.ok && record(metricsNow.items[0]) && typeof metricsNow.items[0].name === 'string' && metricsNow.items[0].name
      ? metricsNow.items[0].name
      : (record(qcNow.items[0]) && typeof qcNow.items[0].metric === 'string' ? qcNow.items[0].metric : 'metric');
    const firstNow = record(qcNow.items[0]) ? qcNow.items[0] : {};
    const relNow = typeof firstNow.relation === 'string' ? firstNow.relation : '<=';
    const predictedJson = JSON.stringify([{ ...firstNow, metric: fallbackMetric, relation: relNow, threshold }, ...qcNow.items.slice(1)], null, 2);
    setBuilderDrafts(curr => ({ ...curr, guardrailThreshold: { source: predictedJson, text: raw } }));
    setFields(curr => {
      const qc = parseJsonArray(curr.quality_constraints);
      if (!qc.ok) return curr;
      const metrics = parseJsonArray(curr.metrics);
      const metricName = metrics.ok && record(metrics.items[0]) && typeof metrics.items[0].name === 'string' && metrics.items[0].name
        ? metrics.items[0].name
        : (record(qc.items[0]) && typeof qc.items[0].metric === 'string' ? qc.items[0].metric : 'metric');
      const first = record(qc.items[0]) ? qc.items[0] : {};
      const relation = typeof first.relation === 'string' ? first.relation : '<=';
      return {
        ...curr,
        quality_constraints: JSON.stringify([{ ...first, metric: metricName, relation, threshold }, ...qc.items.slice(1)], null, 2),
      };
    });
  }

  function updateBudgetNumber(fieldKey: 'max_candidates' | 'max_generations' | 'compute_budget', raw: string, integerOnly: boolean) {
    const budgetNow = parseJsonRecord(fields.budget);
    if (!budgetNow.ok) return;
    if (raw.trim() === '') {
      setBuilderDrafts(curr => ({ ...curr, [fieldKey]: { source: fields.budget, text: '' } }));
      return;
    }
    const parsedNum = Number(raw);
    if (!Number.isFinite(parsedNum)) return;
    const normalized = integerOnly ? Math.max(1, Math.trunc(parsedNum)) : Math.max(0, parsedNum);
    const predictedJson = JSON.stringify({ ...budgetNow.value, [fieldKey]: normalized }, null, 2);
    setBuilderDrafts(curr => ({ ...curr, [fieldKey]: { source: predictedJson, text: raw } }));
    setFields(curr => {
      const b = parseJsonRecord(curr.budget);
      if (!b.ok) return curr;
      return { ...curr, budget: JSON.stringify({ ...b.value, [fieldKey]: normalized }, null, 2) };
    });
  }

  function updateSeedsInput(raw: string) {
    const tokens = raw.split(',').map(part => part.trim()).filter(Boolean);
    if (tokens.every(token => /^-?\d+$/.test(token) && Number.isSafeInteger(Number(token)))) {
      const nextJson = JSON.stringify(tokens.map(token => Number.parseInt(token, 10)), null, 2);
      setBuilderDrafts(curr => ({ ...curr, seeds: { source: nextJson, text: raw } }));
      setFields(curr => ({ ...curr, seeds: nextJson }));
    } else {
      setBuilderDrafts(curr => ({ ...curr, seeds: { source: fields.seeds, text: raw } }));
    }
  }

  function toggleRegisteredTransform(name: string, version: string, checked: boolean) {
    setFields(curr => {
      const parsed = parseJsonArray(curr.allowed_transforms);
      if (!parsed.ok) return curr;
      const custom = parsed.items.filter(
        item => !REGISTERED_V1_TRANSFORMS.some(reg => record(item) && item.name === reg.name && item.version === reg.version),
      );
      const nextRegistered = REGISTERED_V1_TRANSFORMS.flatMap(reg => {
        const existing = parsed.items.find(item => record(item) && item.name === reg.name && item.version === reg.version);
        if (reg.name === name && reg.version === version) {
          return checked ? [existing ?? { name: reg.name, version: reg.version }] : [];
        }
        return existing ? [existing] : [];
      });
      return {
        ...curr,
        allowed_transforms: JSON.stringify([...nextRegistered, ...custom], null, 2),
      };
    });
  }

  const isSavedNotice = notice.includes('ProblemSpec saved') || notice.includes('Frozen snapshot loaded') || notice.includes('loaded');
  const isErrorNotice = notice.includes('Could not save') || notice.includes('Invalid') ||
    notice.includes('before continuing');
  const isDefaultDraftNotice = !notice || notice === 'Draft (unfrozen)';
  const manifestSummary = step === 2 ? summarizeManifest(fields) : null;
  const metricsParsed = step === 2 ? parseJsonArray(fields.metrics) : { ok: false as const };
  const firstMetricObj = metricsParsed.ok && record(metricsParsed.items[0]) ? metricsParsed.items[0] : null;
  const primaryMetricName = typeof firstMetricObj?.name === 'string' ? firstMetricObj.name : '';
  const primaryMetricUnit = typeof firstMetricObj?.unit === 'string' ? firstMetricObj.unit : '';
  const primaryMetricDirection = firstMetricObj?.direction === 'minimize' ? 'minimize' : 'maximize';
  const guardrailsParsed = step === 2 ? parseJsonArray(fields.quality_constraints) : { ok: false as const };
  const firstGuardrailObj = guardrailsParsed.ok && record(guardrailsParsed.items[0]) ? guardrailsParsed.items[0] : null;
  const guardrailRelation = firstGuardrailObj && typeof firstGuardrailObj.relation === 'string' ? firstGuardrailObj.relation : 'none';
  const canonicalGuardrailThreshold = firstGuardrailObj && typeof firstGuardrailObj.threshold === 'number' ? String(firstGuardrailObj.threshold) : '';
  const hardwareParsed = step === 2 ? parseJsonRecord(fields.hardware) : { ok: false as const };
  const hardwareTarget = hardwareParsed.ok && typeof hardwareParsed.value.target === 'string' ? hardwareParsed.value.target : '';
  const backendParsed = step === 2 ? parseJsonRecord(fields.backend) : { ok: false as const };
  const backendName = backendParsed.ok && typeof backendParsed.value.name === 'string' ? backendParsed.value.name : '';
  const backendVersion = backendParsed.ok && typeof backendParsed.value.version === 'string' ? backendParsed.value.version : '';
  const budgetParsed = step === 2 ? parseJsonRecord(fields.budget) : { ok: false as const };
  const canonicalMaxCandidates = budgetParsed.ok && typeof budgetParsed.value.max_candidates === 'number' ? String(budgetParsed.value.max_candidates) : '';
  const canonicalMaxGenerations = budgetParsed.ok && typeof budgetParsed.value.max_generations === 'number' ? String(budgetParsed.value.max_generations) : '';
  const canonicalComputeBudget = budgetParsed.ok && typeof budgetParsed.value.compute_budget === 'number' ? String(budgetParsed.value.compute_budget) : '';
  const computeUnit = budgetParsed.ok && typeof budgetParsed.value.compute_unit === 'string' ? budgetParsed.value.compute_unit : '';
  const seedsParsed = step === 2 ? parseJsonArray(fields.seeds) : { ok: false as const };
  const seedsValid = seedsParsed.ok && seedsParsed.items.every(item => typeof item === 'number' && Number.isInteger(item));
  const canonicalSeeds = seedsValid ? seedsParsed.items.join(', ') : '';
  const transformsParsed = step === 2 ? parseJsonArray(fields.allowed_transforms) : { ok: false as const };
  const customTransformsCount = transformsParsed.ok
    ? transformsParsed.items.filter(item => !REGISTERED_V1_TRANSFORMS.some(reg => record(item) && item.name === reg.name && item.version === reg.version)).length
    : 0;

  return (
    <section aria-label="ProblemSpec editor" className="max-w-6xl mx-auto px-4 md:px-6 pt-2 pb-8 space-y-4">
      {/* Compact Combined Stepper + Status Header */}
      <header className="rounded-xl border border-border/60 bg-card px-4 py-3 shadow-xs space-y-2.5">
        <div className="flex flex-wrap items-center justify-between gap-2">
          <div className="flex items-center gap-2 min-w-0">
            <ShieldCheck size={16} className="text-primary shrink-0" aria-hidden="true" />
            <h2 className="text-sm font-semibold tracking-tight text-foreground">Research question &amp; rules</h2>
            <span className="hidden md:inline text-xs text-muted-foreground">
              — Pick a starting draft or write your own, then freeze before running checks.
            </span>
          </div>

          <div className="flex items-center gap-2 text-xs shrink-0">
            {isDefaultDraftNotice ? (
              <output aria-live="polite" className="text-[11px] text-muted-foreground font-medium px-2 py-0.5 rounded-md bg-muted/50 border border-border/50">
                {notice || 'Draft (unfrozen)'}
              </output>
            ) : null}
            <span className={`inline-flex items-center gap-1.5 px-2.5 py-0.5 rounded-md border text-[11px] font-medium ${
              snapshot
                ? 'border-emerald-500/30 bg-emerald-500/10 text-emerald-600 dark:text-emerald-400'
                : 'border-amber-500/30 bg-amber-500/10 text-amber-700 dark:text-amber-300'
            }`}>
              {snapshot ? <Lock size={11} /> : <FileCode2 size={11} />}
              {snapshot ? `Frozen v${snapshot.version}` : (parent ? `Revision of ${parent}` : 'New research problem')}
            </span>
          </div>
        </div>

        <ol aria-label="ProblemSpec setup progress" className="grid grid-cols-3 gap-2 text-xs font-medium text-center">
          {(['Scope & question', 'Experiment manifest', 'Review & freeze'] as const).map((label, index) => {
            const itemStep = (index + 1) as 1 | 2 | 3;
            const current = step === itemStep;
            const complete = step > itemStep || snapshot !== null;
            return (
              <li
                key={label}
                aria-current={current ? 'step' : undefined}
                className={`py-1.5 px-2 rounded-lg border flex items-center justify-center gap-1.5 transition-colors ${
                  current
                    ? 'border-primary/50 bg-primary/8 text-primary font-semibold'
                    : complete
                      ? 'border-emerald-500/30 bg-emerald-500/5 text-emerald-700 dark:text-emerald-400'
                      : 'border-border/50 bg-background/60 text-muted-foreground'
                }`}
              >
                <span className="w-4 h-4 rounded-full bg-current/10 flex items-center justify-center text-[10px] tabular-nums">{itemStep}</span>
                <span>{label}</span>
              </li>
            );
          })}
        </ol>
      </header>

      {/* Live Status Notice (shown prominently whenever user acts, saves, or hits an error) */}
      {!isDefaultDraftNotice ? (
        <output
          aria-live="polite"
          className={`block text-xs px-3.5 py-2.5 rounded-lg border transition-all ${
            isErrorNotice
              ? 'border-destructive/40 bg-destructive/10 text-destructive'
              : isSavedNotice
              ? 'border-emerald-500/40 bg-emerald-500/10 text-emerald-700 dark:text-emerald-300 font-medium'
              : 'border-border/60 bg-muted/30 text-muted-foreground'
          }`}
        >
          <span className="inline-flex items-center gap-2">
            {isErrorNotice ? <AlertCircle size={14} /> : isSavedNotice ? <CheckCircle2 size={14} /> : null}
            {notice}
          </span>
        </output>
      ) : null}

      {/* Frozen Snapshot Receipt Card */}
      {snapshot ? (
        <div className="p-5 rounded-xl border border-emerald-500/30 bg-emerald-500/5 space-y-4 shadow-xs">
          <div className="flex flex-wrap items-center justify-between gap-3 border-b border-emerald-500/20 pb-3">
            <div className="flex items-center gap-2">
              <ShieldCheck className="text-emerald-500" size={20} />
              <h3 className="text-sm font-semibold text-foreground">
                Immutable ProblemSpec Frozen Receipt
              </h3>
              <Badge className="bg-emerald-500/20 text-emerald-700 dark:text-emerald-300 border-emerald-500/30 text-xs">
                Version {snapshot.version}
              </Badge>
            </div>
            <button
              type="button"
              className="inline-flex items-center gap-1.5 px-3 py-1.5 rounded-lg border border-border/60 bg-card hover:bg-muted text-xs font-medium shadow-xs transition-colors"
              onClick={() => {
                setParent(snapshot.spec_id); setSnapshot(null); setConfirmed(false); retry.current = null;
                setStep(1);
                setNotice('Revision draft. The previous snapshot and campaign remain unchanged.');
              }}
            >
              <RefreshCw size={12} />
              Create revision
            </button>
          </div>

          <div className="grid gap-3 sm:grid-cols-2 text-xs">
            <div className="p-2.5 rounded-lg bg-card/80 border border-border/50">
              <span className="text-muted-foreground block text-[11px] font-medium mb-1">Spec identifier</span>
              <code className="font-mono text-foreground font-semibold">{snapshot.spec_id}</code>
            </div>
            <div className="p-2.5 rounded-lg bg-card/80 border border-border/50">
              <span className="text-muted-foreground block text-[11px] font-medium mb-1">Campaign identifier</span>
              <code className="font-mono text-foreground font-semibold">{snapshot.campaign_id}</code>
            </div>
          </div>

          <div className="p-3 rounded-lg bg-card/90 border border-border/60 space-y-1.5">
            <div className="flex items-center justify-between">
              <span className="text-muted-foreground text-[11px] font-medium">Content manifest SHA-256</span>
              <button
                type="button"
                onClick={() => {
                  void navigator.clipboard.writeText(snapshot.content_hash);
                  setCopiedHash(true);
                  setTimeout(() => setCopiedHash(false), 2000);
                }}
                className="inline-flex items-center gap-1 text-[11px] text-primary hover:underline"
              >
                {copiedHash ? <Check size={11} /> : <Copy size={11} />}
                {copiedHash ? 'Copied' : 'Copy'}
              </button>
            </div>
            <p className="font-mono text-xs text-foreground break-all select-all">
              {snapshot.content_hash}
            </p>
          </div>

          <div className="p-2.5 rounded-lg bg-amber-500/10 border border-amber-500/20 text-xs text-amber-700 dark:text-amber-400">
            <output className="block">{readinessNotice}</output>
          </div>

          <div className="flex flex-wrap items-center justify-between gap-3 pt-2 border-t border-emerald-500/20">
            <span className="text-xs text-emerald-700 dark:text-emerald-300 font-medium">
              ProblemSpec frozen and committed into SHA-256 ledger.
            </span>
            <div className="flex flex-wrap items-center gap-2">
              <Link
                href={`/reports?spec_id=${encodeURIComponent(snapshot.spec_id)}`}
                className="inline-flex items-center gap-1.5 px-3 py-1.5 rounded-lg border border-emerald-600/30 bg-card hover:bg-muted text-foreground text-xs font-medium shadow-xs transition-colors"
              >
                <span>Open Evolution in Reports</span>
                <ArrowRight size={13} />
              </Link>
              <Link
                href={`/compatibility?spec_id=${encodeURIComponent(snapshot.spec_id)}`}
                className="inline-flex items-center gap-1.5 px-3 py-1.5 rounded-lg bg-emerald-600 hover:bg-emerald-700 text-white text-xs font-semibold shadow-xs transition-colors"
              >
                <span>Proceed to Step 3: Check Compatibility</span>
                <ArrowRight size={13} />
              </Link>
            </div>
          </div>
        </div>
      ) : null}

      {snapshot && showEvolution ? <EvolutionPanel key={snapshot.spec_id} specId={snapshot.spec_id} /> : null}

      {/* 2-Column Responsive Workbench Grid */}
      <div className="problem-spec-workbench-grid grid grid-cols-1 lg:grid-cols-12 gap-6 items-start">
        <div className="problem-spec-main-col lg:col-span-8 space-y-6">
          {/* Main Spec Editor Form */}
          <form onSubmit={event => { if (step === 3) void freeze(event); else { event.preventDefault(); continueSetup(); } }} className="space-y-6">
        <fieldset disabled={busy || snapshot !== null} className="space-y-6">
          {/* Card 1: Core Research Identity */}
          {step === 1 ? <div className="p-4 rounded-xl border border-border/60 bg-card shadow-xs space-y-3.5">
            <div className="flex items-center justify-between border-b border-border/40 pb-2">
              <div className="flex items-center gap-2">
                <FileCode2 size={15} className="text-primary" />
                <h3 className="text-sm font-semibold text-foreground">1. Core Research Identity</h3>
              </div>
              <button
                type="button"
                onClick={loadBenchmarkTemplate}
                className="text-[11px] px-2 py-0.5 rounded border border-border/60 bg-background hover:bg-muted text-muted-foreground hover:text-foreground font-medium transition-colors cursor-pointer"
              >
                Use 3DGS question
              </button>
            </div>

            {/* Quick Start Research Presets */}
            <div className="space-y-1.5">
              <span className="text-xs font-medium text-muted-foreground block">
                Quick-start drafts (click one to prefill, then review before freezing):
              </span>
              <div className="grid grid-cols-1 sm:grid-cols-2 gap-2">
                <button
                  type="button"
                  onClick={fillRegisteredCpuPilotManifest}
                  className="p-2.5 text-left rounded-lg border-2 border-primary/45 bg-primary/5 hover:bg-primary/10 hover:border-primary/70 transition-all group cursor-pointer shadow-2xs"
                >
                  <div className="flex items-center justify-between mb-0.5 gap-1">
                    <span className="text-xs font-semibold text-primary">
                      Linear Attention CPU Pilot
                    </span>
                    <Badge variant="outline" className="text-[9px] py-0 h-4 border-primary/50 bg-primary/10 text-primary">Draft · CPU Pilot</Badge>
                  </div>
                  <p className="text-[11px] text-muted-foreground line-clamp-2">
                    Recommended: registered Phase 5 synthetic feature-map protocol (CPU, float64)
                  </p>
                </button>
                <button
                  type="button"
                  onClick={fillValid3DGSManifest}
                  className="p-2.5 text-left rounded-lg border border-border/70 bg-muted/20 hover:bg-primary/5 hover:border-primary/40 transition-all group cursor-pointer"
                >
                  <div className="flex items-center justify-between mb-0.5 gap-1">
                    <span className="text-xs font-semibold text-foreground group-hover:text-primary">
                      3DGS Compression
                    </span>
                    <Badge variant="outline" className="text-[9px] py-0 h-4">Draft example</Badge>
                  </div>
                  <p className="text-[11px] text-muted-foreground line-clamp-2">
                    Gaussian Splatting anchor latent entropy coding
                  </p>
                </button>
                <button
                  type="button"
                  onClick={fillValidAttentionManifest}
                  className="p-2.5 text-left rounded-lg border border-border/70 bg-muted/20 hover:bg-primary/5 hover:border-primary/40 transition-all group cursor-pointer"
                >
                  <div className="flex items-center justify-between mb-0.5 gap-1">
                    <span className="text-xs font-semibold text-foreground group-hover:text-primary">
                      Scaled Attention
                    </span>
                    <Badge variant="outline" className="text-[9px] py-0 h-4">Draft · Transformer</Badge>
                  </div>
                  <p className="text-[11px] text-muted-foreground line-clamp-2">
                    Causal attention latency across sequence lengths
                  </p>
                </button>
                <button
                  type="button"
                  onClick={fillValidQuantumGravityManifest}
                  className="p-2.5 text-left rounded-lg border border-border/70 bg-muted/20 hover:bg-primary/5 hover:border-primary/40 transition-all group cursor-pointer"
                >
                  <div className="flex items-center justify-between mb-0.5 gap-1">
                    <span className="text-xs font-semibold text-foreground group-hover:text-primary">
                      Quantum Gravity
                    </span>
                    <Badge variant="outline" className="text-[9px] py-0 h-4">Draft · Theory</Badge>
                  </div>
                  <p className="text-[11px] text-muted-foreground line-clamp-2">
                    Ashtekar connection commutators & invariants
                  </p>
                </button>
              </div>
            </div>

            <div className="space-y-3">
              <label htmlFor="problem-spec-task" className="block text-xs font-medium text-foreground">
                Research question
                <textarea
                  id="problem-spec-task"
                  name="task"
                  required
                  maxLength={500}
                  value={task}
                  onChange={e => setTask(e.target.value)}
                  placeholder="e.g. Measure causal attention latency under generalized category discovery"
                  className="mt-1 block w-full rounded-lg border border-border/70 bg-background px-3 py-1.5 text-sm shadow-xs focus:ring-1 focus:ring-primary focus:border-primary placeholder:text-muted-foreground/70"
                  rows={2}
                />
              </label>

              <div className="grid gap-3 sm:grid-cols-2">
                <label htmlFor="problem-spec-family" className="block text-xs font-medium text-foreground">
                  Method family
                  <input
                    id="problem-spec-family"
                    name="family"
                    required
                    maxLength={100}
                    value={family}
                    onChange={e => setFamily(e.target.value)}
                    placeholder="e.g. attention, spectral_geometry"
                    className="mt-1 block w-full rounded-lg border border-border/70 bg-background px-3 py-2 text-sm shadow-xs focus:ring-1 focus:ring-primary focus:border-primary placeholder:text-muted-foreground/70"
                  />
                </label>

                <label htmlFor="problem-spec-dtype" className="block text-xs font-medium text-foreground">
                  Dtype
                  <select
                    id="problem-spec-dtype"
                    name="dtype"
                    value={dtype}
                    onChange={e => setDtype(e.target.value)}
                    className="mt-1 block w-full rounded-lg border border-border/70 bg-background px-3 py-2 text-sm shadow-xs focus:ring-1 focus:ring-primary focus:border-primary"
                  >
                    {['float32', 'float64', 'bfloat16'].map(value => <option key={value}>{value}</option>)}
                  </select>
                </label>
              </div>
            </div>
          </div> : null}

          {/* Card 2: Manifests & Constraint Specifications */}
          {step === 2 ? <div className="p-5 rounded-xl border border-border/60 bg-card shadow-xs space-y-5">
            <div className="flex flex-wrap items-center justify-between gap-2 border-b border-border/40 pb-2.5">
              <div className="flex items-center gap-2">
                <Boxes size={16} className="text-primary" />
                <h3 className="text-sm font-semibold text-foreground">2. Manifests & Environment Specifications</h3>
              </div>
              <div className="flex items-center gap-3">
                <span className="text-xs text-muted-foreground tabular-nums">13 JSON fields</span>
                {snapshot === null ? (
                  <button
                    type="button"
                    onClick={formatAllJsonFields}
                    className="text-xs px-2.5 py-1 rounded-md border border-border/60 hover:bg-muted text-foreground font-medium transition-colors"
                  >
                    Format all JSON
                  </button>
                ) : null}
              </div>
            </div>

            {manifestSummary ? (
              <dl aria-label="Plain-English manifest overview" className="grid gap-2 sm:grid-cols-2 lg:grid-cols-4 text-xs">
                <div className="rounded-lg border border-border/50 bg-muted/25 px-3 py-2">
                  <dt className="text-[11px] font-medium text-muted-foreground">Primary metric</dt>
                  <dd className="mt-0.5 font-medium text-foreground truncate" title={manifestSummary.firstMetric}>{manifestSummary.firstMetric}</dd>
                </div>
                <div className="rounded-lg border border-border/50 bg-muted/25 px-3 py-2">
                  <dt className="text-[11px] font-medium text-muted-foreground">Baselines</dt>
                  <dd className="mt-0.5 font-medium text-foreground truncate" title={manifestSummary.baselineLabel}>{manifestSummary.baselineLabel}</dd>
                </div>
                <div className="rounded-lg border border-border/50 bg-muted/25 px-3 py-2">
                  <dt className="text-[11px] font-medium text-muted-foreground">Runtime</dt>
                  <dd className="mt-0.5 font-mono text-[11px] font-medium text-foreground truncate" title={manifestSummary.runtimeLabel}>{manifestSummary.runtimeLabel}</dd>
                </div>
                <div className="rounded-lg border border-border/50 bg-muted/25 px-3 py-2">
                  <dt className="text-[11px] font-medium text-muted-foreground">Budget &amp; seeds</dt>
                  <dd className="mt-0.5 font-mono tabular-nums text-[11px] font-medium text-foreground truncate" title={manifestSummary.budgetLabel}>{manifestSummary.budgetLabel}</dd>
                </div>
              </dl>
            ) : null}

            <p className="text-xs text-muted-foreground">
              Configure common experiment bounds visually or edit the 13 JSON sections directly below. Template values are scaffolding, not verified inputs.
            </p>

            {/* Visual Manifest Builder */}
            <section
              aria-label="Visual manifest builder"
              className="border border-border/60 bg-muted/15 rounded-xl p-4 space-y-4"
            >
              <div className="flex flex-wrap items-baseline justify-between gap-2 border-b border-border/40 pb-2">
                <h4 className="text-xs font-semibold text-foreground">Visual manifest builder</h4>
                <span className="text-[11px] text-muted-foreground">
                  Two-way synced with raw JSON · artifact SHA-256 hashes remain pinned below
                </span>
              </div>

              {/* Group 1: Primary Metric */}
              <div className="rounded-lg border border-border/50 bg-card/70 p-3 space-y-2.5">
                <div className="flex flex-wrap items-center justify-between gap-2">
                  <span className="text-xs font-semibold text-foreground">Primary metric</span>
                  {metricsParsed.ok && metricsParsed.items.length > 1 ? (
                    <span className="text-[11px] text-muted-foreground tabular-nums">
                      +{metricsParsed.items.length - 1} secondary metric(s) preserved
                    </span>
                  ) : null}
                </div>
                {!metricsParsed.ok ? (
                  <output aria-live="polite" className="text-[11px] text-amber-700 dark:text-amber-300 flex items-center gap-1.5">
                    <AlertCircle size={12} aria-hidden="true" className="shrink-0" />
                    <span>Metrics JSON cannot be parsed right now. Fix the raw JSON section below to edit visually.</span>
                  </output>
                ) : null}
                <div className="grid gap-3 sm:grid-cols-3">
                  <div>
                    <label htmlFor="builder-metric-name" className="block text-xs font-medium text-foreground mb-1">
                      Primary metric name
                    </label>
                    <input
                      id="builder-metric-name"
                      type="text"
                      disabled={!metricsParsed.ok}
                      value={primaryMetricName}
                      onChange={e => updatePrimaryMetric({ name: e.target.value })}
                      placeholder="e.g. rate_distortion_psnr"
                      className="block w-full rounded-md border border-border/70 bg-background px-2.5 py-1.5 font-mono text-xs shadow-2xs focus:ring-1 focus:ring-primary focus:border-primary disabled:opacity-50"
                    />
                  </div>
                  <div>
                    <label htmlFor="builder-metric-unit" className="block text-xs font-medium text-foreground mb-1">
                      Metric unit
                    </label>
                    <input
                      id="builder-metric-unit"
                      type="text"
                      disabled={!metricsParsed.ok}
                      value={primaryMetricUnit}
                      onChange={e => updatePrimaryMetric({ unit: e.target.value })}
                      placeholder="e.g. dB, ms"
                      className="block w-full rounded-md border border-border/70 bg-background px-2.5 py-1.5 font-mono text-xs shadow-2xs focus:ring-1 focus:ring-primary focus:border-primary disabled:opacity-50"
                    />
                  </div>
                  <div>
                    <label htmlFor="builder-metric-direction" className="block text-xs font-medium text-foreground mb-1">
                      Optimization direction
                    </label>
                    <select
                      id="builder-metric-direction"
                      disabled={!metricsParsed.ok}
                      value={primaryMetricDirection}
                      onChange={e => updatePrimaryMetric({ direction: e.target.value === 'minimize' ? 'minimize' : 'maximize' })}
                      className="block w-full rounded-md border border-border/70 bg-background px-2.5 py-1.5 text-xs shadow-2xs focus:ring-1 focus:ring-primary focus:border-primary disabled:opacity-50"
                    >
                      <option value="minimize">minimize</option>
                      <option value="maximize">maximize</option>
                    </select>
                  </div>
                </div>
              </div>

              {/* Group 2 & 3: Primary Guardrail + Pinned Runtime */}
              <div className="grid gap-3 md:grid-cols-12">
                <div className="md:col-span-5 rounded-lg border border-border/50 bg-card/70 p-3 space-y-2.5">
                  <span className="block text-xs font-semibold text-foreground">Primary guardrail</span>
                  {!guardrailsParsed.ok ? (
                    <output aria-live="polite" className="text-[11px] text-amber-700 dark:text-amber-300 flex items-center gap-1.5">
                      <AlertCircle size={12} aria-hidden="true" className="shrink-0" />
                      <span>Quality constraints JSON cannot be parsed right now. Fix the raw JSON section below to edit visually.</span>
                    </output>
                  ) : null}
                  <div className="grid gap-3 sm:grid-cols-2">
                    <div>
                      <label htmlFor="builder-guardrail-relation" className="block text-xs font-medium text-foreground mb-1">
                        Guardrail relation
                      </label>
                      <select
                        id="builder-guardrail-relation"
                        disabled={!guardrailsParsed.ok}
                        value={guardrailRelation}
                        onChange={e => updateGuardrailRelation(e.target.value)}
                        className="block w-full rounded-md border border-border/70 bg-background px-2.5 py-1.5 font-mono text-xs shadow-2xs focus:ring-1 focus:ring-primary focus:border-primary disabled:opacity-50"
                      >
                        <option value="none">none</option>
                        <option value="<=">&lt;=</option>
                        <option value=">=">&gt;=</option>
                        {!['none', '<=', '>='].includes(guardrailRelation) ? (
                          <option value={guardrailRelation}>{guardrailRelation}</option>
                        ) : null}
                      </select>
                    </div>
                    <div>
                      <label htmlFor="builder-guardrail-threshold" className="block text-xs font-medium text-foreground mb-1">
                        Guardrail threshold
                      </label>
                      <input
                        id="builder-guardrail-threshold"
                        type="number"
                        step="any"
                        disabled={!guardrailsParsed.ok || guardrailRelation === 'none'}
                        value={draftValue('guardrailThreshold', fields.quality_constraints, canonicalGuardrailThreshold)}
                        onChange={e => updateGuardrailThreshold(e.target.value)}
                        onBlur={() => clearDraftValue('guardrailThreshold')}
                        placeholder={guardrailRelation === 'none' ? 'Disabled' : '0.0'}
                        className="block w-full rounded-md border border-border/70 bg-background px-2.5 py-1.5 font-mono tabular-nums text-xs shadow-2xs focus:ring-1 focus:ring-primary focus:border-primary disabled:opacity-50"
                      />
                    </div>
                  </div>
                </div>

                <div className="md:col-span-7 rounded-lg border border-border/50 bg-card/70 p-3 space-y-2.5">
                  <span className="block text-xs font-semibold text-foreground">Pinned runtime</span>
                  {!hardwareParsed.ok || !backendParsed.ok ? (
                    <output aria-live="polite" className="text-[11px] text-amber-700 dark:text-amber-300 flex items-center gap-1.5">
                      <AlertCircle size={12} aria-hidden="true" className="shrink-0" />
                      <span>Hardware or backend JSON cannot be parsed right now. Fix the raw JSON section below to edit visually.</span>
                    </output>
                  ) : null}
                  <div className="grid gap-3 sm:grid-cols-3">
                    <div>
                      <label htmlFor="builder-hardware-target" className="block text-xs font-medium text-foreground mb-1">
                        Hardware target
                      </label>
                      <input
                        id="builder-hardware-target"
                        type="text"
                        disabled={!hardwareParsed.ok}
                        value={hardwareTarget}
                        onChange={e => setFields(curr => {
                          const hw = parseJsonRecord(curr.hardware);
                          return hw.ok ? { ...curr, hardware: JSON.stringify({ ...hw.value, target: e.target.value }, null, 2) } : curr;
                        })}
                        placeholder="e.g. cpu"
                        className="block w-full rounded-md border border-border/70 bg-background px-2.5 py-1.5 font-mono text-xs shadow-2xs focus:ring-1 focus:ring-primary focus:border-primary disabled:opacity-50"
                      />
                    </div>
                    <div>
                      <label htmlFor="builder-backend-framework" className="block text-xs font-medium text-foreground mb-1">
                        Backend framework
                      </label>
                      <input
                        id="builder-backend-framework"
                        type="text"
                        disabled={!backendParsed.ok}
                        value={backendName}
                        onChange={e => setFields(curr => {
                          const be = parseJsonRecord(curr.backend);
                          return be.ok ? { ...curr, backend: JSON.stringify({ ...be.value, name: e.target.value }, null, 2) } : curr;
                        })}
                        placeholder="e.g. pytorch"
                        className="block w-full rounded-md border border-border/70 bg-background px-2.5 py-1.5 font-mono text-xs shadow-2xs focus:ring-1 focus:ring-primary focus:border-primary disabled:opacity-50"
                      />
                    </div>
                    <div>
                      <label htmlFor="builder-backend-version" className="block text-xs font-medium text-foreground mb-1">
                        Backend version
                      </label>
                      <input
                        id="builder-backend-version"
                        type="text"
                        disabled={!backendParsed.ok}
                        value={backendVersion}
                        onChange={e => setFields(curr => {
                          const be = parseJsonRecord(curr.backend);
                          return be.ok ? { ...curr, backend: JSON.stringify({ ...be.value, version: e.target.value }, null, 2) } : curr;
                        })}
                        placeholder="e.g. 2.4.0"
                        className="block w-full rounded-md border border-border/70 bg-background px-2.5 py-1.5 font-mono text-xs shadow-2xs focus:ring-1 focus:ring-primary focus:border-primary disabled:opacity-50"
                      />
                    </div>
                  </div>
                </div>
              </div>

              {/* Group 4: Search Budget & Seeds */}
              <div className="rounded-lg border border-border/50 bg-card/70 p-3 space-y-2.5">
                <div className="flex flex-wrap items-center justify-between gap-2">
                  <span className="text-xs font-semibold text-foreground">Search budget &amp; seeds</span>
                  {computeUnit ? (
                    <span className="text-[11px] font-mono text-muted-foreground">Unit: {computeUnit}</span>
                  ) : null}
                </div>
                {!budgetParsed.ok || !seedsValid ? (
                  <output aria-live="polite" className="text-[11px] text-amber-700 dark:text-amber-300 flex items-center gap-1.5">
                    <AlertCircle size={12} aria-hidden="true" className="shrink-0" />
                    <span>Budget or seeds JSON cannot be parsed right now. Fix the raw JSON section below to edit visually.</span>
                  </output>
                ) : null}
                <div className="grid gap-3 sm:grid-cols-2 lg:grid-cols-4">
                  <div>
                    <label htmlFor="builder-max-candidates" className="block text-xs font-medium text-foreground mb-1">
                      Max candidates
                    </label>
                    <input
                      id="builder-max-candidates"
                      type="number"
                      min={1}
                      disabled={!budgetParsed.ok}
                      value={draftValue('max_candidates', fields.budget, canonicalMaxCandidates)}
                      onChange={e => updateBudgetNumber('max_candidates', e.target.value, true)}
                      onBlur={() => clearDraftValue('max_candidates')}
                      className="block w-full rounded-md border border-border/70 bg-background px-2.5 py-1.5 font-mono tabular-nums text-xs shadow-2xs focus:ring-1 focus:ring-primary focus:border-primary disabled:opacity-50"
                    />
                  </div>
                  <div>
                    <label htmlFor="builder-max-generations" className="block text-xs font-medium text-foreground mb-1">
                      Max generations
                    </label>
                    <input
                      id="builder-max-generations"
                      type="number"
                      min={1}
                      disabled={!budgetParsed.ok}
                      value={draftValue('max_generations', fields.budget, canonicalMaxGenerations)}
                      onChange={e => updateBudgetNumber('max_generations', e.target.value, true)}
                      onBlur={() => clearDraftValue('max_generations')}
                      className="block w-full rounded-md border border-border/70 bg-background px-2.5 py-1.5 font-mono tabular-nums text-xs shadow-2xs focus:ring-1 focus:ring-primary focus:border-primary disabled:opacity-50"
                    />
                  </div>
                  <div>
                    <label htmlFor="builder-compute-budget" className="block text-xs font-medium text-foreground mb-1">
                      Compute budget
                    </label>
                    <input
                      id="builder-compute-budget"
                      type="number"
                      min={0}
                      step="any"
                      disabled={!budgetParsed.ok}
                      value={draftValue('compute_budget', fields.budget, canonicalComputeBudget)}
                      onChange={e => updateBudgetNumber('compute_budget', e.target.value, false)}
                      onBlur={() => clearDraftValue('compute_budget')}
                      className="block w-full rounded-md border border-border/70 bg-background px-2.5 py-1.5 font-mono tabular-nums text-xs shadow-2xs focus:ring-1 focus:ring-primary focus:border-primary disabled:opacity-50"
                    />
                  </div>
                  <div>
                    <label htmlFor="builder-run-seeds" className="block text-xs font-medium text-foreground mb-1">
                      Run seeds (comma-separated)
                    </label>
                    <input
                      id="builder-run-seeds"
                      type="text"
                      disabled={!seedsValid}
                      value={draftValue('seeds', fields.seeds, canonicalSeeds)}
                      onChange={e => updateSeedsInput(e.target.value)}
                      onBlur={() => clearDraftValue('seeds')}
                      placeholder="42, 1337"
                      className="block w-full rounded-md border border-border/70 bg-background px-2.5 py-1.5 font-mono tabular-nums text-xs shadow-2xs focus:ring-1 focus:ring-primary focus:border-primary disabled:opacity-50"
                    />
                  </div>
                </div>
              </div>

              {/* Group 5: Allowed DSL Transforms */}
              <div className="rounded-lg border border-border/50 bg-card/70 p-3 space-y-2.5">
                <div className="flex flex-wrap items-center justify-between gap-2">
                  <span className="text-xs font-semibold text-foreground">Allowed DSL transforms</span>
                  {customTransformsCount > 0 ? (
                    <span className="text-[11px] text-muted-foreground tabular-nums">
                      +{customTransformsCount} custom transform(s) preserved in raw JSON
                    </span>
                  ) : null}
                </div>
                {!transformsParsed.ok ? (
                  <output aria-live="polite" className="text-[11px] text-amber-700 dark:text-amber-300 flex items-center gap-1.5">
                    <AlertCircle size={12} aria-hidden="true" className="shrink-0" />
                    <span>Allowed transforms JSON cannot be parsed right now. Fix the raw JSON section below to edit visually.</span>
                  </output>
                ) : null}
                <div className="grid gap-2 sm:grid-cols-2">
                  {REGISTERED_V1_TRANSFORMS.map(reg => {
                    const checked = transformsParsed.ok && transformsParsed.items.some(
                      item => record(item) && item.name === reg.name && item.version === reg.version,
                    );
                    const inputId = `builder-transform-${reg.name}`;
                    return (
                      <label
                        key={`${reg.name}:${reg.version}`}
                        htmlFor={inputId}
                        className="flex items-center gap-2.5 rounded-md border border-border/60 bg-background px-3 py-2 text-xs cursor-pointer select-none hover:bg-muted/40 transition-colors"
                      >
                        <input
                          id={inputId}
                          type="checkbox"
                          disabled={!transformsParsed.ok}
                          checked={checked}
                          onChange={e => toggleRegisteredTransform(reg.name, reg.version, e.target.checked)}
                          className="h-3.5 w-3.5 rounded border-border text-primary focus:ring-primary disabled:opacity-50"
                        />
                        <span className="font-mono text-foreground">{reg.name}</span>
                        <span className="text-[11px] text-muted-foreground">v{reg.version}</span>
                      </label>
                    );
                  })}
                </div>
              </div>
            </section>

            <details open className="group rounded-xl border border-border/50 bg-background/40 p-4 space-y-4">
              <summary className="cursor-pointer select-none flex items-center justify-between text-xs font-semibold text-foreground">
                Advanced raw JSON manifest (13 sections) <span className="text-[11px] font-normal text-muted-foreground">Synced live with visual builder above</span>
              </summary>

              {(
                [
                  ['Objective & Guardrails', ['metrics', 'quality_constraints', 'stop_conditions']],
                  ['Data, Baselines & Environment', ['baselines', 'dataset', 'model', 'tokenizer', 'hardware', 'backend', 'evaluator']],
                  ['Search Bounds & Allowed Moves', ['seeds', 'budget', 'allowed_transforms']],
                ] as const
              ).map(([groupTitle, groupKeys]) => (
                <div key={groupTitle} className="space-y-3">
                  <h4 className="text-xs font-semibold text-muted-foreground border-b border-border/30 pb-1">
                    {groupTitle}
                  </h4>
                  <div className="grid gap-4 md:grid-cols-2">
                    {sections
                      .filter(([key]) => (groupKeys as readonly string[]).includes(key))
                      .map(([key, label, , description]) => {
                        const parseError = jsonFieldError(key);
                        const errorId = `problem-spec-section-${key}-error`;
                        return (
                          <div
                            key={key}
                            className={`p-3.5 rounded-lg border space-y-2 ${
                              parseError
                                ? 'border-destructive/60 bg-destructive/5'
                                : 'border-border/50 bg-background/50'
                            }`}
                          >
                            <div className="flex items-center justify-between gap-2">
                              <label
                                htmlFor={`problem-spec-section-${key}`}
                                className="block text-sm font-semibold text-foreground"
                              >
                                {label} (JSON)
                              </label>
                              <div className="flex items-center gap-2 shrink-0">
                                <span
                                  className={`text-[10px] font-medium ${
                                    parseError
                                      ? 'text-destructive'
                                      : 'text-emerald-600 dark:text-emerald-400'
                                  }`}
                                >
                                  {parseError ? 'Syntax error' : 'Valid JSON'}
                                </span>
                                <button
                                  type="button"
                                  onClick={() => formatJsonField(key, label)}
                                  className="text-xs text-muted-foreground hover:text-primary transition-colors underline"
                                  title="Format and validate JSON"
                                >
                                  Format
                                </button>
                              </div>
                            </div>
                            <p id={`problem-spec-section-${key}-help`} className="text-xs leading-relaxed text-muted-foreground">
                              {description}
                            </p>
                            <textarea
                              id={`problem-spec-section-${key}`}
                              name={`section_${key}`}
                              required
                              rows={4}
                              maxLength={32768}
                              spellCheck={false}
                              value={fields[key]}
                              onChange={e => setFields(current => ({ ...current, [key]: e.target.value }))}
                              aria-invalid={parseError ? true : undefined}
                              aria-describedby={parseError ? `problem-spec-section-${key}-help ${errorId}` : `problem-spec-section-${key}-help`}
                              className={`block w-full rounded-md border bg-card p-2 font-mono text-xs leading-relaxed shadow-2xs focus:ring-1 ${
                                parseError
                                  ? 'border-destructive focus:ring-destructive focus:border-destructive'
                                  : 'border-border/60 focus:ring-primary focus:border-primary'
                              }`}
                            />
                            {parseError ? (
                              <p id={errorId} role="alert" className="text-xs text-destructive flex items-center gap-1.5">
                                <AlertCircle size={12} aria-hidden="true" className="shrink-0" />
                                <span>{parseError}</span>
                              </p>
                            ) : null}
                          </div>
                        );
                      })}
                  </div>
                </div>
              ))}
            </details>
          </div> : null}

          {/* Card 3: Immutability Confirmation Gate */}
          {step === 3 ? <div className="space-y-4">
          <div className="p-4 rounded-xl border border-border/60 bg-card shadow-xs">
            <h3 className="text-sm font-semibold">Review the frozen scope</h3>
            <dl className="grid gap-3 sm:grid-cols-3 mt-3 text-xs">
              <div className="rounded-lg border border-border/50 bg-background/60 p-3 sm:col-span-3">
                <dt className="text-muted-foreground">Research question</dt>
                <dd className="mt-1 font-medium text-foreground">{task}</dd>
              </div>
              <div className="rounded-lg border border-border/50 bg-background/60 p-3">
                <dt className="text-muted-foreground">Method family</dt>
                <dd className="mt-1 font-medium text-foreground">{family}</dd>
              </div>
              <div className="rounded-lg border border-border/50 bg-background/60 p-3">
                <dt className="text-muted-foreground">Numeric dtype</dt>
                <dd className="mt-1 font-mono font-medium text-foreground">{dtype}</dd>
              </div>
              <div className="rounded-lg border border-border/50 bg-background/60 p-3">
                <dt className="text-muted-foreground">Manifest sections</dt>
                <dd className="mt-1 font-medium text-foreground">{sections.length} · syntax checked</dd>
              </div>
            </dl>
            <button type="button" onClick={() => setStep(2)} className="mt-3 text-xs text-primary hover:underline">
              Review manifest values
            </button>
          </div>
          <div className="p-4 rounded-xl border border-amber-500/30 bg-amber-500/5 space-y-3">
            <div className="flex items-start gap-3">
              <ShieldAlert className="text-amber-500 shrink-0 mt-0.5" size={18} />
              <div className="space-y-1">
                <h4 className="text-xs font-semibold text-foreground">Cryptographic Immutability Commitment</h4>
                <p className="text-xs text-muted-foreground">
                  Freezing a ProblemSpec commits these inputs into a SHA-256 content-addressed ledger. Once frozen, inputs cannot be modified in-place; modifications require generating a linked child revision.
                </p>
                <label htmlFor="problem-spec-confirmed" className="flex items-center gap-2.5 pt-1.5 cursor-pointer select-none">
                  <input
                    id="problem-spec-confirmed"
                    name="confirmed"
                    type="checkbox"
                    checked={confirmed}
                    onChange={e => setConfirmed(e.target.checked)}
                    className="h-4 w-4 rounded border-border text-primary focus:ring-primary cursor-pointer"
                  />
                  <span className="text-xs font-medium text-foreground">
                    I confirm these inputs should be frozen; edits require a new revision.
                  </span>
                </label>
              </div>
            </div>
          </div>
          </div> : null}
        </fieldset>

        {snapshot === null ? <div className={`flex items-center gap-3 ${step === 1 ? 'justify-end' : 'justify-between'}`}>
          {step > 1 ? <button
            type="button"
            disabled={busy}
            onClick={() => setStep(current => current === 3 ? 2 : 1)}
            className="px-4 py-2.5 rounded-lg border border-border/60 text-sm font-medium hover:bg-muted/50 disabled:opacity-40"
          >
            Back
          </button> : null}
          {step < 3 ? (
            <button type="button" onClick={continueSetup} className="px-5 py-2.5 rounded-lg bg-primary text-primary-foreground text-xs font-semibold hover:bg-primary/90">
              {step === 1 ? 'Continue to manifest' : 'Review and freeze'}
            </button>
          ) : (
            <div className="flex flex-wrap items-center justify-end gap-3">
              {!confirmed && !busy ? (
                <span className="text-xs text-muted-foreground">
                  Check the confirmation box above to enable freezing.
                </span>
              ) : null}
              <button
                type="submit"
                disabled={busy || snapshot !== null || !confirmed}
                className={`px-5 py-2.5 rounded-lg text-xs font-semibold inline-flex items-center gap-2 transition-all shadow-xs ${
                  snapshot
                    ? 'bg-muted text-muted-foreground cursor-not-allowed border border-border'
                    : !confirmed || busy
                    ? 'bg-primary/50 text-primary-foreground cursor-not-allowed opacity-50'
                    : 'bg-primary text-primary-foreground hover:bg-primary/90 cursor-pointer'
                }`}
              >
                {busy ? <RefreshCw className="animate-spin" size={14} /> : <Lock size={14} />}
                {busy ? 'Freezing Spec…' : snapshot ? 'Spec Frozen' : 'Freeze Spec'}
              </button>
            </div>
          )}
        </div> : null}
      </form>
    </div>

    {/* Right Column: Spec Integrity & Snapshot History */}
    <div className="problem-spec-side-col lg:col-span-4 space-y-5">
      <div className="rounded-xl border border-border/60 bg-card p-4 shadow-xs space-y-3">
        <div className="flex items-center gap-2 border-b border-border/40 pb-2">
          <Boxes size={15} className="text-primary" />
          <h3 className="text-sm font-semibold text-foreground">
            G1 ProblemSpec invariants
          </h3>
        </div>
        <div className="space-y-2 text-xs">
          <div className="flex items-center justify-between p-2 rounded-md bg-muted/30">
            <span className="text-muted-foreground">Identity & Dtype</span>
            <span className={`font-mono text-[11px] font-medium ${task && family ? 'text-emerald-600 dark:text-emerald-400' : 'text-amber-600 dark:text-amber-400'}`}>
              {task && family ? `${family} · ${dtype}` : 'Drafting'}
            </span>
          </div>
          <div className="flex items-center justify-between p-2 rounded-md bg-muted/30">
            <span className="text-muted-foreground">Manifest Specifications</span>
            <span className="font-mono tabular-nums text-[11px] font-medium text-primary">
              {sections.filter(([key]) => {
                try { JSON.parse(fields[key]); return true; } catch { return false; }
              }).length}/{sections.length} JSON readable
            </span>
          </div>
          <div className="flex items-center justify-between p-2 rounded-md bg-muted/30">
            <span className="text-muted-foreground">Invariant Gate</span>
            <span className={`font-mono text-[11px] font-medium ${snapshot ? 'text-emerald-600 dark:text-emerald-400' : 'text-muted-foreground'}`}>
              {snapshot ? `G1 Frozen (v${snapshot.version})` : 'G1 Pending Freeze'}
            </span>
          </div>
        </div>
      </div>

      {/* History Drawer */}
      <section aria-label="ProblemSpec history" className="rounded-xl border border-border/60 bg-card p-4 shadow-xs space-y-3">
        <div className="flex items-center justify-between border-b border-border/40 pb-2.5">
          <div className="flex items-center gap-2">
            <History size={16} className="text-primary" />
            <h3 className="text-sm font-semibold text-foreground">Snapshot history</h3>
          </div>
          <button
            type="button"
            disabled={busy}
            onClick={() => setRefresh(value => value + 1)}
            className="inline-flex items-center gap-1.5 px-2.5 py-1 rounded-md border border-border/50 text-[11px] font-medium hover:bg-muted transition-colors disabled:opacity-50"
          >
            <RefreshCw size={11} />
            Refresh history
          </button>
        </div>

        <output className="text-xs text-muted-foreground block">{historyNotice}</output>

        <ul className="space-y-1.5">
          {history.map(saved => (
            <li key={saved.spec_id}>
              <button
                type="button"
                disabled={busy}
                onClick={() => {
                  if (!snapshot && (task || family) && !window.confirm('Replace the unsaved draft with this saved snapshot?')) return;
                  inspect(saved);
                }}
                className={`w-full text-left p-2.5 rounded-lg border text-xs font-mono transition-colors flex items-center justify-between gap-2 ${
                  snapshot?.spec_id === saved.spec_id
                    ? 'border-primary/60 bg-primary/5 text-primary'
                    : 'border-border/40 hover:bg-muted/40 text-foreground'
                }`}
              >
                <span className="truncate max-w-[70%]">
                  {String(saved.definition.task)} · v{saved.version} · {saved.spec_id}
                </span>
                <span className="text-[10px] text-muted-foreground shrink-0 font-mono">
                  {saved.spec_id.slice(-8)}
                </span>
              </button>
            </li>
          ))}
        </ul>

        <div className="flex items-center justify-between pt-2">
          <button
            type="button"
            disabled={offset === 0 || busy}
            onClick={() => setOffset(value => Math.max(0, value - 20))}
            className="px-3 py-1.5 rounded-md border border-border/50 text-xs font-medium hover:bg-muted disabled:opacity-40 transition-colors"
          >
            Previous specs
          </button>
          <button
            type="button"
            disabled={offset + 20 >= total || busy}
            onClick={() => setOffset(value => value + 20)}
            className="px-3 py-1.5 rounded-md border border-border/50 text-xs font-medium hover:bg-muted disabled:opacity-40 transition-colors"
          >
            Next specs
          </button>
        </div>
      </section>
    </div>
  </div>
    </section>
  );
}
