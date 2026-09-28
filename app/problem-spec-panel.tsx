'use client';

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
} from 'lucide-react';
import { Badge } from '@/components/ui/badge';
import EvolutionPanel from './evolution-panel';

const sections = [
  ['metrics', 'Metrics and directions', [{ name: '', unit: '', direction: 'minimize' }], 'Name each measured outcome, its unit, and whether lower or higher is better.'],
  ['quality_constraints', 'Quality constraints', [], 'Set the maximum permitted quality loss or other guardrails against the baseline.'],
  ['baselines', 'Baseline artifacts', [{ name: '', version: '', sha256: '', readiness: 'unverified' }], 'Pin every comparison implementation by version and SHA-256; an unverified artifact is not evidence.'],
  ['dataset', 'Dataset and splits', { artifact_hash: '', version: '', splits: [{ role: 'search', split_hash: '' }] }, 'Pin the dataset and give search, validation, and protected holdout splits distinct identities.'],
  ['model', 'Model artifact or not_applicable with reason', { name: '', version: '', sha256: '', readiness: 'unverified' }, 'Identify the exact model revision, or explain why this experiment does not use one.'],
  ['tokenizer', 'Tokenizer artifact or not_applicable with reason', { name: '', version: '', sha256: '', readiness: 'unverified' }, 'Identify the exact tokenizer revision, or explain why tokenization is not applicable.'],
  ['hardware', 'Pinned hardware', { target: '' }, 'Record the hardware class and relevant device/runtime details for reproducibility.'],
  ['backend', 'Pinned backend', { name: '', version: '' }, 'Pin the numerical framework and version used by the evaluator.'],
  ['evaluator', 'Evaluator manifest', { name: '', protocol_version: '', implementation_hash: '', config_hash: '' }, 'Pin the evaluator protocol, implementation, and configuration hashes.'],
  ['seeds', 'Distinct run seeds', [], 'List the exact seeds used for each repeated run; do not reuse one run as multiple samples.'],
  ['budget', 'Search and compute limits', { max_candidates: 1, max_generations: 1, wall_time_ms: 1000, compute_budget: 1, compute_unit: 'CPU-seconds' }, 'Bound candidates, generations, elapsed time, and compute; these defaults are placeholders.'],
  ['allowed_transforms', 'Allowed transform names and versions', [], 'Allow only registered DSL transformations with explicit versions.'],
  ['stop_conditions', 'Metric stop conditions', [], 'Define objective thresholds or conditions that stop the search.'],
] as const;

type Snapshot = {
  spec_id: string; campaign_id: string; content_hash: string; version: number;
  parent_spec_id: string | null; definition: Record<string, unknown>;
};

