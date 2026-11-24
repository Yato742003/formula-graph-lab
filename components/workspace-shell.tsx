'use client';

import { useEffect, useState, useSyncExternalStore, type ReactNode } from 'react';
import Link from 'next/link';
import {
  Activity,
  BookOpen,
  Building2,
  ChevronRight,
  FileCode2,
  FileText,
  Network,
  PanelLeftClose,
  PanelLeftOpen,
  PanelRightClose,
  PanelRightOpen,
  Search,
  ShieldCheck,
  Sparkles,
  WandSparkles,
} from 'lucide-react';

export const RESEARCH_STAGES = [
  {
    href: '/graph',
    step: '01',
    label: 'Graph',
    verb: 'Extract formulas',
    hint: 'Import arXiv HTML papers and inspect extracted formulas (Alt+1)',
    icon: Network,
  },
  {
    href: '/spec',
    step: '02',
    label: 'Spec',
    verb: 'Freeze rules',
    hint: 'Define and freeze the research question and experiment rules (Alt+2)',
    icon: FileCode2,
  },
  {
    href: '/compatibility',
    step: '03',
    label: 'Compatibility',
    verb: 'Check ports',
    hint: 'Review whether two formula symbols can safely connect (Alt+3)',
    icon: ShieldCheck,
  },
  {
    href: '/proposals',
    step: '04',
    label: 'Proposals',
    verb: 'Compile move',
    hint: 'Compile a bounded transformation or review an AI draft (Alt+4)',
    icon: Sparkles,
  },
  {
    href: '/reports',
    step: '05',
    label: 'Reports',
    verb: 'Verify & replay',
    hint: 'Inspect checks, replay evidence bundles, and run evolution (Alt+5)',
    icon: FileText,
  },
] as const;

const WORKSPACE_LOCATION_EVENT = 'fgl-workspace-location-change';
const WALKTHROUGH_EVENT = 'fgl-walkthrough-change';
const WALKTHROUGH_STORAGE_KEY = 'fgl-sample-walkthrough-v1';

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

export function useSampleWalkthrough() {
  const enabled = useSyncExternalStore(
    (onStoreChange) => {
      window.addEventListener('storage', onStoreChange);
      window.addEventListener(WALKTHROUGH_EVENT, onStoreChange);
      return () => {
        window.removeEventListener('storage', onStoreChange);
        window.removeEventListener(WALKTHROUGH_EVENT, onStoreChange);
      };
    },
    () => {
      try {
        return window.localStorage?.getItem(WALKTHROUGH_STORAGE_KEY) === '1';
      } catch {
        return false;
      }
    },
    () => false,
  );

  function setWalkthrough(next: boolean) {
    try {
      if (next) window.localStorage?.setItem(WALKTHROUGH_STORAGE_KEY, '1');
      else window.localStorage?.removeItem(WALKTHROUGH_STORAGE_KEY);
      window.dispatchEvent(new Event(WALKTHROUGH_EVENT));
    } catch {
      // Ignore unavailable storage in restricted contexts.
    }
  }

  return { walkthroughEnabled: enabled, setWalkthrough };
}

export function useWorkbenchStageShortcuts(params: URLSearchParams) {
  useEffect(() => {
    function onKeyDown(event: KeyboardEvent) {
      if (!event.altKey || event.ctrlKey || event.metaKey || event.shiftKey) return;
      const index = Number(event.key) - 1;
      if (index >= 0 && index < RESEARCH_STAGES.length) {
        event.preventDefault();
        window.location.assign(withContext(RESEARCH_STAGES[index].href, params));
      }
    }
    window.addEventListener('keydown', onKeyDown);
    return () => window.removeEventListener('keydown', onKeyDown);
  }, [params]);
}

