'use client';

import { Activity, AlertTriangle, CheckCircle2, Cpu, HardDrive, Layers, Lock, RefreshCw, ShieldCheck } from 'lucide-react';
import { useCallback, useEffect, useState } from 'react';

export type DeadLetterPriorState = 'queued' | 'running' | 'unknown';
export type DeadLetterReconciliationStatus =
  | 'pending'
  | 'quarantined'
  | 'retry_authorized'
  | 'reconciled_receipt';
export type DeadLetterReconcileDecision =
  | 'quarantine'
  | 'authorize_retry'
  | 'reconcile_receipt';

export interface DeadLetterJob {
  job_id: string;
  operation: string;
  target_uuid: string;
  prior_state: DeadLetterPriorState;
  failure_reason: string | null;
  reconciliation_status: DeadLetterReconciliationStatus;
  reserved_ms: number;
  reservation_retained: boolean;
  retry_eligible: boolean;
  authorized_retry_key: string | null;
  reconciled_receipt_id: string | null;
  recovered_at: number | null;
  reconciled_at: string | null;
  reconciled_by: string | null;
}

export interface DeadLetterReconcileResult {
  reconciliation_id: string;
  job_id: string;
  workspace_id: string;
  operation: string;
  target_uuid: string;
  prior_state: DeadLetterPriorState;
  decision: DeadLetterReconcileDecision;
  reconciliation_status: DeadLetterReconciliationStatus;
  reserved_ms: number;
  reservation_retained: boolean;
  receipt_found: boolean;
  reconciled_receipt_id: string | null;
  authorized_retry_key: string | null;
  reconciled_by: string;
  reconciled_at: string;
  replayed: boolean;
}

interface Subsystems {
  import?: {
    status?: string;
    allowed_hosts?: string[];
    max_html_bytes?: number;
    max_body_bytes?: number;
  };
  checker?: {
    status?: string;
    ram_limit_mb?: number;
    max_ram_bytes?: number;
    timeout_ms?: number;
    max_timeout_ms?: number;
    cpu_cores?: number;
    sandbox_available?: boolean;
  };
  worker?: {
    status?: string;
    active_queued?: number;
    active_running?: number;
    finished_jobs?: number;
    failed_jobs?: number;
    stuck_jobs?: number;
    metrics?: {
      total?: number;
      active_queued?: number;
      active_running?: number;
      finished?: number;
      failed?: number;
      stuck?: number;
      dead_letter_pending?: number;
      dead_letter_quarantined?: number;
    };
  };
  quotas?: {
    status?: string;
    max_request_bytes?: number;
    request_body_cap_bytes?: number;
    rate_limit_per_minute?: number;
    workspace_rate_limit_rpm?: number;
  };
}

interface OpsDashboardData {
  status: 'ok' | 'degraded';
  workspace_id?: string;
  actor_id?: string;
  checked_at: string;
  subsystems?: Subsystems;
  dead_letters?: DeadLetterJob[];
  quotas?: {
    status?: string;
    max_request_bytes?: number;
    request_body_cap_bytes?: number;
    rate_limit_per_minute?: number;
    workspace_rate_limit_rpm?: number;
  };
}

interface RecoveryResult {
  recovered_count: number;
  recovered_job_ids: string[];
  recovered_at: string;
}

const VALID_PRIOR_STATES = new Set<DeadLetterPriorState>(['queued', 'running', 'unknown']);
const VALID_RECONCILIATION_STATUSES = new Set<DeadLetterReconciliationStatus>([
  'pending',
  'quarantined',
  'retry_authorized',
  'reconciled_receipt',
]);

function isNonEmptyString(value: unknown): value is string {
  return typeof value === 'string' && value.trim().length > 0;
}

function isNullableString(value: unknown): value is string | null {
  return value === null || typeof value === 'string';
}

function isDeadLetterJob(value: unknown): value is DeadLetterJob {
  if (!value || typeof value !== 'object' || Array.isArray(value)) {
    return false;
  }
  const job = value as Record<string, unknown>;
  return (
    isNonEmptyString(job.job_id) &&
    isNonEmptyString(job.operation) &&
    isNonEmptyString(job.target_uuid) &&
    typeof job.prior_state === 'string' &&
    VALID_PRIOR_STATES.has(job.prior_state as DeadLetterPriorState) &&
    isNullableString(job.failure_reason) &&
    typeof job.reconciliation_status === 'string' &&
    VALID_RECONCILIATION_STATUSES.has(job.reconciliation_status as DeadLetterReconciliationStatus) &&
    typeof job.reserved_ms === 'number' &&
    Number.isFinite(job.reserved_ms) &&
    job.reserved_ms >= 0 &&
    typeof job.reservation_retained === 'boolean' &&
    typeof job.retry_eligible === 'boolean' &&
    isNullableString(job.authorized_retry_key) &&
    isNullableString(job.reconciled_receipt_id) &&
    (job.recovered_at === null || (typeof job.recovered_at === 'number' && Number.isFinite(job.recovered_at))) &&
    isNullableString(job.reconciled_at) &&
    isNullableString(job.reconciled_by)
  );
}

