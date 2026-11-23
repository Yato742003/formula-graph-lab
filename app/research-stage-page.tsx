'use client';

import { useEffect, useState } from 'react';
import Link from 'next/link';
import {
  WorkspaceShell,
  syncWorkspaceQueryParam,
  useWorkspaceLocation,
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
  const specId = params.get('spec_id') ?? '';
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

  return (
    <WorkspaceShell user={user}>
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