export default function StageNav({
  orientation = 'vertical',
  completedSteps,
}: {
  orientation?: 'vertical' | 'horizontal';
  completedSteps?: Partial<Record<'/graph' | '/spec' | '/compatibility' | '/proposals' | '/reports', boolean>>;
} = {}) {
  const { pathname, params } = useWorkspaceLocation();
  const hasPaper = Boolean(params.get('paper_id'));
  const hasSpec = Boolean(params.get('spec_id'));
  const hasCandidate = Boolean(params.get('candidate_id'));

  return (
    <nav
      className={`stage-nav ${orientation === 'vertical' ? 'stage-nav-vertical' : 'stage-nav-horizontal'}`}
      aria-label="Research stages"
    >
      {RESEARCH_STAGES.map(({ href, step, label, verb, hint, icon: Icon }) => {
        const active = pathname === href || (href === '/graph' && pathname === '/');
        const ready =
          completedSteps?.[href] ??
          ((href === '/graph' && hasPaper) ||
            (href === '/spec' && hasSpec) ||
            ((href === '/proposals' || href === '/reports') && hasCandidate));
        return (
          <Link
            key={href}
            href={withContext(href, params)}
            aria-current={active ? 'page' : undefined}
            title={hint}
            className={`stage-nav-link${active ? ' is-active' : ''}${ready ? ' is-ready' : ''}`}
          >
            <span className="stage-nav-dot" aria-hidden="true" />
            <span className="stage-nav-step" aria-hidden="true">{step}</span>
            <Icon size={14} aria-hidden="true" />
            <span className="stage-nav-label">{label}</span>
            {orientation === 'vertical' ? (
              <span className="stage-nav-verb" aria-hidden="true">{verb}</span>
            ) : null}
          </Link>
        );
      })}
    </nav>
  );
}

