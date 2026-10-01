// @vitest-environment jsdom

import './setup';

import { cleanup, render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { afterEach, describe, expect, it, vi } from 'vitest';
import { OpsDashboard } from '../components/ops-dashboard';

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
      expect(screen.getByText('All Subsystems Operational')).toBeTruthy();
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
      expect(screen.getByText('All Subsystems Operational')).toBeTruthy();
      expect(screen.getByText('120 req / min')).toBeTruthy();
      expect(screen.getByText('256 MB')).toBeTruthy();
      expect(screen.getByText('3')).toBeTruthy(); // 1 queued + 2 running
    });
  });
});
