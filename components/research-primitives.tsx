import type { ReactNode } from 'react';

export function StatusBadge({
  state,
  children,
}: {
  state: string;
  children?: ReactNode;
}) {
  return (
    <span className="research-status-badge" data-state={state}>
      {children ?? state.replaceAll('_', ' ')}
    </span>
  );
}

export function TrustBadge({ children = 'Hypothesis only' }: { children?: ReactNode }) {
  return <span className="research-trust-badge">{children}</span>;
}

export function DisclosureCard({
  title,
  children,
  defaultOpen = false,
}: {
  title: string;
  children: ReactNode;
  defaultOpen?: boolean;
}) {
  return (
    <details className="research-disclosure" open={defaultOpen || undefined}>
      <summary>{title}</summary>
      <div className="research-disclosure-body">{children}</div>
    </details>
  );
}

export function EmptyState({
  title,
  description,
  action,
}: {
  title: string;
  description: string;
  action?: ReactNode;
}) {
  return (
    <section className="research-empty-state" aria-label={title}>
      <div>
        <h2>{title}</h2>
        <p>{description}</p>
      </div>
      {action ? <div className="research-empty-state-action">{action}</div> : null}
    </section>
  );
}

export function SummaryCard({
  label,
  value,
  detail,
}: {
  label: string;
  value: ReactNode;
  detail?: ReactNode;
}) {
  return (
    <div className="research-summary-card">
      <span>{label}</span>
      <strong>{value}</strong>
      {detail ? <small>{detail}</small> : null}
    </div>
  );
}

export function StickyActionBar({ children }: { children: ReactNode }) {
  return <div className="research-sticky-action-bar">{children}</div>;
}

export function DetailsDrawer({
  title,
  children,
  defaultOpen = false,
}: {
  title: string;
  children: ReactNode;
  defaultOpen?: boolean;
}) {
  return (
    <details className="research-details-drawer" open={defaultOpen || undefined}>
      <summary>{title}</summary>
      <div>{children}</div>
    </details>
  );
}