export function WorkbenchLeftSpine({
  user,
  collapsed = false,
  onCollapse,
  activePaperLabel,
  activeSpecLabel,
  onOpenSearch,
  isSearchOpen,
  showWalkthroughToggle = false,
  completedSteps,
  children,
}: {
  user: { displayName: string; email?: string };
  collapsed?: boolean;
  onCollapse?: () => void;
  activePaperLabel?: string;
  activeSpecLabel?: string;
  onOpenSearch?: () => void;
  isSearchOpen?: boolean;
  showWalkthroughToggle?: boolean;
  completedSteps?: Partial<Record<'/graph' | '/spec' | '/compatibility' | '/proposals' | '/reports', boolean>>;
  children?: ReactNode;
}) {
  const { pathname, params } = useWorkspaceLocation();
  const { walkthroughEnabled, setWalkthrough } = useSampleWalkthrough();
  const helpHref = withContext('/help', params);
  const opsHref = withContext('/ops', params);
  const paperParam = params.get('paper_id');
  const paperContext = activePaperLabel ?? (paperParam ? `arXiv:${paperParam}` : 'None pinned');
  const specParam = params.get('spec_id');
  const specContext = activeSpecLabel ?? (specParam ? `${specParam.slice(0, 14)}…` : 'Unfrozen draft');

  return (
    <aside className={`wb-sidebar${collapsed ? ' is-collapsed' : ''}`} aria-label="Workspace navigation">
      <div className="wb-sidebar-header">
        <div className="brand-lockup">
          <div className="brand-mark" aria-hidden="true">
            <Network size={16} strokeWidth={2.2} />
          </div>
          <div className="min-w-0">
            <div className="flex items-center gap-1.5">
              <p className="brand-name">FormulaGraph</p>
              <span className="pro-tier-badge">SAAS</span>
            </div>
            <p className="brand-suffix">
              <Building2 size={10} aria-hidden="true" className="inline mr-1 -mt-0.5" />
              Tenant workspace
            </p>
          </div>
        </div>
        {onCollapse ? (
          <button
            type="button"
            className="wb-icon-btn"
            onClick={onCollapse}
            title="Collapse sidebar ([)"
            aria-label="Collapse paper sources"
          >
            <PanelLeftClose size={14} aria-hidden="true" />
          </button>
        ) : null}
      </div>

      {/* Pinned Workspace Context */}
      <div className="wb-context-card" aria-label="Pinned workspace context">
        <div className="wb-context-row">
          <span className={`wb-status-dot ${paperParam || activePaperLabel ? 'is-evidence' : 'is-pending'}`} aria-hidden="true" />
          <span className="wb-context-key">Paper</span>
          <code className="wb-context-val" title={paperContext}>{paperContext}</code>
        </div>
        <div className="wb-context-row">
          <span className={`wb-status-dot ${specParam || activeSpecLabel ? 'is-ready' : 'is-pending'}`} aria-hidden="true" />
          <span className="wb-context-key">Spec</span>
          <code className="wb-context-val" title={specParam ?? specContext}>{specContext}</code>
        </div>
      </div>

      {/* Vertical 5-Step Pipeline */}
      <div className="wb-sidebar-section">
        <p className="wb-section-heading">Research pipeline</p>
        <StageNav orientation="vertical" completedSteps={completedSteps} />
      </div>

      {/* Interactive Sample Walkthrough Toggle (Perceived UX Cold-Start Cure) */}
      {showWalkthroughToggle ? (
        <div className="wb-walkthrough-box">
          <button
            type="button"
            aria-pressed={walkthroughEnabled}
            onClick={() => setWalkthrough(!walkthroughEnabled)}
            className={`wb-walkthrough-btn${walkthroughEnabled ? ' is-active' : ''}`}
          >
            <WandSparkles size={13} aria-hidden="true" />
            <span>{walkthroughEnabled ? 'Hide sample walkthrough' : 'Sample walkthrough'}</span>
          </button>
        </div>
      ) : null}

      {/* Stage-specific Drawer Content (e.g. Paper sources on Graph/Compatibility) */}
      {children ? <div className="wb-sidebar-scroll">{children}</div> : <div className="wb-sidebar-spacer" />}

      {/* Bottom SaaS Utility Footer */}
      <div className="wb-sidebar-footer">
        {onOpenSearch ? (
          <button
            type="button"
            className={`wb-utility-btn ${isSearchOpen ? 'is-active' : ''}`}
            aria-label={isSearchOpen ? 'Close graph search' : 'Search graph'}
            title="Search graph (Ctrl+K or /)"
            aria-expanded={isSearchOpen}
            aria-controls="graph-search-panel"
            onClick={onOpenSearch}
          >
            <Search size={13} aria-hidden="true" />
            <span>Search</span>
            <kbd aria-hidden="true">Ctrl K</kbd>
          </button>
        ) : null}
        <div className="wb-utility-links">
          <Link
            className="workspace-help-link"
            href={opsHref}
            aria-label="Open Operations dashboard"
            aria-current={pathname === '/ops' ? 'page' : undefined}
          >
            <Activity size={13} aria-hidden="true" />
            <span>Ops</span>
          </Link>
          <Link
            className="workspace-help-link"
            href={helpHref}
            aria-label="Open FormulaGraph help"
            aria-current={pathname === '/help' ? 'page' : undefined}
          >
            <BookOpen size={13} aria-hidden="true" />
            <span>Help</span>
          </Link>
        </div>
        <div className="wb-tenant-pill" title={user.email ?? user.displayName}>
          <span className="workspace-dot" aria-hidden="true" />
          <span className="truncate font-medium">{user.displayName}</span>
          <span className="wb-tenant-role">Isolated</span>
        </div>
      </div>
    </aside>
  );
}

