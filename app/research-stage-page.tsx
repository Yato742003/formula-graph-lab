'use client';

import { useEffect, useState } from 'react';
import Link from 'next/link';
import { WorkspaceShell } from '@/components/workspace-shell';
import { TrustBadge } from '@/components/research-primitives';
import ResearchMovePanel from './research-move-panel';
import EvolutionPanel from './evolution-panel';
import { parseCompatibilityView, type CompatibilityView } from '@/lib/research-view';

type Surface = 'proposals' | 'reports';

export default function ResearchStagePage({
  user,
  surface,
}: {
  user: { displayName: string; email: string };
  surface: Surface;
}) {
  const [mappings, setMappings] = useState<CompatibilityView[]>([]);
  const [state, setState] = useState<'loading' | 'loaded' | 'unavailable'>('loading');
  const [specId] = useState(() => {
    if (typeof window === 'undefined') return '';
    return new URLSearchParams(window.location.search).get('spec_id') ?? '';
  });

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

  const isProposals = surface === 'proposals';

  return (
    <WorkspaceShell user={user}>
      <section className="standalone-stage-content">
        <header className="standalone-stage-heading">
          <div>
            <h1>{isProposals ? 'Proposals' : 'Reports'}</h1>
            <p>{isProposals ? 'Build a candidate or review an AI draft.' : 'Inspect saved checks, compare candidates and replay evidence.'}</p>
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
              <EvolutionPanel specId={specId} />
            ) : (
              <div className="reports-evolution-empty">
                <p>Select a frozen research question in Spec to view its evolution campaign.</p>
                <Link href="/spec">Open Spec</Link>
              </div>
            )}
          </div>
        ) : null}
      </section>
    </WorkspaceShell>
  );
}
