'use client';

import { useEffect, useState } from 'react';
import Link from 'next/link';
import { ArrowRight, WandSparkles } from 'lucide-react';
import {
  WorkspaceShell,
  syncWorkspaceQueryParam,
  useSampleWalkthrough,
  useWorkspaceLocation,
  withContext,
} from '@/components/workspace-shell';
import { TrustBadge } from '@/components/research-primitives';
import ResearchMovePanel from './research-move-panel';
import EvolutionPanel from './evolution-panel';
import { parseCompatibilityView, type CompatibilityView } from '@/lib/research-view';

type Surface = 'proposals' | 'reports';
type FrozenSpecOption = { specId: string; version: number; task: string };

function parseSpecOptions(raw: unknown): FrozenSpecOption[] {
  if (!raw || typeof raw !== 'object' || !('items' in raw) || !Array.isArray(raw.items)) {
    return [];
  }
  return raw.items.flatMap((item): FrozenSpecOption[] => {
    if (
      !item ||
      typeof item !== 'object' ||
      typeof (item as Record<string, unknown>).spec_id !== 'string' ||
      !(item as { spec_id: string }).spec_id.startsWith('spec_') ||
      typeof (item as Record<string, unknown>).version !== 'number' ||
      !(item as Record<string, unknown>).definition ||
      typeof (item as { definition: Record<string, unknown> }).definition.task !== 'string'
    ) {
      return [];
    }
    const typed = item as { spec_id: string; version: number; definition: { task: string } };
    return [{ specId: typed.spec_id, version: typed.version, task: typed.definition.task }];
  });
}