export function WorkspaceShell({
  user,
  children,
  inspector,
  inspectorTitle = 'Context & guarantees',
  stageLabel,
  headerActions,
  showWalkthroughToggle = false,
  className = '',
}: {
  user: { displayName: string; email?: string };
  children: ReactNode;
  inspector?: ReactNode;
  inspectorTitle?: string;
  stageLabel?: string;
  headerActions?: ReactNode;
  showWalkthroughToggle?: boolean;
  className?: string;
}) {
  const { pathname, params } = useWorkspaceLocation();
  const [leftCollapsed, setLeftCollapsed] = useState(false);
  const [rightCollapsed, setRightCollapsed] = useState(false);
  useWorkbenchStageShortcuts(params);

  useEffect(() => {
    function onKeyDown(event: KeyboardEvent) {
      const target = event.target as HTMLElement | null;
      if (
        target &&
        (target.tagName === 'INPUT' ||
          target.tagName === 'TEXTAREA' ||
          target.tagName === 'SELECT' ||
          target.isContentEditable)
      ) {
        return;
      }
      if (event.ctrlKey || event.metaKey || event.altKey) return;
      if (event.key === '[') {
        event.preventDefault();
        setLeftCollapsed((v) => !v);
      } else if (event.key === ']' && inspector) {
        event.preventDefault();
        setRightCollapsed((v) => !v);
      }
    }
    window.addEventListener('keydown', onKeyDown);
    return () => window.removeEventListener('keydown', onKeyDown);
  }, [inspector]);

  const activeStage = RESEARCH_STAGES.find((s) => s.href === pathname);
  const breadcrumbLabel =
    stageLabel ??
    (activeStage
      ? `${activeStage.step} ${activeStage.label} · ${activeStage.verb}`
      : pathname === '/ops'
        ? 'Operations & quotas'
        : pathname === '/help'
          ? 'Documentation & recipes'
          : 'Workspace');

  return (
    <main
      className={`wb-shell standalone-stage-page${leftCollapsed ? ' is-left-collapsed' : ''}${
        !inspector || rightCollapsed ? ' is-right-collapsed' : ''
      } ${className}`.trim()}
    >
      <WorkbenchLeftSpine
        user={user}
        collapsed={leftCollapsed}
        onCollapse={() => setLeftCollapsed(true)}
        showWalkthroughToggle={showWalkthroughToggle}
      />

      <div className="wb-center-column">
        <header className="wb-subbar">
          <div className="wb-subbar-left">
            {leftCollapsed ? (
              <button
                type="button"
                className="wb-icon-btn"
                onClick={() => setLeftCollapsed(false)}
                title="Expand sidebar ([)"
                aria-label="Expand sidebar"
              >
                <PanelLeftOpen size={14} aria-hidden="true" />
              </button>
            ) : null}
            <span className="wb-breadcrumb-root">Workspace</span>
            <ChevronRight size={13} className="text-muted-foreground shrink-0" aria-hidden="true" />
            <span className="wb-breadcrumb-current">{breadcrumbLabel}</span>
          </div>
          <div className="wb-subbar-right">
            {headerActions}
            {inspector ? (
              <button
                type="button"
                className="wb-inspector-toggle"
                onClick={() => setRightCollapsed((v) => !v)}
                title={rightCollapsed ? 'Open inspector (])' : 'Hide inspector (])'}
                aria-label={rightCollapsed ? 'Expand inspector' : 'Collapse inspector'}
              >
                {rightCollapsed ? (
                  <PanelRightOpen size={14} aria-hidden="true" />
                ) : (
                  <PanelRightClose size={14} aria-hidden="true" />
                )}
                <span>Inspector</span>
                <kbd aria-hidden="true">]</kbd>
              </button>
            ) : null}
          </div>
        </header>
        <div className="wb-main-scroll">{children}</div>
      </div>

      {inspector ? (
        <aside
          className={`wb-inspector${rightCollapsed ? ' is-collapsed' : ''}`}
          aria-label="Stage inspector"
        >
          <div className="wb-inspector-header">
            <span className="wb-inspector-title">{inspectorTitle}</span>
            <button
              type="button"
              className="wb-icon-btn"
              onClick={() => setRightCollapsed(true)}
              title="Collapse inspector (])"
              aria-label="Collapse stage inspector"
            >
              <PanelRightClose size={14} aria-hidden="true" />
            </button>
          </div>
          <div className="wb-inspector-body">{inspector}</div>
        </aside>
      ) : null}
    </main>
  );
}
