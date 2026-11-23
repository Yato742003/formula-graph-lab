'use client';

import { useSyncExternalStore, type ReactNode } from 'react';
import { Activity, BookOpen, Network, ShieldCheck, FileCode2, Sparkles, FileText } from 'lucide-react';

const stages = [
  { href: '/graph', step: '01', label: 'Graph', hint: 'Import arXiv HTML papers and inspect extracted formulas', icon: Network },
  { href: '/spec', step: '02', label: 'Spec', hint: 'Define and freeze the research question and experiment rules', icon: FileCode2 },
  { href: '/compatibility', step: '03', label: 'Compatibility', hint: 'Review whether two formula symbols can safely connect', icon: ShieldCheck },
  { href: '/proposals', step: '04', label: 'Proposals', hint: 'Compile a bounded transformation or review an AI draft', icon: Sparkles },
  { href: '/reports', step: '05', label: 'Reports', hint: 'Inspect checks, replay evidence bundles, and run evolution', icon: FileText },
] as const;

const WORKSPACE_LOCATION_EVENT = 'fgl-workspace-location-change';

export function syncWorkspaceQueryParam(
  key: 'paper_id' | 'spec_id' | 'candidate_id',
  value: string | null,
) {
  if (typeof window === 'undefined') return;
  const url = new URL(window.location.href);
  if (value && value.trim()) url.searchParams.set(key, value.trim());
  else url.searchParams.delete(key);
  const next = `${url.pathname}${url.search}${url.hash}`;
  if (next !== `${window.location.pathname}${window.location.search}${window.location.hash}`) {
    window.history.replaceState(window.history.state, '', next);
    window.dispatchEvent(new Event(WORKSPACE_LOCATION_EVENT));
  }
}

export function withContext(href: string, params: URLSearchParams) {
  const query = new URLSearchParams();
  for (const key of ['paper_id', 'spec_id', 'candidate_id']) {
    const value = params.get(key);
    if (value) query.set(key, value);
  }
  const suffix = query.toString();
  const [path, anchor] = href.split('#');
  return `${path}${suffix ? `?${suffix}` : ''}${anchor ? `#${anchor}` : ''}`;
}

export function useWorkspaceLocation() {
  const locationKey = useSyncExternalStore(
    (onStoreChange) => {
      window.addEventListener('popstate', onStoreChange);
      window.addEventListener(WORKSPACE_LOCATION_EVENT, onStoreChange);
      return () => {
        window.removeEventListener('popstate', onStoreChange);
        window.removeEventListener(WORKSPACE_LOCATION_EVENT, onStoreChange);
      };
    },
    () => `${window.location.pathname}${window.location.search}`,
    () => '',
  );
  const [pathname = '', search = ''] = locationKey.split('?');
  const params = new URLSearchParams(search);
  return { pathname, params };
}

export default function StageNav() {
  const { pathname, params } = useWorkspaceLocation();

  return (
    <nav className="stage-nav" aria-label="Research stages">
      {stages.map(({ href, step, label, hint, icon: Icon }) => {
        const active = pathname === href || (href === '/graph' && pathname === '/');
        return (
          <a
            key={href}
            href={withContext(href, params)}
            aria-current={active ? 'page' : undefined}
            title={hint}
            className={`stage-nav-link${active ? ' is-active' : ''}`}
          >
            <span className="stage-nav-step" aria-hidden="true">{step}</span>
            <Icon size={14} aria-hidden="true" />
            <span>{label}</span>
          </a>
        );
      })}
    </nav>
  );
}

export function WorkspaceShell({
  user,
  children,
  className = '',
}: {
  user: { displayName: string };
  children: ReactNode;
  className?: string;
}) {
  const { pathname, params } = useWorkspaceLocation();
  const helpHref = withContext('/help', params);
  const opsHref = withContext('/ops', params);

  return (
    <main className={`standalone-stage-page ${className}`.trim()}>
      <header className="topbar standalone-topbar">
        <div className="brand-lockup">
          <div className="brand-mark" aria-hidden="true"><Network size={19} strokeWidth={2.2} /></div>
          <div>
            <div className="flex items-center gap-1.5">
              <p className="brand-name">FormulaGraph</p>
              <span className="pro-tier-badge">PRO BETA</span>
            </div>
            <p className="brand-suffix">LAB / 01</p>
          </div>
        </div>
        <div className="standalone-topbar-actions">
          <a className="workspace-help-link" href={opsHref} aria-label="Open Operations dashboard" aria-current={pathname === '/ops' ? 'page' : undefined}>
            <Activity size={14} aria-hidden="true" />
            Ops
          </a>
          <a className="workspace-help-link" href={helpHref} aria-label="Open FormulaGraph help" aria-current={pathname === '/help' ? 'page' : undefined}>
            <BookOpen size={14} aria-hidden="true" />
            Help
          </a>
          <div className="workspace-pill"><span className="workspace-dot" />{user.displayName}</div>
        </div>
        <StageNav />
      </header>
      {children}
    </main>
  );
}
