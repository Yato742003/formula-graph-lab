// @vitest-environment jsdom

import './setup';

import { cleanup, render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { afterEach, describe, expect, it, vi } from 'vitest';
import { OpsDashboard, type DeadLetterJob } from '../components/ops-dashboard';

function toRequestUrl(input: RequestInfo | URL): string {
  return typeof input === 'string' ? input : input instanceof URL ? input.toString() : input.url;
}

function parseJsonBody(body: BodyInit | null | undefined): Record<string, unknown> {
  if (typeof body !== 'string') {
    throw new Error('Expected JSON string request body');
  }
  return JSON.parse(body) as Record<string, unknown>;
}

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
  vi.clearAllMocks();
});

const mockDashboardData = {
  status: 'ok',
  workspace_id: 'ws_test_123',
  actor_id: 'user-1',
  checked_at: '2026-10-01T12:00:00Z',
  subsystems: {
    import: {
      status: 'healthy',
      allowed_hosts: ['arxiv.org', 'export.arxiv.org'],
      max_html_bytes: 10485760,
    },
    checker: {
      status: 'healthy',
      ram_limit_mb: 256,
      timeout_ms: 10000,
      cpu_cores: 1,
      sandbox_available: true,
    },
    worker: {
      status: 'healthy',
      active_queued: 2,
      active_running: 1,
      finished_jobs: 14,
      failed_jobs: 0,
      stuck_jobs: 0,
    },
    quotas: {
      status: 'healthy',
      max_request_bytes: 2097152,
      rate_limit_per_minute: 120,
    },
  },
};

