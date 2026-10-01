'use client';

import { useSyncExternalStore, type ReactNode } from 'react';
import { Activity, BookOpen, Network, ShieldCheck, FileCode2, Sparkles, FileText } from 'lucide-react';

const stages = [
  { href: '/graph', label: 'Graph', icon: Network },
  { href: '/spec', label: 'Spec', icon: FileCode2 },
  { href: '/compatibility', label: 'Compatibility', icon: ShieldCheck },
  { href: '/proposals', label: 'Proposals', icon: Sparkles },
  { href: '/reports', label: 'Reports', icon: FileText },
] as const;

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
      return () => window.removeEventListener('popstate', onStoreChange);
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
      {stages.map(({ href, label, icon: Icon }) => {
        const active = pathname === href || (href === '/graph' && pathname === '/');
        return (
          <a
            key={href}
            href={withContext(href, params)}
            aria-current={active ? 'page' : undefined}
            className={`stage-nav-link${active ? ' is-active' : ''}`}
          >
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
          <div><p className="brand-name">FormulaGraph</p><p className="brand-suffix">LAB / 01</p></div>
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