function createIdempotencyKey(prefix: string): string {
  if (typeof crypto !== 'undefined' && typeof crypto.randomUUID === 'function') {
    return crypto.randomUUID();
  }
  const raw = `${prefix}_${Date.now().toString(36)}_${Math.random().toString(36).slice(2, 10)}`;
  return raw.replace(/[^a-zA-Z0-9_-]/g, '').slice(0, 64);
}

function reconciliationDotColor(status: DeadLetterReconciliationStatus): string {
  switch (status) {
    case 'pending':
      return 'oklch(72% 0.13 75)';
    case 'quarantined':
      return 'oklch(58% 0.15 25)';
    case 'retry_authorized':
      return 'oklch(62% 0.12 240)';
    case 'reconciled_receipt':
      return 'oklch(60% 0.12 150)';
  }
}

function parseDashboard(value: unknown): OpsDashboardData {
  if (!value || typeof value !== 'object' ||
      !['ok', 'degraded'].includes((value as OpsDashboardData).status) ||
      typeof (value as OpsDashboardData).checked_at !== 'string') {
    throw new Error('Operations telemetry response is invalid.');
  }
  const data = value as OpsDashboardData;
  for (const group of [data.subsystems?.import, data.subsystems?.checker,
    data.subsystems?.worker, data.subsystems?.quotas, data.quotas]) {
    if (group === undefined) continue;
    if (group === null || typeof group !== 'object' || Array.isArray(group)) {
      throw new Error('Operations telemetry response is invalid.');
    }
    for (const [key, field] of Object.entries(group)) {
      if (key === 'allowed_hosts') {
        if (!Array.isArray(field) || !field.every(host => typeof host === 'string')) {
          throw new Error('Operations host policy is invalid.');
        }
      } else if (key === 'metrics') {
        if (field !== null && (typeof field !== 'object' || Array.isArray(field) ||
            Object.values(field).some(metric => typeof metric !== 'number' || !Number.isFinite(metric) || metric < 0))) {
          throw new Error('Operations queue metrics are invalid.');
        }
      } else if (field !== null && !['status', 'sandbox_image', 'sandbox_available'].includes(key) &&
          (typeof field !== 'number' || !Number.isFinite(field) || field < 0)) {
        throw new Error('Operations numeric limits are invalid.');
      }
    }
  }
  if (data.dead_letters !== undefined) {
    if (!Array.isArray(data.dead_letters) || !data.dead_letters.every(isDeadLetterJob)) {
      throw new Error('Operations dead-letter ledger is invalid.');
    }
  }
  return value as OpsDashboardData;
}

function pillClass(status: string) {
  return status === 'ok' || status === 'healthy' ? 'healthy' : 'degraded';
}

