'use client';

import { useEffect, useRef, useState } from 'react';
import { FileText, GitBranch, RefreshCw, ShieldAlert, Square } from 'lucide-react';
import { Badge } from '@/components/ui/badge';

type EvolutionCampaignView = {
  evolutionId: string;
  specId: string;
  status: 'active' | 'search_stopped' | 'finalists_frozen' | 'stopped';
  searchStopReason: string;
  stopReason: string;
  generation: number;
  comparedCount: number;
  paretoCount: number;
  finalistCount: number;
  winnerCount: number;
};

function record(value: unknown): value is Record<string, unknown> {
  return value !== null && typeof value === 'object' && !Array.isArray(value);
}

export function parseEvolutionCampaign(value: unknown): EvolutionCampaignView {
  if (
    !record(value) ||
    typeof value.evolution_id !== 'string' ||
    !/^evo_[a-f0-9]{32}$/.test(value.evolution_id) ||
    !record(value.spec) ||
    typeof value.spec.spec_id !== 'string' ||
    !['active', 'search_stopped', 'finalists_frozen', 'stopped'].includes(
      String(value.status),
    ) ||
    typeof value.search_stop_reason !== 'string' ||
    typeof value.stop_reason !== 'string' ||
    typeof value.generation !== 'number' ||
    !Number.isInteger(value.generation) ||
    value.generation < 0 ||
    !Array.isArray(value.evaluations) ||
    !Array.isArray(value.pareto_archive) ||
    !Array.isArray(value.finalist_ids) ||
    !Array.isArray(value.winner_ids) ||
    ![value.pareto_archive, value.finalist_ids, value.winner_ids].every(items =>
      items.every(item => typeof item === 'string'),
    )
  ) {
    throw new Error('Invalid evolution campaign response.');
  }
  return {
    evolutionId: value.evolution_id,
    specId: value.spec.spec_id,
    status: value.status as EvolutionCampaignView['status'],
    searchStopReason: value.search_stop_reason,
    stopReason: value.stop_reason,
    generation: value.generation,
    comparedCount: value.evaluations.length,
    paretoCount: value.pareto_archive.length,
    finalistCount: value.finalist_ids.length,
    winnerCount: value.winner_ids.length,
  };
}

function statusLabel(campaign: EvolutionCampaignView | null) {
  if (!campaign) return 'Not started';
  return campaign.status.replaceAll('_', ' ');
}