describe('OpsDashboard', () => {
  it('renders all four subsystem cards with telemetry data', async () => {
    const fetcher = vi.fn(async () => Response.json(mockDashboardData));
    vi.stubGlobal('fetch', fetcher);

    render(<OpsDashboard />);

    expect(screen.getByRole('heading', { name: 'Operations & Health Dashboard' })).toBeTruthy();

    await waitFor(() => {
      expect(screen.getByText('Workspace queue telemetry available')).toBeTruthy();
      expect(screen.getByText('Import Guard')).toBeTruthy();
      expect(screen.getByText('Checker Execution Bounds')).toBeTruthy();
      expect(screen.getByText('Job Queue & Recovery')).toBeTruthy();
      expect(screen.getByText('Quotas & Sanitization')).toBeTruthy();
      expect(screen.getByText('256 MB')).toBeTruthy();
      expect(screen.getByText('10.0s')).toBeTruthy();
      expect(screen.getByText('Docker Container')).toBeTruthy();
    });
  });

  it('triggers stuck job sweep and displays recovery summary', async () => {
    const user = userEvent.setup();
    const fetcher = vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
      const url = typeof input === 'string' ? input : input instanceof URL ? input.toString() : input.url;
      if (url.includes('/api/research/ops/jobs/recover') && init?.method === 'POST') {
        return Response.json({
          status: 'ok',
          recovered_count: 3,
          recovered_job_ids: ['job-1', 'job-2', 'job-3'],
          recovered_at: '2026-10-01T12:05:00Z',
        });
      }
      return Response.json(mockDashboardData);
    });
    vi.stubGlobal('fetch', fetcher);

    render(<OpsDashboard />);

    await waitFor(() => {
      expect(screen.getByText('Job Queue & Recovery')).toBeTruthy();
    });

    const sweepButton = screen.getByRole('button', { name: /Sweep & Recover Stuck Jobs/i });
    await user.click(sweepButton);

    await waitFor(() => {
      expect(screen.getByRole('status').textContent).toMatch(/Recovered 3 stuck or expired job/i);
    });

    const recoverCall = fetcher.mock.calls.find((c) => {
      const raw = c[0];
      const callUrl = typeof raw === 'string' ? raw : raw instanceof URL ? raw.toString() : raw.url;
      return callUrl.includes('/recover');
    });
    expect(recoverCall).toBeTruthy();
    expect(recoverCall![1]?.method).toBe('POST');
  });

  it('safely handles backend response with top-level quotas and nested metrics without throwing', async () => {
    const rawBackendData = {
      status: 'ok',
      checked_at: '2026-10-01T12:00:00Z',
      subsystems: {
        import: { status: 'ok', max_body_bytes: 10485760, allowed_hosts: ['arxiv.org'] },
        checker: { status: 'ok', max_ram_bytes: 268435456, max_timeout_ms: 10000, cpu_cores: 1 },
        worker: { status: 'ok', sandbox_image: 'none', metrics: { active_queued: 1, active_running: 2, stuck: 0 } },
      },
      quotas: {
        request_body_cap_bytes: 2097152,
        workspace_rate_limit_rpm: 120,
      },
    };
    const fetcher = vi.fn(async () => Response.json(rawBackendData));
    vi.stubGlobal('fetch', fetcher);

    render(<OpsDashboard />);

    await waitFor(() => {
      expect(screen.getByText('Workspace queue telemetry available')).toBeTruthy();
      expect(screen.getByText('120 req / min')).toBeTruthy();
      expect(screen.getByText('256 MB')).toBeTruthy();
      expect(screen.getByText('3')).toBeTruthy(); // 1 queued + 2 running
    });
  });

  it('does not report healthy telemetry when the request fails or fields are missing', async () => {
    const fetcher = vi.fn(async () => new Response('{}', { status: 503 }));
    vi.stubGlobal('fetch', fetcher);
    render(<OpsDashboard />);
    expect(await screen.findByRole('alert')).toBeTruthy();
    expect(screen.getByText('Operations telemetry unavailable')).toBeTruthy();
    expect(screen.queryByText('All Subsystems Operational')).toBeNull();
    cleanup();
    fetcher.mockImplementation(async () => Response.json({ status: 'ok', checked_at: '2026-10-01', subsystems: {} }));
    render(<OpsDashboard />);
    await waitFor(() => expect(screen.getByText('Operations telemetry unavailable')).toBeTruthy());
    expect(screen.getByRole('button', { name: /Sweep & Recover/ }).hasAttribute('disabled')).toBe(true);
  });

  it('rejects malformed telemetry rather than crashing while rendering', async () => {
    vi.stubGlobal('fetch', vi.fn(async () => Response.json({ ...mockDashboardData,
      subsystems: { ...mockDashboardData.subsystems, import: { allowed_hosts: 'not-an-array' } },
    })));
    render(<OpsDashboard />);
    expect((await screen.findByRole('alert')).textContent).toContain('host policy is invalid');
    expect(screen.getByText('Operations telemetry unavailable')).toBeTruthy();
  });

  it('renders dead-letter rows with retry enabled only for queued jobs', async () => {
    const queuedJobId = '11111111-1111-4111-8111-111111111111';
    const runningJobId = '22222222-2222-4222-8222-222222222222';
    const fetcher = vi.fn(async () => Response.json({
      ...mockDashboardData,
      subsystems: {
        ...mockDashboardData.subsystems,
        worker: {
          ...mockDashboardData.subsystems.worker,
          metrics: {
            dead_letter_pending: 2,
            dead_letter_quarantined: 0,
          },
        },
      },
      dead_letters: [
        {
          job_id: queuedJobId,
          operation: 'research_case_verify',
          target_uuid: 'aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa',
          prior_state: 'queued',
          failure_reason: 'worker_lease_expired_before_start',
          reconciliation_status: 'pending',
          reserved_ms: 4500,
          reservation_retained: true,
          retry_eligible: true,
          authorized_retry_key: null,
          reconciled_receipt_id: null,
          recovered_at: 1730000000,
          reconciled_at: null,
          reconciled_by: null,
        },
        {
          job_id: runningJobId,
          operation: 'sandbox_eval',
          target_uuid: 'bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb',
          prior_state: 'running',
          failure_reason: 'heartbeat_timeout',
          reconciliation_status: 'pending',
          reserved_ms: 9200,
          reservation_retained: true,
          retry_eligible: false,
          authorized_retry_key: null,
          reconciled_receipt_id: null,
          recovered_at: 1730000100,
          reconciled_at: null,
          reconciled_by: null,
        },
      ],
    }));
    vi.stubGlobal('fetch', fetcher);

    render(<OpsDashboard />);

    await waitFor(() => {
      expect(screen.getByText('Dead-Letter & Reconciliation Ledger')).toBeTruthy();
      expect(screen.getByText(queuedJobId)).toBeTruthy();
      expect(screen.getByText(queuedJobId).getAttribute('title')).toBe(queuedJobId);
      expect(screen.getByText(runningJobId)).toBeTruthy();
      expect(screen.getByText('4500 ms')).toBeTruthy();
      expect(screen.getByText('9200 ms')).toBeTruthy();
      expect(screen.getByText('queued')).toBeTruthy();
      expect(screen.getByText('running · uncertain')).toBeTruthy();
    });

    const ledgerTable = screen.getByRole('table');
    expect(ledgerTable.style.tableLayout).toBe('fixed');
    expect(ledgerTable.querySelectorAll('colgroup col')).toHaveLength(6);

    const retryButtons = screen.getAllByRole('button', { name: 'Authorize Retry' });
    expect(retryButtons).toHaveLength(2);
    expect(retryButtons[0].hasAttribute('disabled')).toBe(false);
    expect(retryButtons[1].hasAttribute('disabled')).toBe(true);
    expect(retryButtons[1].getAttribute('title')).toBe(
      'Uncertain jobs that reached running state cannot be retried',
    );
  });

  it('requires rationale >= 3 chars and reconciles queued and running dead-letter jobs', async () => {
    const user = userEvent.setup();
    const queuedJobId = '11111111-1111-4111-8111-111111111111';
    const runningJobId = '22222222-2222-4222-8222-222222222222';

    let currentDeadLetters: DeadLetterJob[] = [
      {
        job_id: queuedJobId,
        operation: 'research_case_verify',
        target_uuid: 'aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa',
        prior_state: 'queued',
        failure_reason: 'lease_expired',
        reconciliation_status: 'pending',
        reserved_ms: 4500,
        reservation_retained: true,
        retry_eligible: true,
        authorized_retry_key: null,
        reconciled_receipt_id: null,
        recovered_at: 1730000000,
        reconciled_at: null,
        reconciled_by: null,
      },
      {
        job_id: runningJobId,
        operation: 'sandbox_eval',
        target_uuid: 'bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb',
        prior_state: 'running',
        failure_reason: 'heartbeat_timeout',
        reconciliation_status: 'pending',
        reserved_ms: 9200,
        reservation_retained: true,
        retry_eligible: false,
        authorized_retry_key: null,
        reconciled_receipt_id: null,
        recovered_at: 1730000100,
        reconciled_at: null,
        reconciled_by: null,
      },
    ];

    const fetcher = vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
      const url = toRequestUrl(input);
      if (url.includes(`/api/research/ops/jobs/${queuedJobId}/reconcile`) && init?.method === 'POST') {
        const body = parseJsonBody(init.body);
        currentDeadLetters = currentDeadLetters.map((item) =>
          item.job_id === queuedJobId
            ? {
                ...item,
                reconciliation_status: 'retry_authorized' as const,
                authorized_retry_key: 'retry-key-001',
                reconciled_at: '2026-10-01T12:10:00Z',
                reconciled_by: 'operator-1',
              }
            : item,
        );
        return Response.json({
          reconciliation_id: 'rec-1',
          job_id: queuedJobId,
          workspace_id: 'ws_test_123',
          operation: 'research_case_verify',
          target_uuid: 'aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa',
          prior_state: 'queued',
          decision: body.decision,
          reconciliation_status: 'retry_authorized',
          reserved_ms: 4500,
          reservation_retained: true,
          receipt_found: false,
          reconciled_receipt_id: null,
          authorized_retry_key: 'retry-key-001',
          reconciled_by: 'operator-1',
          reconciled_at: '2026-10-01T12:10:00Z',
          replayed: false,
        });
      }
      if (url.includes(`/api/research/ops/jobs/${runningJobId}/reconcile`) && init?.method === 'POST') {
        const body = parseJsonBody(init.body);
        currentDeadLetters = currentDeadLetters.map((item) =>
          item.job_id === runningJobId
            ? {
                ...item,
                reconciliation_status: 'reconciled_receipt' as const,
                reconciled_receipt_id: 'rcpt-999',
                reconciled_at: '2026-10-01T12:11:00Z',
                reconciled_by: 'operator-1',
              }
            : item,
        );
        return Response.json({
          reconciliation_id: 'rec-2',
          job_id: runningJobId,
          workspace_id: 'ws_test_123',
          operation: 'sandbox_eval',
          target_uuid: 'bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb',
          prior_state: 'running',
          decision: body.decision,
          reconciliation_status: 'reconciled_receipt',
          reserved_ms: 9200,
          reservation_retained: true,
          receipt_found: true,
          reconciled_receipt_id: 'rcpt-999',
          authorized_retry_key: null,
          reconciled_by: 'operator-1',
          reconciled_at: '2026-10-01T12:11:00Z',
          replayed: false,
        });
      }
      return Response.json({
        ...mockDashboardData,
        dead_letters: currentDeadLetters,
      });
    });
    vi.stubGlobal('fetch', fetcher);

    render(<OpsDashboard />);

    await waitFor(() => {
      expect(screen.getByText(queuedJobId)).toBeTruthy();
    });

    const queuedRationale = screen.getByLabelText(`Reconciliation rationale for ${queuedJobId}`);
    await user.type(queuedRationale, 'no');
    const retryButtons = screen.getAllByRole('button', { name: 'Authorize Retry' });
    await user.click(retryButtons[0]);

    expect(screen.getByText(/at least 3 characters/i)).toBeTruthy();
    expect(fetcher.mock.calls.some((c) => toRequestUrl(c[0]).includes('/reconcile'))).toBe(false);

    await user.clear(queuedRationale);
    await user.type(queuedRationale, 'Confirmed queued before worker lease drop');
    await user.click(retryButtons[0]);

    await waitFor(() => {
      expect(screen.getAllByText('retry_authorized').length).toBeGreaterThanOrEqual(2);
      expect(screen.getAllByText(/retry-key-001/).length).toBeGreaterThanOrEqual(1);
    });

    const queuedPost = fetcher.mock.calls.find((c) =>
      toRequestUrl(c[0]).includes(`/api/research/ops/jobs/${queuedJobId}/reconcile`),
    );
    expect(queuedPost).toBeTruthy();
    expect(queuedPost![1]?.method).toBe('POST');
    const queuedHeaders = queuedPost![1]?.headers as Record<string, string>;
    expect(queuedHeaders['x-idempotency-key']).toMatch(/^[\x20-\x7E]{8,128}$/);
    expect(parseJsonBody(queuedPost![1]?.body)).toEqual({
      decision: 'authorize_retry',
      notes: 'Confirmed queued before worker lease drop',
    });

    const runningRationale = screen.getByLabelText(`Reconciliation rationale for ${runningJobId}`);
    await user.type(runningRationale, 'Verify whether durable receipt committed');
    const checkReceiptButtons = screen.getAllByRole('button', { name: 'Check Receipt' });
    await user.click(checkReceiptButtons[0]);

    await waitFor(() => {
      expect(screen.getAllByText('reconciled_receipt').length).toBeGreaterThanOrEqual(2);
      expect(screen.getAllByText(/rcpt-999/).length).toBeGreaterThanOrEqual(1);
    });

    const runningPost = fetcher.mock.calls.find((c) =>
      toRequestUrl(c[0]).includes(`/api/research/ops/jobs/${runningJobId}/reconcile`),
    );
    expect(runningPost).toBeTruthy();
    const runningHeaders = runningPost![1]?.headers as Record<string, string>;
    expect(runningHeaders['x-idempotency-key']).toMatch(/^[\x20-\x7E]{8,128}$/);
    expect(parseJsonBody(runningPost![1]?.body)).toEqual({
      decision: 'reconcile_receipt',
      notes: 'Verify whether durable receipt committed',
    });
  });

  it('rejects malformed dead_letters payloads fail-closed', async () => {
    vi.stubGlobal(
      'fetch',
      vi.fn(async () =>
        Response.json({
          ...mockDashboardData,
          dead_letters: [
            {
              job_id: '11111111-1111-4111-8111-111111111111',
              operation: 'sandbox_eval',
              target_uuid: 'aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa',
              prior_state: 'invalid_state',
              failure_reason: null,
              reconciliation_status: 'pending',
              reserved_ms: -5,
              reservation_retained: true,
              retry_eligible: false,
              authorized_retry_key: null,
              reconciled_receipt_id: null,
              recovered_at: null,
              reconciled_at: null,
              reconciled_by: null,
            },
          ],
        }),
      ),
    );

    render(<OpsDashboard />);
    expect((await screen.findByRole('alert')).textContent).toContain('dead-letter ledger is invalid');
    expect(screen.getByText('Operations telemetry unavailable')).toBeTruthy();
  });

  it('renders Workspace HTTP Latency P50/P95/P99 telemetry, route breakdown, and live BFF RTT header', async () => {
    const populatedPayload = {
      ...mockDashboardData,
      http_telemetry: {
        scope: 'process_memory_window',
        window_seconds: 900,
        max_samples_per_workspace: 2000,
        sample_count: 42,
        request_count: 42,
        success_count: 40,
        error_4xx_count: 1,
        error_5xx_count: 1,
        error_rate_pct: 4.76,
        error_free_rate_pct: 95.24,
        p50_ms: 18.4,
        p95_ms: 82.6,
        p99_ms: 141.2,
        max_ms: 165.0,
        routes: [
          {
            method: 'POST',
            route: '/v1/checks/symbolic/quick',
            request_count: 12,
            error_count: 1,
            error_rate_pct: 8.33,
            p50_ms: 42.0,
            p95_ms: 112.5,
            p99_ms: 141.2,
            max_ms: 165.0,
          },
          {
            method: 'GET',
            route: '/v1/research/ops/dashboard',
            request_count: 30,
            error_count: 1,
            error_rate_pct: 3.33,
            p50_ms: 11.2,
            p95_ms: 28.4,
            p99_ms: 35.1,
            max_ms: 40.0,
          },
        ],
      },
    };
    vi.stubGlobal(
      'fetch',
      vi.fn(async () =>
        Response.json(populatedPayload, {
          headers: { 'x-bff-duration-ms': '24.7' },
        }),
      ),
    );

    render(<OpsDashboard />);

    await waitFor(() => {
      expect(screen.getByText(/Workspace HTTP Latency & Reliability/i)).toBeTruthy();
      expect(screen.getByText('18.4 ms')).toBeTruthy();
      expect(screen.getByText('82.6 ms')).toBeTruthy();
      expect(screen.getAllByText('141.2 ms').length).toBeGreaterThanOrEqual(2);
      expect(screen.getByText('95.2%')).toBeTruthy();
      expect(screen.getByText('1 / 1')).toBeTruthy();
      expect(screen.getAllByText('24.7 ms').length).toBeGreaterThanOrEqual(1);
      expect(screen.getByText('/v1/checks/symbolic/quick')).toBeTruthy();
      expect(screen.getByText('/v1/research/ops/dashboard')).toBeTruthy();
    });

    const routeTable = screen.getByRole('table', { name: 'Workspace HTTP route latency breakdown' });
    expect(routeTable.style.tableLayout).toBe('fixed');
    expect(routeTable.querySelectorAll('colgroup col')).toHaveLength(7);
  });

  it('renders an honest empty latency state when sample_count is 0 instead of fake zeros', async () => {
    vi.stubGlobal(
      'fetch',
      vi.fn(async () =>
        Response.json({
          ...mockDashboardData,
          http_telemetry: {
            scope: 'process_memory_window',
            window_seconds: 900,
            max_samples_per_workspace: 2000,
            sample_count: 0,
            request_count: 0,
            success_count: 0,
            error_4xx_count: 0,
            error_5xx_count: 0,
            error_rate_pct: null,
            error_free_rate_pct: null,
            p50_ms: null,
            p95_ms: null,
            p99_ms: null,
            max_ms: null,
            routes: [],
          },
        }),
      ),
    );

    render(<OpsDashboard />);

    await waitFor(() => {
      expect(
        screen.getByText(/No workspace requests recorded in the active 15-minute window yet/i),
      ).toBeTruthy();
    });
    expect(screen.queryByRole('table', { name: 'Workspace HTTP route latency breakdown' })).toBeNull();
  });

  it('rejects malformed http_telemetry payloads fail-closed', async () => {
    vi.stubGlobal(
      'fetch',
      vi.fn(async () =>
        Response.json({
          ...mockDashboardData,
          http_telemetry: {
            scope: 'invalid_scope',
            p50_ms: -10,
          },
        }),
      ),
    );

    render(<OpsDashboard />);
    expect((await screen.findByRole('alert')).textContent).toContain('HTTP latency telemetry is invalid');
    expect(screen.getByText('Operations telemetry unavailable')).toBeTruthy();
  });
});