function SampleWalkthroughBanner({
  surface,
  onClose,
}: {
  surface: Surface;
  onClose: () => void;
}) {
  if (surface === 'proposals') {
    return (
      <section
        aria-label="Illustrative proposal walkthrough"
        className="wb-walkthrough-card mb-4"
      >
        <div className="wb-walkthrough-header">
          <div className="flex items-center gap-2">
            <span className="wb-status-dot is-derivation" aria-hidden="true" />
            <strong className="text-xs font-semibold text-foreground">
              Illustrative sample walkthrough · Step 04 output preview
            </strong>
          </div>
          <div className="flex items-center gap-2">
            <span className="wb-inline-note">Read-only preview · not stored evidence</span>
            <button type="button" onClick={onClose} className="wb-text-link">
              Dismiss
            </button>
          </div>
        </div>
        <div className="grid gap-3 md:grid-cols-3 text-xs mt-2.5">
          <div className="wb-mini-tile">
            <span className="wb-mini-label">Compiled expression (DSL)</span>
            <code className="block mt-1 text-[11px] text-foreground">
              κ_mix(q,k) = 0.50·⟨φ_exp(q),φ_exp(k)⟩ + 0.50·⟨φ_elu(q),φ_elu(k)⟩
            </code>
          </div>
          <div className="wb-mini-tile">
            <span className="wb-mini-label">Immutable parents</span>
            <p className="mt-1 font-mono text-[11px] text-foreground m-0">
              S3.E1 (φ_exp, rank 16) + S3.E4 (φ_elu, rank 16) → rank 32
            </p>
          </div>
          <div className="wb-mini-tile">
            <span className="wb-mini-label">Recorded proof obligation</span>
            <p className="mt-1 text-[11px] text-muted-foreground m-0">
              <code>nonzero_normalization_denominator</code> · remains conditional until checked
            </p>
          </div>
        </div>
      </section>
    );
  }

  const rows = [
    { name: 'cand_mix_w050 (Sample candidate)', regret: 0.0042, ciLow: 0.0031, ciHigh: 0.0054, pct: 42, primary: true },
    { name: 'positive-random-exp (Parent A)', regret: 0.0079, ciLow: 0.0064, ciHigh: 0.0093, pct: 79, primary: false },
    { name: 'elu-plus-one (Parent B)', regret: 0.0094, ciLow: 0.0081, ciHigh: 0.0108, pct: 94, primary: false },
  ];

  return (
    <section
      aria-label="Illustrative report walkthrough"
      className="wb-walkthrough-card mb-4"
    >
      <div className="wb-walkthrough-header">
        <div className="flex items-center gap-2">
          <span className="wb-status-dot is-evidence" aria-hidden="true" />
          <strong className="text-xs font-semibold text-foreground">
            Illustrative sample walkthrough · Locked-holdout comparison preview
          </strong>
        </div>
        <div className="flex items-center gap-2">
          <span className="wb-inline-note">synthetic_operator_only_no_product_claim · preview only</span>
          <button type="button" onClick={onClose} className="wb-text-link">
            Dismiss
          </button>
        </div>
      </div>
      <div className="mt-3 overflow-x-auto">
        <table className="wb-dense-table">
          <thead>
            <tr>
              <th>Variant</th>
              <th>Scope</th>
              <th>Regret vs exact softmax (lower is better)</th>
              <th className="text-right">Holdout mean · 95% CI</th>
            </tr>
          </thead>
          <tbody>
            {rows.map((row) => (
              <tr key={row.name} className={row.primary ? 'is-highlighted' : undefined}>
                <td className="font-mono text-[11px]">{row.name}</td>
                <td aria-label={row.primary ? 'Sample mixture' : 'Baseline'}>
                  <span className="inline-flex items-center gap-1.5">
                    <span className={`wb-status-dot ${row.primary ? 'is-derivation' : 'is-evidence'}`} aria-hidden="true" />
                    <span>{row.primary ? 'Sample mixture' : 'Baseline'}</span>
                  </span>
                </td>
                <td aria-label={`Relative regret bar ${row.regret.toFixed(4)}`}>
                  <svg
                    width="160"
                    height="14"
                    viewBox="0 0 160 14"
                    aria-hidden="true"
                    className="block"
                  >
                    <rect x="0" y="4" width="160" height="6" rx="3" fill="oklch(0.93 0.015 250)" />
                    <rect
                      x="0"
                      y="4"
                      width={Math.round(row.pct * 1.5)}
                      height="6"
                      rx="3"
                      fill={row.primary ? 'oklch(0.46 0.18 256)' : 'oklch(0.46 0.18 256 / 0.45)'}
                    />
                  </svg>
                </td>
                <td className="text-right font-mono tabular-nums text-[11px]">
                  {row.regret.toFixed(4)} [{row.ciLow.toFixed(4)}, {row.ciHigh.toFixed(4)}]
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </section>
  );
}

export default function ResearchStagePage({
  user,
  surface,
}: {
  user: { displayName: string; email: string };
  surface: Surface;
}) {
  const [mappings, setMappings] = useState<CompatibilityView[]>([]);
  const [state, setState] = useState<'loading' | 'loaded' | 'unavailable'>('loading');
  const [savedSpecs, setSavedSpecs] = useState<FrozenSpecOption[]>([]);
  const { params } = useWorkspaceLocation();
  const { walkthroughEnabled, setWalkthrough } = useSampleWalkthrough();
  const specId = params.get('spec_id') ?? '';
  const paperId = params.get('paper_id') ?? '';
  const candidateId = params.get('candidate_id') ?? '';
  const isProposals = surface === 'proposals';

  useEffect(() => {
    const controller = new AbortController();
    void fetch('/api/research/compatibility?limit=100', { signal: controller.signal })
      .then(async (response) => {
        if (!response.ok) throw new Error('Compatibility unavailable');
        return parseCompatibilityView(await response.json());
      })
      .then((items) => {
        if (controller.signal.aborted) return;
        setMappings(items);
        setState('loaded');
      })
      .catch(() => {
        if (!controller.signal.aborted) setState('unavailable');
      });
    return () => controller.abort();
  }, []);

  useEffect(() => {
    if (isProposals) return;
    const controller = new AbortController();
    void fetch('/api/research/problems?limit=50&offset=0', { signal: controller.signal })
      .then(async (response) => (response.ok ? parseSpecOptions(await response.json()) : []))
      .then((items) => {
        if (!controller.signal.aborted) setSavedSpecs(items);
      })
      .catch(() => {
        if (!controller.signal.aborted) setSavedSpecs([]);
      });
    return () => controller.abort();
  }, [isProposals]);

  const stageInspector = (
    <div className="space-y-4 text-xs">
      <section className="wb-inspector-card">
        <h3 className="wb-card-title">Stage summary</h3>
        <p className="text-muted-foreground leading-relaxed m-0">
          {isProposals
            ? 'Combines reviewed compatible ports under an allowlisted DSL operator without overwriting extracted paper equations.'
            : 'Reconstructs checker verdicts, comparison tables, and offline deterministic replay bundles from immutable hashes.'}
        </p>
        {!walkthroughEnabled ? (
          <button
            type="button"
            onClick={() => setWalkthrough(true)}
            className="wb-secondary-btn mt-2.5 w-full justify-center"
          >
            <WandSparkles size={13} aria-hidden="true" />
            <span>Preview sample output</span>
          </button>
        ) : null}
      </section>

      <section className="wb-inspector-card">
        <h3 className="wb-card-title">Scoped context</h3>
        <dl className="wb-kv-list">
          <div>
            <dt>Paper</dt>
            <dd className="font-mono">{paperId || 'Not pinned'}</dd>
          </div>
          <div>
            <dt>ProblemSpec</dt>
            <dd className="font-mono">{specId ? `${specId.slice(0, 16)}…` : 'Select in Step 02'}</dd>
          </div>
          <div>
            <dt>Candidate</dt>
            <dd className="font-mono">{candidateId ? `${candidateId.slice(0, 16)}…` : 'None selected'}</dd>
          </div>
          <div>
            <dt>Reviewed mappings</dt>
            <dd className="font-mono tabular-nums">
              {state === 'loaded'
                ? mappings.filter((m) => m.status === 'compatible' && m.isReviewed && m.freshness === 'current').length
                : '—'}
            </dd>
          </div>
        </dl>
      </section>

      <section className="wb-inspector-card">
        <h3 className="wb-card-title">Verification &amp; isolation</h3>
        <ul className="wb-guarantee-list">
          <li>
            <span className="wb-status-dot is-ready" aria-hidden="true" />
            <span>Exact paper formulas stay immutable (SHA-256)</span>
          </li>
          <li>
            <span className="wb-status-dot is-ready" aria-hidden="true" />
            <span>Hermetic worker (<code>sandbox-policy.v2</code>, no network)</span>
          </li>
          <li>
            <span className="wb-status-dot is-ready" aria-hidden="true" />
            <span>Holdout split locked until finalist freeze</span>
          </li>
          <li>
            <span className="wb-status-dot is-ready" aria-hidden="true" />
            <span>Tenant-scoped D1 &amp; Neo4j authorization</span>
          </li>
        </ul>
      </section>

      <section className="wb-inspector-card">
        <h3 className="wb-card-title">Shortcuts</h3>
        <div className="wb-shortcut-list">
          <span>Jump stages <kbd>Alt 1–5</kbd></span>
          <span>Toggle sidebar <kbd>[</kbd></span>
          <span>Toggle inspector <kbd>]</kbd></span>
        </div>
      </section>
    </div>
  );

  return (
    <WorkspaceShell
      user={user}
      showWalkthroughToggle
      inspectorTitle={isProposals ? 'Proposal inspector' : 'Report & replay inspector'}
      inspector={stageInspector}
      headerActions={
        isProposals ? (
          <Link
            href={withContext('/reports', params)}
            className="wb-primary-cta"
          >
            <span>Next: Step 5 · Reports</span>
            <ArrowRight size={13} aria-hidden="true" />
          </Link>
        ) : null
      }
    >
      <section className="standalone-stage-content">
        <header className="standalone-stage-heading">
          <div>
            <p className="eyebrow">
              {isProposals ? 'Step 04 of 05 · Hypothesis & compilation' : 'Step 05 of 05 · Verification & replay'}
            </p>
            <h1>{isProposals ? 'Proposals' : 'Reports'}</h1>
            <p>
              {isProposals
                ? 'Build a candidate from a frozen spec and reviewed mapping, or review an optional AI draft.'
                : 'Inspect saved checks, compare candidates side by side, and export offline replay bundles.'}
            </p>
          </div>
          <TrustBadge>{isProposals ? 'AI proposal is optional' : 'Evidence is scoped'}</TrustBadge>
        </header>

        {walkthroughEnabled ? (
          <SampleWalkthroughBanner surface={surface} onClose={() => setWalkthrough(false)} />
        ) : null}

        <div className="standalone-stage-panel">
          <ResearchMovePanel
            mappings={mappings}
            mappingsState={state}
            mappingsPartial={false}
            surface={surface}
          />
        </div>
        {!isProposals ? (
          <div className="standalone-stage-panel reports-evolution-panel">
            {specId ? (
              <div className="space-y-3">
                {savedSpecs.length > 1 ? (
                  <div className="flex flex-wrap items-center justify-between gap-2 text-xs">
                    <label htmlFor="reports-evolution-spec-switcher" className="font-medium text-muted-foreground">
                      Active evolution ProblemSpec
                    </label>
                    <select
                      id="reports-evolution-spec-switcher"
                      name="evolution_spec_id"
                      value={specId}
                      onChange={(event) => syncWorkspaceQueryParam('spec_id', event.target.value || null)}
                      className="max-w-md rounded-md border border-border/60 bg-background px-2.5 py-1 text-xs text-foreground"
                    >
                      {!savedSpecs.some((item) => item.specId === specId) ? (
                        <option value={specId}>{specId}</option>
                      ) : null}
                      {savedSpecs.map((item) => (
                        <option key={item.specId} value={item.specId}>
                          v{item.version} · {item.task}
                        </option>
                      ))}
                    </select>
                  </div>
                ) : null}
                <EvolutionPanel specId={specId} />
              </div>
            ) : (
              <div className="reports-evolution-empty">
                <div className="space-y-1.5">
                  <strong className="block text-sm font-semibold text-foreground">
                    Bounded evolution campaign
                  </strong>
                  <p>Select a frozen research question in Spec to view its evolution campaign.</p>
                  {savedSpecs.length > 0 ? (
                    <label htmlFor="reports-evolution-spec-picker" className="inline-flex flex-wrap items-center gap-2 text-xs text-foreground">
                      <span className="font-medium">Or pick a saved spec now:</span>
                      <select
                        id="reports-evolution-spec-picker"
                        name="evolution_spec_picker"
                        value=""
                        onChange={(event) => {
                          if (event.target.value) syncWorkspaceQueryParam('spec_id', event.target.value);
                        }}
                        className="max-w-md rounded-md border border-border/60 bg-background px-2.5 py-1 text-xs text-foreground"
                      >
                        <option value="">Choose a frozen ProblemSpec…</option>
                        {savedSpecs.map((item) => (
                          <option key={item.specId} value={item.specId}>
                            v{item.version} · {item.task}
                          </option>
                        ))}
                      </select>
                    </label>
                  ) : null}
                </div>
                <Link href="/spec">Open Spec</Link>
              </div>
            )}
          </div>
        ) : null}
      </section>
    </WorkspaceShell>
  );
}
