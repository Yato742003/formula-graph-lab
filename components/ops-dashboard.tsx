'use client';

import { Activity, AlertTriangle, CheckCircle2, Cpu, HardDrive, Layers, RefreshCw, ShieldCheck } from 'lucide-react';
import { useCallback, useEffect, useState } from 'react';

interface Subsystems {
  import: {
    status: string;
    allowed_hosts: string[];
    max_html_bytes: number;
  };
  checker: {
    status: string;
    ram_limit_mb: number;
    timeout_ms: number;
    cpu_cores: number;
    sandbox_available: boolean;
  };
  worker: {
    status: string;
    active_queued: number;
    active_running: number;
    finished_jobs: number;
    failed_jobs: number;
    stuck_jobs: number;
  };
  quotas: {
    status: string;
    max_request_bytes: number;
    rate_limit_per_minute: number;
  };
}

interface OpsDashboardData {
  status: 'ok' | 'degraded';
  workspace_id: string;
  actor_id: string;
  checked_at: string;
  subsystems: Subsystems;
}

interface RecoveryResult {
  recovered_count: number;
  recovered_job_ids: string[];
  recovered_at: string;
}

export function OpsDashboard() {
  const [data, setData] = useState<OpsDashboardData | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [recovering, setRecovering] = useState(false);
  const [recoveryResult, setRecoveryResult] = useState<RecoveryResult | null>(null);

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
      const json = (await response.json()) as OpsDashboardData;
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
        return (await response.json()) as OpsDashboardData;
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

  const isDegraded = data?.status === 'degraded';

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
                : isDegraded
                ? 'System Degraded: Stuck jobs detected'
                : 'All Subsystems Operational'}
            </div>
            {data && (
              <div style={{ fontSize: '11px', color: 'var(--muted-foreground)' }}>
                Checked at {new Date(data.checked_at).toLocaleTimeString()} &bull; Workspace:{' '}
                <code>{data.workspace_id}</code>
              </div>
            )}
          </div>
        </div>

        {data && (
          <div style={{ display: 'flex', gap: '16px', fontSize: '11px', color: 'var(--muted-foreground)' }}>
            <div>
              Status: <span style={{ fontWeight: 700, color: isDegraded ? '#d97706' : '#059669' }}>{data.status.toUpperCase()}</span>
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
              {data.status === 'ok' ? 'HEALTHY' : 'DEGRADED'}
            </div>
            <div className="ops-metric-tile-sub">4 active controllers</div>
          </div>

          <div className="ops-metric-tile">
            <div className="ops-metric-tile-label">
              <Layers size={12} aria-hidden="true" /> Active Jobs
            </div>
            <div className="ops-metric-tile-val">
              {data.subsystems.worker.active_running + data.subsystems.worker.active_queued}
            </div>
            <div className="ops-metric-tile-sub">
              {data.subsystems.worker.active_running} running &bull; {data.subsystems.worker.active_queued} queued
            </div>
          </div>

          <div className="ops-metric-tile">
            <div className="ops-metric-tile-label">
              <Cpu size={12} aria-hidden="true" /> Worker Bound
            </div>
            <div className="ops-metric-tile-val">
              {data.subsystems.checker.ram_limit_mb} <span style={{ fontSize: '13px', fontWeight: 500 }}>MB</span>
            </div>
            <div className="ops-metric-tile-sub">
              {(data.subsystems.checker.timeout_ms / 1000).toFixed(0)}s timeout &bull; {data.subsystems.checker.cpu_cores} core
            </div>
          </div>

          <div className="ops-metric-tile">
            <div className="ops-metric-tile-label">
              <ShieldCheck size={12} aria-hidden="true" /> Ingress Quota
            </div>
            <div className="ops-metric-tile-val">
              {data.subsystems.quotas.rate_limit_per_minute} <span style={{ fontSize: '13px', fontWeight: 500 }}>RPM</span>
            </div>
            <div className="ops-metric-tile-sub">
              {(data.subsystems.quotas.max_request_bytes / (1024 * 1024)).toFixed(0)} MB max payload
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
              <span className={`ops-pill pill-${data.subsystems.import.status}`}>
                {data.subsystems.import.status}
              </span>
            </div>
            <p className="ops-card-desc">
              Controls arXiv HTML paper fetching, payload size caps, and network boundaries.
            </p>
            <dl className="ops-dl">
              <div className="ops-dl-row">
                <dt>Allowed Hosts</dt>
                <dd>{data.subsystems.import.allowed_hosts.join(', ')}</dd>
              </div>
              <div className="ops-dl-row">
                <dt>Max HTML Size</dt>
                <dd>{(data.subsystems.import.max_html_bytes / (1024 * 1024)).toFixed(0)} MB</dd>
              </div>
              <div className="ops-dl-row">
                <dt>SSRF Boundary</dt>
                <dd style={{ color: '#059669' }}>Strict Allowlist</dd>
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
              <span className={`ops-pill pill-${data.subsystems.checker.status}`}>
                {data.subsystems.checker.status}
              </span>
            </div>
            <p className="ops-card-desc">
              Hard OS memory limits, CPU bounds, and execution deadlines for SymPy and Python workers.
            </p>
            <dl className="ops-dl">
              <div className="ops-dl-row">
                <dt>RAM Ceiling</dt>
                <dd>{data.subsystems.checker.ram_limit_mb} MB</dd>
              </div>
              <div className="ops-dl-row">
                <dt>Execution Deadline</dt>
                <dd>{(data.subsystems.checker.timeout_ms / 1000).toFixed(1)}s</dd>
              </div>
              <div className="ops-dl-row">
                <dt>CPU Cores Limit</dt>
                <dd>{data.subsystems.checker.cpu_cores} Core</dd>
              </div>
              <div className="ops-dl-row">
                <dt>Sandbox Engine</dt>
                <dd>{data.subsystems.checker.sandbox_available ? 'Docker Container' : 'Host Process'}</dd>
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
              <span className={`ops-pill pill-${data.subsystems.worker.status}`}>
                {data.subsystems.worker.status}
              </span>
            </div>
            <p className="ops-card-desc">
              Active job counters and automatic recovery of expired or crashed worker executions.
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
                  {data.subsystems.worker.active_queued}
                </div>
              </div>
              <div style={{ background: 'var(--accent)', padding: '6px', borderRadius: '6px' }}>
                <div style={{ fontSize: '10px', color: 'var(--muted-foreground)' }}>Running</div>
                <div style={{ fontSize: '13px', fontWeight: 700, fontFamily: 'var(--font-geist-mono)' }}>
                  {data.subsystems.worker.active_running}
                </div>
              </div>
              <div style={{ background: 'var(--accent)', padding: '6px', borderRadius: '6px' }}>
                <div style={{ fontSize: '10px', color: 'var(--muted-foreground)' }}>Finished</div>
                <div style={{ fontSize: '13px', fontWeight: 700, fontFamily: 'var(--font-geist-mono)' }}>
                  {data.subsystems.worker.finished_jobs}
                </div>
              </div>
              <div
                style={{
                  background: data.subsystems.worker.stuck_jobs > 0 ? '#ef444415' : 'var(--accent)',
                  padding: '6px',
                  borderRadius: '6px',
                  border: data.subsystems.worker.stuck_jobs > 0 ? '1px solid #ef444440' : 'none',
                }}
              >
                <div style={{ fontSize: '10px', color: data.subsystems.worker.stuck_jobs > 0 ? '#b91c1c' : 'var(--muted-foreground)' }}>
                  Stuck
                </div>
                <div
                  style={{
                    fontSize: '13px',
                    fontWeight: 700,
                    fontFamily: 'var(--font-geist-mono)',
                    color: data.subsystems.worker.stuck_jobs > 0 ? '#b91c1c' : 'inherit',
                  }}
                >
                  {data.subsystems.worker.stuck_jobs}
                </div>
              </div>
            </div>

            <button
              type="button"
              className="ops-recover-btn"
              onClick={() => { void handleRecover(); }}
              disabled={recovering}
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
              <span className={`ops-pill pill-${data.subsystems.quotas.status}`}>
                {data.subsystems.quotas.status}
              </span>
            </div>
            <p className="ops-card-desc">
              Protects API ingress with body payload ceilings, rate limits, and secret scrubbing.
            </p>
            <dl className="ops-dl">
              <div className="ops-dl-row">
                <dt>Ingress Body Limit</dt>
                <dd>{(data.subsystems.quotas.max_request_bytes / (1024 * 1024)).toFixed(0)} MB</dd>
              </div>
              <div className="ops-dl-row">
                <dt>Workspace Rate Cap</dt>
                <dd>{data.subsystems.quotas.rate_limit_per_minute} req / min</dd>
              </div>
              <div className="ops-dl-row">
                <dt>Log Sanitization</dt>
                <dd style={{ color: '#059669' }}>Active &bull; Redacted</dd>
              </div>
            </dl>
          </article>
        </div>
      )}
    </div>
  );
}