function record(value: unknown): value is Record<string, unknown> {
  return value !== null && typeof value === 'object' && !Array.isArray(value);
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

export default function ProblemSpecPanel() {
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
  const inFlight = useRef(false);
  const retry = useRef<{ body: string; key: string } | null>(null);

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
    setNotice('Frozen snapshot loaded. Create a revision to edit; artifacts remain unverified.');
  }

  function continueSetup() {
    if (step === 1 && (!task.trim() || !family.trim())) {
      setNotice('Add a research question and method family before continuing.');
      return;
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

  function formatJsonField(key: string) {
    try {
      const parsed = JSON.parse(fields[key]);
      setFields(curr => ({ ...curr, [key]: JSON.stringify(parsed, null, 2) }));
    } catch { }
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
      setNotice('ProblemSpec saved. Artifact verification is still pending.');
    } catch (error) {
      setNotice(`Could not save ProblemSpec. Your draft is unchanged. ${error instanceof Error ? error.message : 'Retry when connected.'}`);
    } finally { inFlight.current = false; setBusy(false); }
  }

  const isSavedNotice = notice.includes('ProblemSpec saved') || notice.includes('Frozen snapshot loaded');
  const isErrorNotice = notice.includes('Could not save') || notice.includes('Invalid') ||
    notice.includes('before continuing');

  return (
    <section aria-label="ProblemSpec editor" className="max-w-5xl mx-auto p-4 md:p-8 space-y-6">
      {/* Header with Visual Status */}
      <header className="rounded-xl border border-border/60 bg-card p-5 shadow-xs space-y-3">
        <div className="flex flex-wrap items-center justify-between gap-3">
          <div className="flex items-center gap-2.5">
            <div className="w-8 h-8 rounded-lg bg-primary/10 border border-primary/20 flex items-center justify-center text-primary">
              <ShieldCheck size={18} />
            </div>
            <div>
              <div className="flex items-center gap-2">
                <h2 className="text-lg font-semibold tracking-tight">Problem Spec (G1)</h2>
                <Badge variant="outline" className="text-[11px] font-mono border-primary/30 text-primary">
                  Phase 5A Gate
                </Badge>
              </div>
          <p className="text-xs text-muted-foreground mt-0.5">
                Freeze your research question and reproducible inputs. Saving does not run experiments or verify artifacts.
              </p>
            </div>
          </div>

          <div className="flex items-center gap-2 text-xs">
            <span className={`inline-flex items-center gap-1.5 px-2.5 py-1 rounded-md border font-medium ${
              snapshot
                ? 'border-emerald-500/30 bg-emerald-500/10 text-emerald-600 dark:text-emerald-400'
                : 'border-amber-500/30 bg-amber-500/10 text-amber-600 dark:text-amber-400'
            }`}>
              {snapshot ? <Lock size={12} /> : <FileCode2 size={12} />}
              {snapshot ? `Frozen v${snapshot.version}` : (parent ? `Revision of ${parent}` : 'New research problem')}
            </span>
          </div>
        </div>

        <p className="text-xs text-muted-foreground/80 border-t border-border/40 pt-2.5">
          Replace template values with pinned experiment inputs. Continuing checks JSON syntax; freezing validates the manifest but does not verify artifact bytes or run experiments.
        </p>
      </header>

      <ol aria-label="ProblemSpec setup progress" className="grid grid-cols-3 gap-2 text-xs font-medium text-center">
        {(['Scope & question', 'Experiment manifest', 'Review & freeze'] as const).map((label, index) => {
          const itemStep = (index + 1) as 1 | 2 | 3;
          const current = step === itemStep;
          const complete = step > itemStep || snapshot !== null;
          return (
            <li
              key={label}
              aria-current={current ? 'step' : undefined}
              className={`p-2 rounded-lg border flex items-center justify-center gap-2 ${
                current
                  ? 'border-primary/40 bg-primary/5 text-primary'
                  : complete
                    ? 'border-emerald-500/30 bg-emerald-500/5 text-emerald-700 dark:text-emerald-400'
                    : 'border-border/60 bg-card text-muted-foreground'
              }`}
            >
              <span className="w-4 h-4 rounded-full bg-current/10 flex items-center justify-center text-[10px]">{itemStep}</span>
              {label}
            </li>
          );
        })}
      </ol>

      {/* Live Status Notice */}
      <output
        aria-live="polite"
        className={`block text-xs p-3 rounded-lg border transition-all ${
          isErrorNotice
            ? 'border-destructive/40 bg-destructive/10 text-destructive'
            : isSavedNotice
            ? 'border-emerald-500/40 bg-emerald-500/10 text-emerald-600 dark:text-emerald-400 font-medium'
            : 'border-border/60 bg-muted/30 text-muted-foreground'
        }`}
      >
        <span className="inline-flex items-center gap-2">
          {isErrorNotice ? <AlertCircle size={14} /> : isSavedNotice ? <CheckCircle2 size={14} /> : null}
          {notice}
        </span>
      </output>

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
              <span className="text-muted-foreground block text-[11px] uppercase tracking-wider mb-1">Spec Identifier</span>
              <code className="font-mono text-foreground font-semibold">{snapshot.spec_id}</code>
            </div>
            <div className="p-2.5 rounded-lg bg-card/80 border border-border/50">
              <span className="text-muted-foreground block text-[11px] uppercase tracking-wider mb-1">Campaign Identifier</span>
              <code className="font-mono text-foreground font-semibold">{snapshot.campaign_id}</code>
            </div>
          </div>

          <div className="p-3 rounded-lg bg-card/90 border border-border/60 space-y-1.5">
            <div className="flex items-center justify-between">
              <span className="text-muted-foreground text-[11px] uppercase tracking-wider">Content Manifest SHA-256</span>
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
        </div>
      ) : null}

      {snapshot ? <EvolutionPanel key={snapshot.spec_id} specId={snapshot.spec_id} /> : null}

      {/* Main Spec Editor Form */}
      <form onSubmit={event => { if (step === 3) void freeze(event); else { event.preventDefault(); continueSetup(); } }} className="space-y-6">
        <fieldset disabled={busy || snapshot !== null} className="space-y-6">
          {/* Card 1: Core Research Identity */}
          {step === 1 ? <div className="p-5 rounded-xl border border-border/60 bg-card shadow-xs space-y-4">
            <div className="flex items-center gap-2 border-b border-border/40 pb-2.5">
              <FileCode2 size={16} className="text-primary" />
              <h3 className="text-sm font-semibold text-foreground">1. Core Research Identity</h3>
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
                  className="mt-1 block w-full rounded-lg border border-border/70 bg-background px-3 py-2 text-sm shadow-xs focus:ring-1 focus:ring-primary focus:border-primary placeholder:text-muted-foreground/70"
                  rows={3}
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
          {step === 2 ? <div className="p-5 rounded-xl border border-border/60 bg-card shadow-xs space-y-4">
            <div className="flex items-center justify-between border-b border-border/40 pb-2.5">
              <div className="flex items-center gap-2">
                <Boxes size={16} className="text-primary" />
                <h3 className="text-sm font-semibold text-foreground">2. Manifests & Environment Specifications</h3>
              </div>
              <span className="text-xs text-muted-foreground">13 JSON fields</span>
            </div>

            <p className="text-sm text-muted-foreground">
              Use the notes below to fill each section. Template values are scaffolding, not verified inputs.
            </p>

            <div className="grid gap-4 md:grid-cols-2">
              {sections.map(([key, label, , description]) => (
                <div key={key} className="p-3.5 rounded-lg border border-border/50 bg-background/50 space-y-2">
                  <div className="flex items-center justify-between">
                    <label
                      htmlFor={`problem-spec-section-${key}`}
                      className="block text-sm font-semibold text-foreground"
                    >
                      {label} (JSON)
                    </label>
                    <button
                      type="button"
                      onClick={() => formatJsonField(key)}
                      className="text-xs text-muted-foreground hover:text-primary transition-colors underline"
                      title="Format and validate JSON"
                    >
                      Format
                    </button>
                  </div>
                  <p id={`problem-spec-section-${key}-help`} className="text-xs leading-relaxed text-muted-foreground">
                    {description}
                  </p>
                  <textarea
                    id={`problem-spec-section-${key}`}
                    name={`section_${key}`}
                    required
                    rows={5}
                    maxLength={32768}
                    spellCheck={false}
                    value={fields[key]}
                    onChange={e => setFields(current => ({ ...current, [key]: e.target.value }))}
                    aria-describedby={`problem-spec-section-${key}-help`}
                    className="block w-full rounded-md border border-border/60 bg-card p-2 font-mono text-xs leading-relaxed shadow-2xs focus:ring-1 focus:ring-primary focus:border-primary"
                  />
                </div>
              ))}
            </div>
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
          )}
        </div> : null}
      </form>

      {/* History Drawer */}
      <section aria-label="ProblemSpec history" className="rounded-xl border border-border/60 bg-card p-5 shadow-xs space-y-3">
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
                <span className="text-[10px] text-muted-foreground shrink-0 uppercase">
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
            onClick={() => setOffset(value => value + 20)}>
            Next specs
          </button>
        </div>
      </section>
    </section>
  );
}