export function OpsDashboard() {
  const [data, setData] = useState<OpsDashboardData | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [recovering, setRecovering] = useState(false);
  const [recoveryResult, setRecoveryResult] = useState<RecoveryResult | null>(null);
  const [rationaleByJob, setRationaleByJob] = useState<Record<string, string>>({});
  const [validationByJob, setValidationByJob] = useState<Record<string, string>>({});
  const [reconcilingJobId, setReconcilingJobId] = useState<string | null>(null);
  const [reconcileResult, setReconcileResult] = useState<DeadLetterReconcileResult | null>(null);

  const fetchDashboard = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      const response = await fetch('/api/research/ops/dashboard', {
        headers: { Accept: 'application/json' },
      });
      if (!response.ok) {
        throw new Error(`Failed to load ops telemetry: HTTP ${response.status}`);
      }
      const json = parseDashboard(await response.json());
      setData(json);
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Unknown error loading operations data');
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    let active = true;
    fetch('/api/research/ops/dashboard', {
      headers: { Accept: 'application/json' },
    })
      .then(async (response) => {
        if (!response.ok) throw new Error(`Failed to load ops telemetry: HTTP ${response.status}`);
        return parseDashboard(await response.json());
      })
      .then((json) => {
        if (active) {
          setData(json);
          setLoading(false);
        }
      })
      .catch((err) => {
        if (active) {
          setError(err instanceof Error ? err.message : 'Unknown error loading operations data');
          setLoading(false);
        }
      });
    return () => {
      active = false;
    };
  }, []);

  const handleRecover = async () => {
    // ponytail: on-demand sweep button instead of polling cron, ceiling: requires operator manual trigger or page visit, upgrade: add background scheduler when worker daemon is introduced.
    setRecovering(true);
    setError(null);
    try {
      const idempotencyKey = `rec_${Date.now()}_${Math.random().toString(36).slice(2, 9)}`;
      const response = await fetch('/api/research/ops/jobs/recover', {
        method: 'POST',
        headers: {
          'Content-Type': 'application/json',
          'x-idempotency-key': idempotencyKey,
        },
        body: '{}',
      });
      if (!response.ok) {
        throw new Error(`Job recovery failed: HTTP ${response.status}`);
      }
      const result = (await response.json()) as RecoveryResult;
      setRecoveryResult(result);
      await fetchDashboard();
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Recovery operation failed');
    } finally {
      setRecovering(false);
    }
  };

  const handleReconcile = async (job: DeadLetterJob, decision: DeadLetterReconcileDecision) => {
    const notes = (rationaleByJob[job.job_id] ?? '').trim();
    if (notes.length < 3) {
      setValidationByJob((prev) => ({
        ...prev,
        [job.job_id]: 'Rationale must be at least 3 characters.',
      }));
      return;
    }
    if (decision === 'authorize_retry' && (job.prior_state !== 'queued' || !job.retry_eligible)) {
      return;
    }

    setValidationByJob((prev) => {
      const next = { ...prev };
      delete next[job.job_id];
      return next;
    });
    setReconcilingJobId(job.job_id);
    setError(null);
    try {
      const response = await fetch(`/api/research/ops/jobs/${encodeURIComponent(job.job_id)}/reconcile`, {
        method: 'POST',
        headers: {
          'Content-Type': 'application/json',
          Accept: 'application/json',
          'x-idempotency-key': createIdempotencyKey('reconcile'),
        },
        body: JSON.stringify({ decision, notes }),
      });
      if (!response.ok) {
        throw new Error(`Dead-letter reconciliation failed: HTTP ${response.status}`);
      }
      const result = (await response.json()) as DeadLetterReconcileResult;
      setReconcileResult(result);
      setRationaleByJob((prev) => {
        const next = { ...prev };
        delete next[job.job_id];
        return next;
      });
      await fetchDashboard();
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Dead-letter reconciliation failed');
    } finally {
      setReconcilingJobId(null);
    }
  };

  // Safe normalized telemetry values preventing undefined property crashes
  const quotas = data?.quotas ?? data?.subsystems?.quotas;
  const rateLimitRpm = quotas?.rate_limit_per_minute ?? quotas?.workspace_rate_limit_rpm ?? 120;
  const maxRequestBytes = quotas?.max_request_bytes ?? quotas?.request_body_cap_bytes ?? 2097152;
  const quotasStatus = quotas?.status ?? 'unknown';

  const importSub = data?.subsystems?.import;
  const importStatus = importSub?.status ?? 'unknown';
  const allowedHosts = importSub?.allowed_hosts ?? ['arxiv.org', 'export.arxiv.org'];
  const maxHtmlBytes = importSub?.max_html_bytes;

  const checkerSub = data?.subsystems?.checker;
  const checkerStatus = checkerSub?.status ?? 'unknown';
  const ramLimitMb = checkerSub?.ram_limit_mb ?? (checkerSub?.max_ram_bytes ? Math.round(checkerSub.max_ram_bytes / (1024 * 1024)) : 256);
  const timeoutMs = checkerSub?.timeout_ms ?? checkerSub?.max_timeout_ms ?? 10000;
  const cpuCores = checkerSub?.cpu_cores ?? 1;
  const sandboxAvailable = checkerSub?.sandbox_available ?? false;

  const workerSub = data?.subsystems?.worker;
  const metrics = workerSub?.metrics;
  const workerStatus = workerSub?.status ?? 'unknown';
  const activeQueued = workerSub?.active_queued ?? metrics?.active_queued ?? 0;
  const activeRunning = workerSub?.active_running ?? metrics?.active_running ?? 0;
  const finishedJobs = workerSub?.finished_jobs ?? metrics?.finished ?? 0;
  const stuckJobs = workerSub?.stuck_jobs ?? metrics?.stuck ?? 0;
  const deadLetters = data?.dead_letters ?? [];
  const deadLetterPendingCount =
    metrics?.dead_letter_pending ??
    deadLetters.filter((job) => job.reconciliation_status === 'pending').length;
  const deadLetterQuarantinedCount =
    metrics?.dead_letter_quarantined ??
    deadLetters.filter((job) => job.reconciliation_status === 'quarantined').length;

  const unavailable = Boolean(error) || !data || !importSub || !checkerSub || !workerSub ||
    workerStatus === 'unavailable';
  const isDegraded = unavailable || data?.status === 'degraded' || stuckJobs > 0;

  return (
    <div className="ops-dashboard">
      <header className="standalone-stage-heading">
        <div>
          <p className="eyebrow">
            <Activity size={13} aria-hidden="true" /> Infrastructure &amp; Runtime Bounds
          </p>
          <h1>Operations &amp; Health Dashboard</h1>
          <p>
            Subsystem bounds, resource ceilings, rate limit enforcement, and stuck-job recovery.
          </p>
        </div>
        <div style={{ display: 'flex', alignItems: 'center', gap: '8px', flexWrap: 'wrap' }}>
          <button
            type="button"
            className="workspace-help-link"
            onClick={() => { void fetchDashboard(); }}
            disabled={loading}
            style={{ cursor: loading ? 'not-allowed' : 'pointer' }}
            aria-label="Refresh dashboard data"
          >
            <RefreshCw size={13} className={loading ? 'animate-spin' : ''} aria-hidden="true" />
            Refresh
          </button>
        </div>
      </header>

      {/* Global Status Banner */}
      <section
        className={`ops-hero-status ${isDegraded ? 'is-degraded' : 'is-ok'}`}
        aria-live="polite"
      >
        <div style={{ display: 'flex', alignItems: 'center', gap: '10px' }}>
          {isDegraded ? (
            <AlertTriangle size={18} style={{ color: '#d97706' }} aria-hidden="true" />
          ) : (
            <CheckCircle2 size={18} style={{ color: '#059669' }} aria-hidden="true" />
          )}
          <div>
            <div style={{ fontWeight: 650, fontSize: '13px', color: 'var(--foreground)' }}>
              {loading && !data
                ? 'Connecting to graph service...'
                : unavailable
                ? 'Operations telemetry unavailable'
                : isDegraded
                ? 'Workspace queue needs attention'
                : 'Workspace queue telemetry available'}
            </div>
            {data && (
              <div style={{ fontSize: '11px', color: 'var(--muted-foreground)' }}>
                Checked at {new Date(data.checked_at).toLocaleTimeString()}
                {data.workspace_id && (
                  <> &bull; Workspace: <code>{data.workspace_id}</code></>
                )}
              </div>
            )}
          </div>
        </div>

        {data && (
          <div style={{ display: 'flex', gap: '16px', fontSize: '11px', color: 'var(--muted-foreground)' }}>
            <div>
              Status: <span style={{ fontWeight: 700, color: isDegraded ? '#d97706' : '#059669' }}>{(data.status || 'OK').toUpperCase()}</span>
            </div>
          </div>
        )}
      </section>

      {/* Metric Strip (Operator-First Topline Telemetry) */}
      {data && (
        <section className="ops-metric-strip" aria-label="Key health metrics">
          <div className="ops-metric-tile">
            <div className="ops-metric-tile-label">
              <Activity size={12} aria-hidden="true" /> Subsystems
            </div>
            <div className="ops-metric-tile-val" style={{ color: isDegraded ? '#d97706' : '#059669' }}>
              {unavailable ? 'UNKNOWN' : isDegraded ? 'DEGRADED' : 'REPORTED'}
            </div>
            <div className="ops-metric-tile-sub">Configuration is not a live execution check</div>
          </div>

          <div className="ops-metric-tile">
            <div className="ops-metric-tile-label">
              <Layers size={12} aria-hidden="true" /> Active Jobs
            </div>
            <div className="ops-metric-tile-val">
              {unavailable ? '—' : activeRunning + activeQueued}
            </div>
            <div className="ops-metric-tile-sub">
              {unavailable ? 'Queue metrics unavailable' : `${activeRunning} running · ${activeQueued} queued`}
            </div>
          </div>

          <div className="ops-metric-tile">
            <div className="ops-metric-tile-label">
              <Cpu size={12} aria-hidden="true" /> Worker Bound
            </div>
            <div className="ops-metric-tile-val">
              {ramLimitMb} <span style={{ fontSize: '13px', fontWeight: 500 }}>MB</span>
            </div>
            <div className="ops-metric-tile-sub">
              {(timeoutMs / 1000).toFixed(0)}s timeout &bull; {cpuCores} core
            </div>
          </div>

          <div className="ops-metric-tile">
            <div className="ops-metric-tile-label">
              <ShieldCheck size={12} aria-hidden="true" /> Ingress Quota
            </div>
            <div className="ops-metric-tile-val">
              {quotas ? rateLimitRpm : '—'} <span style={{ fontSize: '13px', fontWeight: 500 }}>RPM</span>
            </div>
            <div className="ops-metric-tile-sub">
              {quotas ? `${(maxRequestBytes / (1024 * 1024)).toFixed(0)} MB max payload` : 'Quota configuration unknown'}
            </div>
          </div>
        </section>
      )}

      {/* Error alert */}
      {error && (
        <div
          role="alert"
          style={{
            padding: '12px 16px',
            borderRadius: '8px',
            background: 'color-mix(in oklab, #ef4444 12%, var(--card))',
            border: '1px solid #ef444440',
            color: '#b91c1c',
            fontSize: '12px',
            marginBottom: '16px',
          }}
        >
          {error}
        </div>
      )}

      {/* Recovery Notice */}
      {recoveryResult && (
        <output
          style={{
            display: 'flex',
            justifyContent: 'space-between',
            alignItems: 'center',
            padding: '12px 16px',
            borderRadius: '8px',
            background: 'color-mix(in oklab, #3b82f6 10%, var(--card))',
            border: '1px solid #3b82f640',
            color: 'var(--foreground)',
            fontSize: '12px',
            marginBottom: '16px',
          }}
        >
          <span>
            Successfully swept queue. Recovered <strong>{recoveryResult.recovered_count}</strong> stuck or expired job(s).
          </span>
          <button
            type="button"
            onClick={() => setRecoveryResult(null)}
            style={{
              background: 'none',
              border: 'none',
              fontSize: '11px',
              color: 'var(--muted-foreground)',
              cursor: 'pointer',
              textDecoration: 'underline',
            }}
          >
            Dismiss
          </button>
        </output>
      )}

      {/* Subsystem Cards Grid */}
      {data && (
        <div className="ops-subsystems-grid">
          {/* 1. Import Subsystem */}
          <article className="ops-card">
            <div className="ops-card-header">
              <h2 className="ops-card-title">
                <HardDrive size={15} style={{ color: 'var(--primary)' }} aria-hidden="true" />
                Import Guard
              </h2>
              <span className={`ops-pill pill-${pillClass(importStatus)}`}>
                {importStatus === 'ok' ? 'Healthy' : importStatus}
              </span>
            </div>
            <p className="ops-card-desc">
              Controls arXiv HTML paper fetching, payload size caps, and network boundaries.
            </p>
            <dl className="ops-dl">
              <div className="ops-dl-row">
                <dt>Allowed Hosts</dt>
                <dd>{allowedHosts.join(', ')}</dd>
              </div>
              <div className="ops-dl-row">
                <dt>Max HTML Size</dt>
                <dd>{typeof maxHtmlBytes === 'number' ? `${(maxHtmlBytes / (1024 * 1024)).toFixed(0)} MB` : 'Unknown'}</dd>
              </div>
              <div className="ops-dl-row">
                <dt>SSRF Boundary</dt>
                <dd>{importSub?.allowed_hosts ? 'Allowlist configured' : 'Unknown'}</dd>
              </div>
            </dl>
          </article>

          {/* 2. Checker & Sandbox Subsystem */}
          <article className="ops-card">
            <div className="ops-card-header">
              <h2 className="ops-card-title">
                <Cpu size={15} style={{ color: 'var(--primary)' }} aria-hidden="true" />
                Checker Execution Bounds
              </h2>
              <span className={`ops-pill pill-${pillClass(checkerStatus)}`}>
                {checkerStatus === 'ok' ? 'Healthy' : checkerStatus}
              </span>
            </div>
            <p className="ops-card-desc">
              Hard OS memory limits, CPU bounds, and execution deadlines for SymPy and Python workers.
            </p>
            <dl className="ops-dl">
              <div className="ops-dl-row">
                <dt>RAM Ceiling</dt>
                <dd>{ramLimitMb} MB</dd>
              </div>
              <div className="ops-dl-row">
                <dt>Execution Deadline</dt>
                <dd>{(timeoutMs / 1000).toFixed(1)}s</dd>
              </div>
              <div className="ops-dl-row">
                <dt>CPU Cores Limit</dt>
                <dd>{cpuCores} Core</dd>
              </div>
              <div className="ops-dl-row">
                <dt>Sandbox Engine</dt>
                <dd>{sandboxAvailable ? 'Docker Container' : 'Host Process'}</dd>
              </div>
            </dl>
          </article>

          {/* 3. Worker Queue & Recovery */}
          <article className="ops-card">
            <div className="ops-card-header">
              <h2 className="ops-card-title">
                <Layers size={15} style={{ color: 'var(--primary)' }} aria-hidden="true" />
                Job Queue &amp; Recovery
              </h2>
              <span className={`ops-pill pill-${unavailable || stuckJobs > 0 ? 'degraded' : pillClass(workerStatus)}`}>
                {unavailable ? 'Unavailable' : stuckJobs > 0 ? 'Degraded' : workerStatus}
              </span>
            </div>
            <p className="ops-card-desc">
              Workspace job counters. Sweeping marks expired jobs failed; it does not rerun them.
            </p>

            <div
              style={{
                display: 'grid',
                gridTemplateColumns: 'repeat(4, 1fr)',
                gap: '8px',
                margin: '4px 0',
                textAlign: 'center',
              }}
            >
              <div style={{ background: 'var(--accent)', padding: '6px', borderRadius: '6px' }}>
                <div style={{ fontSize: '10px', color: 'var(--muted-foreground)' }}>Queued</div>
                <div style={{ fontSize: '13px', fontWeight: 700, fontFamily: 'var(--font-geist-mono)' }}>
                  {activeQueued}
                </div>
              </div>
              <div style={{ background: 'var(--accent)', padding: '6px', borderRadius: '6px' }}>
                <div style={{ fontSize: '10px', color: 'var(--muted-foreground)' }}>Running</div>
                <div style={{ fontSize: '13px', fontWeight: 700, fontFamily: 'var(--font-geist-mono)' }}>
                  {activeRunning}
                </div>
              </div>
              <div style={{ background: 'var(--accent)', padding: '6px', borderRadius: '6px' }}>
                <div style={{ fontSize: '10px', color: 'var(--muted-foreground)' }}>Finished</div>
                <div style={{ fontSize: '13px', fontWeight: 700, fontFamily: 'var(--font-geist-mono)' }}>
                  {finishedJobs}
                </div>
              </div>
              <div
                style={{
                  background: stuckJobs > 0 ? '#ef444415' : 'var(--accent)',
                  padding: '6px',
                  borderRadius: '6px',
                  border: stuckJobs > 0 ? '1px solid #ef444440' : 'none',
                }}
              >
                <div style={{ fontSize: '10px', color: stuckJobs > 0 ? '#b91c1c' : 'var(--muted-foreground)' }}>
                  Stuck
                </div>
                <div
                  style={{
                    fontSize: '13px',
                    fontWeight: 700,
                    fontFamily: 'var(--font-geist-mono)',
                    color: stuckJobs > 0 ? '#b91c1c' : 'inherit',
                  }}
                >
                  {stuckJobs}
                </div>
              </div>
            </div>

            <button
              type="button"
              className="ops-recover-btn"
              onClick={() => { void handleRecover(); }}
              disabled={recovering || unavailable}
            >
              <RefreshCw size={12} className={recovering ? 'animate-spin' : ''} aria-hidden="true" />
              {recovering ? 'Sweeping Queue...' : 'Sweep & Recover Stuck Jobs'}
            </button>
          </article>

          {/* 4. Quotas & Log Sanitization */}
          <article className="ops-card">
            <div className="ops-card-header">
              <h2 className="ops-card-title">
                <ShieldCheck size={15} style={{ color: 'var(--primary)' }} aria-hidden="true" />
                Quotas &amp; Sanitization
              </h2>
              <span className={`ops-pill pill-${pillClass(quotasStatus)}`}>
                {quotasStatus === 'ok' ? 'Healthy' : quotasStatus}
              </span>
            </div>
            <p className="ops-card-desc">
              Protects API ingress with body payload ceilings, rate limits, and secret scrubbing.
            </p>
            <dl className="ops-dl">
              <div className="ops-dl-row">
                <dt>Ingress Body Limit</dt>
                <dd>{(maxRequestBytes / (1024 * 1024)).toFixed(0)} MB</dd>
              </div>
              <div className="ops-dl-row">
                <dt>Workspace Rate Cap</dt>
                <dd>{rateLimitRpm} req / min</dd>
              </div>
              <div className="ops-dl-row">
                <dt>Log Sanitization</dt>
                <dd>Requires deployment log-sink verification</dd>
              </div>
            </dl>
          </article>
        </div>
      )}

      {/* Dead-Letter & Reconciliation Ledger */}
      {data && (
        <section
          className="ops-card"
          aria-labelledby="dead-letter-ledger-heading"
          style={{ marginTop: '16px', padding: '16px', gap: '12px' }}
        >
          <div className="ops-card-header" style={{ flexWrap: 'wrap', gap: '8px' }}>
            <h2 id="dead-letter-ledger-heading" className="ops-card-title">
              <Lock size={15} style={{ color: 'var(--primary)' }} aria-hidden="true" />
              Dead-Letter &amp; Reconciliation Ledger
            </h2>
            <div
              style={{
                display: 'flex',
                alignItems: 'center',
                gap: '12px',
                fontSize: '12px',
                fontFamily: 'var(--font-geist-mono, monospace)',
                fontVariantNumeric: 'tabular-nums',
                color: 'var(--muted-foreground)',
              }}
            >
              <span style={{ display: 'inline-flex', alignItems: 'center', gap: '6px' }}>
                <span
                  aria-hidden="true"
                  style={{
                    width: '6px',
                    height: '6px',
                    borderRadius: '9999px',
                    background: reconciliationDotColor('pending'),
                  }}
                />
                pending: <strong style={{ color: 'var(--foreground)' }}>{deadLetterPendingCount}</strong>
              </span>
              <span style={{ display: 'inline-flex', alignItems: 'center', gap: '6px' }}>
                <span
                  aria-hidden="true"
                  style={{
                    width: '6px',
                    height: '6px',
                    borderRadius: '9999px',
                    background: reconciliationDotColor('quarantined'),
                  }}
                />
                quarantined: <strong style={{ color: 'var(--foreground)' }}>{deadLetterQuarantinedCount}</strong>
              </span>
            </div>
          </div>

          <div
            style={{
              padding: '8px 12px',
              borderRadius: '6px',
              border: '1px solid var(--border)',
              background: 'var(--accent)',
              fontSize: '11px',
              lineHeight: 1.45,
              color: 'var(--muted-foreground)',
            }}
          >
            <strong style={{ color: 'var(--foreground)', fontWeight: 600 }}>Safety invariants:</strong>{' '}
            reservations (<code style={{ fontFamily: 'var(--font-geist-mono, monospace)' }}>reserved_ms</code>) stay consumed (
            <code style={{ fontFamily: 'var(--font-geist-mono, monospace)' }}>retained</code>), uncertain{' '}
            <code style={{ fontFamily: 'var(--font-geist-mono, monospace)' }}>running</code> jobs cannot be auto-retried, and
            recovery never promotes campaign winners.
          </div>

          {reconcileResult && (
            <output
              style={{
                display: 'flex',
                justifyContent: 'space-between',
                alignItems: 'center',
                gap: '12px',
                padding: '8px 12px',
                borderRadius: '6px',
                border: '1px solid var(--border)',
                background: 'var(--card)',
                fontSize: '12px',
                color: 'var(--foreground)',
              }}
            >
              <span style={{ fontVariantNumeric: 'tabular-nums' }}>
                Reconciled <code style={{ fontFamily: 'var(--font-geist-mono, monospace)' }}>{reconcileResult.job_id}</code>{' '}
                &rarr; <strong>{reconcileResult.reconciliation_status}</strong> ({reconcileResult.reserved_ms} ms{' '}
                {reconcileResult.reservation_retained ? 'retained' : 'released'})
                {reconcileResult.authorized_retry_key && (
                  <>
                    {' '}
                    &bull; retry key:{' '}
                    <code style={{ fontFamily: 'var(--font-geist-mono, monospace)' }}>
                      {reconcileResult.authorized_retry_key}
                    </code>
                  </>
                )}
                {reconcileResult.reconciled_receipt_id && (
                  <>
                    {' '}
                    &bull; receipt:{' '}
                    <code style={{ fontFamily: 'var(--font-geist-mono, monospace)' }}>
                      {reconcileResult.reconciled_receipt_id}
                    </code>
                  </>
                )}
                {reconcileResult.replayed && ' (idempotent replay)'}
              </span>
              <button
                type="button"
                onClick={() => setReconcileResult(null)}
                style={{
                  background: 'none',
                  border: 'none',
                  fontSize: '11px',
                  color: 'var(--muted-foreground)',
                  cursor: 'pointer',
                  textDecoration: 'underline',
                }}
              >
                Dismiss
              </button>
            </output>
          )}

          {deadLetters.length === 0 ? (
            <div
              style={{
                padding: '12px',
                borderRadius: '6px',
                border: '1px dashed var(--border)',
                fontSize: '12px',
                color: 'var(--muted-foreground)',
                fontVariantNumeric: 'tabular-nums',
              }}
            >
              <strong style={{ color: 'var(--foreground)' }}>0 dead-letter jobs awaiting reconciliation</strong>
              <span style={{ marginLeft: '8px' }}>
                Swept jobs appear here for receipt inspection, quarantine, or queued-only retry authorization.
              </span>
            </div>
          ) : (
            <div style={{ overflowX: 'auto' }}>
              <table
                style={{
                  width: '100%',
                  borderCollapse: 'collapse',
                  fontSize: '12px',
                }}
              >
                <thead>
                  <tr
                    style={{
                      borderBottom: '1px solid var(--border)',
                      color: 'var(--muted-foreground)',
                      fontSize: '11px',
                      textAlign: 'left',
                    }}
                  >
                    <th style={{ padding: '8px 12px', fontWeight: 600 }}>Job / Target</th>
                    <th style={{ padding: '8px 12px', fontWeight: 600 }}>Operation</th>
                    <th style={{ padding: '8px 12px', fontWeight: 600 }}>Prior State</th>
                    <th style={{ padding: '8px 12px', fontWeight: 600, textAlign: 'right' }}>Budget</th>
                    <th style={{ padding: '8px 12px', fontWeight: 600 }}>Status</th>
                    <th style={{ padding: '8px 12px', fontWeight: 600 }}>Reconciliation</th>
                  </tr>
                </thead>
                <tbody>
                  {deadLetters.map((job) => {
                    const isActionable =
                      job.reconciliation_status === 'pending' || job.reconciliation_status === 'quarantined';
                    const retryBlocked = job.prior_state !== 'queued' || !job.retry_eligible;
                    const isBusy = reconcilingJobId === job.job_id;
                    const rowValidation = validationByJob[job.job_id];
                    const priorLabel =
                      job.prior_state === 'queued'
                        ? 'queued'
                        : job.prior_state === 'running'
                        ? 'running · uncertain'
                        : 'unknown · uncertain';

                    return (
                      <tr
                        key={job.job_id}
                        style={{
                          borderBottom: '1px solid color-mix(in oklab, var(--border) 60%, transparent)',
                          verticalAlign: 'top',
                        }}
                      >
                        <td
                          style={{
                            padding: '8px 12px',
                            fontFamily: 'var(--font-geist-mono, monospace)',
                            fontVariantNumeric: 'tabular-nums slashed-zero',
                            whiteSpace: 'nowrap',
                          }}
                        >
                          <div style={{ color: 'var(--foreground)', fontWeight: 600 }}>{job.job_id}</div>
                          <div style={{ fontSize: '11px', color: 'var(--muted-foreground)' }}>{job.target_uuid}</div>
                        </td>
                        <td style={{ padding: '8px 12px' }}>
                          <div style={{ color: 'var(--foreground)', fontWeight: 500 }}>{job.operation}</div>
                          {job.failure_reason && (
                            <div
                              style={{
                                fontSize: '11px',
                                color: 'var(--muted-foreground)',
                                fontFamily: 'var(--font-geist-mono, monospace)',
                              }}
                            >
                              {job.failure_reason}
                            </div>
                          )}
                        </td>
                        <td
                          style={{
                            padding: '8px 12px',
                            fontFamily: 'var(--font-geist-mono, monospace)',
                            whiteSpace: 'nowrap',
                            color: job.prior_state === 'queued' ? 'var(--foreground)' : 'oklch(72% 0.13 75)',
                          }}
                        >
                          {priorLabel}
                        </td>
                        <td
                          style={{
                            padding: '8px 12px',
                            textAlign: 'right',
                            fontFamily: 'var(--font-geist-mono, monospace)',
                            fontVariantNumeric: 'tabular-nums',
                            whiteSpace: 'nowrap',
                          }}
                        >
                          <div style={{ color: 'var(--foreground)', fontWeight: 600 }}>{job.reserved_ms} ms</div>
                          <div style={{ fontSize: '11px', color: 'var(--muted-foreground)' }}>
                            {job.reservation_retained ? 'retained' : 'released'}
                          </div>
                        </td>
                        <td style={{ padding: '8px 12px', whiteSpace: 'nowrap' }}>
                          <div
                            style={{
                              display: 'inline-flex',
                              alignItems: 'center',
                              gap: '6px',
                              fontFamily: 'var(--font-geist-mono, monospace)',
                              color: 'var(--foreground)',
                            }}
                          >
                            <span
                              aria-hidden="true"
                              style={{
                                width: '6px',
                                height: '6px',
                                borderRadius: '9999px',
                                background: reconciliationDotColor(job.reconciliation_status),
                                flexShrink: 0,
                              }}
                            />
                            {job.reconciliation_status}
                          </div>
                          {job.authorized_retry_key && (
                            <div
                              style={{
                                fontSize: '11px',
                                color: 'var(--muted-foreground)',
                                fontFamily: 'var(--font-geist-mono, monospace)',
                                fontVariantNumeric: 'tabular-nums slashed-zero',
                              }}
                            >
                              retry: {job.authorized_retry_key}
                            </div>
                          )}
                          {job.reconciled_receipt_id && (
                            <div
                              style={{
                                fontSize: '11px',
                                color: 'var(--muted-foreground)',
                                fontFamily: 'var(--font-geist-mono, monospace)',
                                fontVariantNumeric: 'tabular-nums slashed-zero',
                              }}
                            >
                              receipt: {job.reconciled_receipt_id}
                            </div>
                          )}
                        </td>
                        <td style={{ padding: '8px 12px', minWidth: '280px' }}>
                          {isActionable ? (
                            <div style={{ display: 'flex', flexDirection: 'column', gap: '8px' }}>
                              <input
                                type="text"
                                aria-label={`Reconciliation rationale for ${job.job_id}`}
                                placeholder="Rationale (min 3 chars)"
                                value={rationaleByJob[job.job_id] ?? ''}
                                disabled={isBusy}
                                onChange={(event) => {
                                  const nextValue = event.target.value;
                                  setRationaleByJob((prev) => ({ ...prev, [job.job_id]: nextValue }));
                                  if (nextValue.trim().length >= 3 && rowValidation) {
                                    setValidationByJob((prev) => {
                                      const next = { ...prev };
                                      delete next[job.job_id];
                                      return next;
                                    });
                                  }
                                }}
                                style={{
                                  width: '100%',
                                  padding: '4px 8px',
                                  borderRadius: '6px',
                                  border: '1px solid var(--border)',
                                  background: 'var(--card)',
                                  color: 'var(--foreground)',
                                  fontSize: '12px',
                                  boxSizing: 'border-box',
                                }}
                              />
                              {rowValidation && (
                                <div style={{ fontSize: '11px', color: 'oklch(58% 0.15 25)' }}>{rowValidation}</div>
                              )}
                              <div style={{ display: 'flex', flexWrap: 'wrap', gap: '8px' }}>
                                <button
                                  type="button"
                                  disabled={isBusy}
                                  onClick={() => {
                                    void handleReconcile(job, 'quarantine');
                                  }}
                                  style={{
                                    padding: '4px 8px',
                                    borderRadius: '6px',
                                    border: '1px solid var(--border)',
                                    background: 'var(--card)',
                                    color: 'var(--foreground)',
                                    fontSize: '11px',
                                    fontWeight: 600,
                                    cursor: isBusy ? 'not-allowed' : 'pointer',
                                  }}
                                >
                                  Quarantine
                                </button>
                                <button
                                  type="button"
                                  disabled={isBusy}
                                  onClick={() => {
                                    void handleReconcile(job, 'reconcile_receipt');
                                  }}
                                  style={{
                                    padding: '4px 8px',
                                    borderRadius: '6px',
                                    border: '1px solid var(--border)',
                                    background: 'var(--card)',
                                    color: 'var(--foreground)',
                                    fontSize: '11px',
                                    fontWeight: 600,
                                    cursor: isBusy ? 'not-allowed' : 'pointer',
                                  }}
                                >
                                  Check Receipt
                                </button>
                                <button
                                  type="button"
                                  disabled={isBusy || retryBlocked}
                                  title={
                                    retryBlocked
                                      ? 'Uncertain jobs that reached running state cannot be retried'
                                      : 'Authorize retry for queued job'
                                  }
                                  onClick={() => {
                                    void handleReconcile(job, 'authorize_retry');
                                  }}
                                  style={{
                                    padding: '4px 8px',
                                    borderRadius: '6px',
                                    border: '1px solid var(--border)',
                                    background: 'var(--card)',
                                    color: retryBlocked ? 'var(--muted-foreground)' : 'var(--foreground)',
                                    fontSize: '11px',
                                    fontWeight: 600,
                                    opacity: retryBlocked || isBusy ? 0.55 : 1,
                                    cursor: retryBlocked || isBusy ? 'not-allowed' : 'pointer',
                                  }}
                                >
                                  Authorize Retry
                                </button>
                              </div>
                            </div>
                          ) : (
                            <div
                              style={{
                                fontSize: '11px',
                                color: 'var(--muted-foreground)',
                                fontFamily: 'var(--font-geist-mono, monospace)',
                              }}
                            >
                              {job.reconciled_by ?? 'operator'}
                              {job.reconciled_at ? ` · ${job.reconciled_at}` : ''}
                            </div>
                          )}
                        </td>
                      </tr>
                    );
                  })}
                </tbody>
              </table>
            </div>
          )}
        </section>
      )}
    </div>
  );
}
