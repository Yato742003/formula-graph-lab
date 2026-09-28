import { describe, expect, it } from 'vitest';
import {
  D1ImportRepository,
  ImportWorkflowError,
} from '../lib/server/paper-import';

const successfulResult = {
  success: true,
  meta: { changes: 1 },
  results: [],
};

class RecordingStatement {
  values: unknown[] = [];

  constructor(
    readonly sql: string,
    private readonly database: RecordingDatabase,
  ) {}

  bind(...values: unknown[]) {
    this.values = values;
    return this;
  }

  async run() {
    return successfulResult;
  }

  async first<T>() {
    return (this.database.workspaceOwned ? { id: 'ws_owned' } : null) as T | null;
  }
}

class RecordingDatabase {
  readonly statements: RecordingStatement[] = [];

  constructor(readonly workspaceOwned = true) {}

  prepare(sql: string) {
    const statement = new RecordingStatement(sql, this);
    this.statements.push(statement);
    return statement;
  }

  async batch(statements: RecordingStatement[]) {
    return statements.map(() => successfulResult);
  }
}

function createRepository(database = new RecordingDatabase()) {
  return {
    database,
    repository: new D1ImportRepository(database as unknown as D1Database),
  };
}

describe('D1 import repository tenant guards', () => {
  it('rejects a deterministic workspace collision owned by another user', async () => {
    const { repository } = createRepository(new RecordingDatabase(false));

    await expect(
      repository.ensureWorkspace({
        id: 'ws_collision',
        ownerUserId: 'user-a',
        now: 1,
      }),
    ).rejects.toEqual(
      expect.objectContaining<Partial<ImportWorkflowError>>({
        code: 'WORKSPACE_OWNERSHIP_CONFLICT',
        status: 409,
      }),
    );
  });

  it('scopes job creation, paper persistence and status updates to the owner', async () => {
    const { database, repository: d1 } = createRepository();
    await d1.ensureWorkspace({
      id: 'ws_owned',
      ownerUserId: 'user-a',
      now: 1,
    });
    await d1.createJob({
      id: 'job-1',
      workspaceId: 'ws_owned',
      requestedBy: 'user-a',
      canonicalUrl: 'https://arxiv.org/html/2402.08954',
      now: 1,
    });
    await d1.markSucceeded({
      jobId: 'job-1',
      requestedBy: 'user-a',
      paper: {
        id: 'paper-1',
        workspaceId: 'ws_owned',
        arxivId: '2402.08954',
        version: 1,
        title: 'Paper',
        sourceUrl: 'https://arxiv.org/html/2402.08954v1',
        sourceSha256: 'a'.repeat(64),
        now: 2,
      },
    });
    await d1.markFailed({
      jobId: 'job-2',
      workspaceId: 'ws_owned',
      requestedBy: 'user-a',
      errorCode: 'GRAPH_API_UNAVAILABLE',
      now: 3,
    });

    const sql = database.statements.map((statement) => statement.sql);
    const ownershipReads = sql.filter((query) => query.includes('SELECT id'));
    const guardedWrites = sql.filter(
      (query) =>
        query.includes('INSERT INTO import_jobs') ||
        query.includes('INSERT INTO papers') ||
        query.includes('UPDATE import_jobs'),
    );
    expect(ownershipReads).toHaveLength(1);
    expect(ownershipReads[0]).toContain('owner_user_id = ?');
    expect(guardedWrites).toHaveLength(4);
    expect(guardedWrites[0]).toContain('owner_user_id = ?');
    expect(guardedWrites[1]).toContain('owner_user_id = ?');
    expect(guardedWrites[2]).toContain('requested_by = ?');
    expect(guardedWrites[3]).toContain('requested_by = ?');
  });
});