export default function EvolutionPanel({ specId }: { specId: string }) {
  const [campaign, setCampaign] = useState<EvolutionCampaignView | null>(null);
  const [state, setState] = useState<'loading' | 'ready' | 'unavailable'>('loading');
  const [notice, setNotice] = useState('Loading bounded-search history…');
  const [busy, setBusy] = useState(false);
  const [revision, setRevision] = useState(0);
  const startRetry = useRef<{ specId: string; key: string } | null>(null);
  const stopRetry = useRef<{ evolutionId: string; key: string } | null>(null);

  useEffect(() => {
    const controller = new AbortController();
    void (async () => {
      try {
        const response = await fetch('/api/research/evolution?limit=50&offset=0', {
          signal: controller.signal,
        });
        if (!response.ok) throw new Error('Evolution history is unavailable.');
        const raw: unknown = await response.json();
        if (
          !record(raw) ||
          !Array.isArray(raw.items) ||
          typeof raw.total !== 'number' ||
          !Number.isInteger(raw.total) ||
          raw.total < 0
        ) {
          throw new Error('Invalid evolution history response.');
        }
        const selected = raw.items.map(parseEvolutionCampaign).find(item => item.specId === specId) ?? null;
        if (controller.signal.aborted) return;
        setCampaign(selected);
        setState('ready');
        setNotice(
          selected
            ? 'Campaign ledger loaded. Browser controls cannot submit metrics or verification outcomes.'
            : 'No campaign for this frozen spec. Starting one creates only a durable budget ledger.',
        );
      } catch {
        if (controller.signal.aborted) return;
        setCampaign(null);
        setState('unavailable');
        setNotice('Evolution history is unavailable. No campaign state was changed.');
      }
    })();
    return () => controller.abort();
  }, [revision, specId]);

  async function start() {
    if (busy) return;
    setBusy(true);
    setNotice('Opening a bounded campaign ledger…');
    if (startRetry.current?.specId !== specId) {
      startRetry.current = { specId, key: crypto.randomUUID() };
    }
    try {
      const response = await fetch('/api/research/evolution', {
        method: 'POST',
        headers: {
          'content-type': 'application/json',
          'x-idempotency-key': startRetry.current.key,
        },
        body: JSON.stringify({ spec_id: specId }),
      });
      const raw: unknown = await response.json().catch(() => null);
      if (!response.ok || !record(raw) || !('campaign' in raw)) {
        throw new Error(`Campaign start rejected (${response.status}).`);
      }
      const saved = parseEvolutionCampaign(raw.campaign);
      if (saved.specId !== specId) throw new Error('Campaign scope mismatch.');
      startRetry.current = null;
      setCampaign(saved);
      setState('ready');
      setNotice('Campaign ledger opened. No proposal, verification, or experiment was run.');
    } catch (error) {
      setNotice(
        `${error instanceof Error ? error.message : 'Campaign start failed.'} Retry uses the same safe request key.`,
      );
    } finally {
      setBusy(false);
    }
  }

  async function stop() {
    if (!campaign || campaign.status === 'stopped' || busy) return;
    setBusy(true);
    setNotice('Stopping future search work…');
    if (stopRetry.current?.evolutionId !== campaign.evolutionId) {
      stopRetry.current = { evolutionId: campaign.evolutionId, key: crypto.randomUUID() };
    }
    try {
      const response = await fetch(
        `/api/research/evolution/${encodeURIComponent(campaign.evolutionId)}/stop`,
        {
          method: 'POST',
          headers: {
            'content-type': 'application/json',
            'x-idempotency-key': stopRetry.current.key,
          },
          body: '{}',
        },
      );
      const raw: unknown = await response.json().catch(() => null);
      if (!response.ok || !record(raw) || !('campaign' in raw)) {
        throw new Error(`Campaign stop rejected (${response.status}).`);
      }
      const saved = parseEvolutionCampaign(raw.campaign);
      if (saved.evolutionId !== campaign.evolutionId || saved.status !== 'stopped') {
        throw new Error('Campaign stop receipt mismatch.');
      }
      stopRetry.current = null;
      setCampaign(saved);
      setNotice('Campaign stopped. Recorded candidates and failures remain available.');
    } catch (error) {
      setNotice(
        `${error instanceof Error ? error.message : 'Campaign stop failed.'} Check history before retrying.`,
      );
    } finally {
      setBusy(false);
    }
  }

  return (
    <section aria-label="Evolution campaign" className="rounded-xl border border-border/60 bg-card p-5 shadow-xs space-y-4">
      <header className="flex flex-wrap items-start justify-between gap-3 border-b border-border/40 pb-3">
        <div className="flex items-start gap-2.5">
          <span className="flex h-8 w-8 shrink-0 items-center justify-center rounded-lg border border-primary/20 bg-primary/10 text-primary">
            <GitBranch size={17} />
          </span>
          <div>
            <h3 className="text-sm font-semibold text-foreground">Evolution search (E1)</h3>
            <p className="mt-0.5 text-xs leading-relaxed text-muted-foreground">
              Frozen budgets, Pareto lineage and locked confirmation—never browser-supplied fitness.
            </p>
          </div>
        </div>
        <Badge variant="outline" className="capitalize">{statusLabel(campaign)}</Badge>
      </header>

      {campaign ? (
        <>
          <dl className="grid grid-cols-2 gap-2 text-xs sm:grid-cols-4">
            {[
              ['Generation', campaign.generation],
              ['Compared', campaign.comparedCount],
              ['Pareto', campaign.paretoCount],
              ['Winners', campaign.winnerCount],
            ].map(([label, value]) => (
              <div key={label} className="rounded-lg border border-border/50 bg-background/60 p-3">
                <dt className="text-[10px] uppercase tracking-wider text-muted-foreground">{label}</dt>
                <dd className="mt-1 font-mono text-sm font-semibold text-foreground">{value}</dd>
              </div>
            ))}
          </dl>
          <code className="block break-all rounded-lg border border-border/50 bg-muted/20 p-2 text-[11px] text-muted-foreground">
            {campaign.evolutionId}
          </code>
          <p className="text-xs text-muted-foreground">
            Search stop: {campaign.searchStopReason} · terminal stop: {campaign.stopReason} · finalists: {campaign.finalistCount}
          </p>
        </>
      ) : null}

      <div className="flex items-start gap-2 rounded-lg border border-amber-500/25 bg-amber-500/5 p-3 text-xs text-amber-800 dark:text-amber-300">
        <ShieldAlert size={15} className="mt-0.5 shrink-0" />
        <p>
          Synthetic diagnostics remain <code>empirical=not_run</code>. A candidate cannot enter Pareto selection without a current server-owned <code>can_enter_parent_pool</code> decision.
        </p>
      </div>

      <output aria-live="polite" className="block text-xs text-muted-foreground">{notice}</output>

      <div className="flex flex-wrap items-center gap-2">
        {!campaign ? (
          <button
            type="button"
            disabled={busy || state !== 'ready'}
            onClick={() => void start()}
            className="inline-flex min-h-9 items-center gap-1.5 rounded-lg bg-primary px-3 py-2 text-xs font-semibold text-primary-foreground disabled:cursor-not-allowed disabled:opacity-50"
          >
            <GitBranch size={13} />
            Open bounded campaign
          </button>
        ) : campaign.status !== 'stopped' ? (
          <button
            type="button"
            disabled={busy}
            onClick={() => void stop()}
            className="inline-flex min-h-9 items-center gap-1.5 rounded-lg border border-border/60 px-3 py-2 text-xs font-semibold text-foreground disabled:opacity-50"
          >
            <Square size={12} />
            Stop campaign
          </button>
        ) : null}
        {campaign ? (
          <a
            href={`/api/research/evolution/${encodeURIComponent(campaign.evolutionId)}/report`}
            target="_blank"
            rel="noreferrer"
            className="inline-flex min-h-9 items-center gap-1.5 rounded-lg border border-border/60 px-3 py-2 text-xs font-medium text-foreground"
          >
            <FileText size={12} />
            Open campaign report
          </a>
        ) : null}
        <button
          type="button"
          disabled={busy || state === 'loading'}
          onClick={() => {
            setState('loading');
            setNotice('Loading bounded-search history…');
            setRevision(value => value + 1);
          }}
          className="inline-flex min-h-9 items-center gap-1.5 rounded-lg border border-border/60 px-3 py-2 text-xs font-medium text-foreground disabled:opacity-50"
        >
          <RefreshCw size={12} />
          Refresh
        </button>
      </div>
    </section>
  );
}
