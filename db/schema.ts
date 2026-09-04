import { index, integer, sqliteTable, text, uniqueIndex } from 'drizzle-orm/sqlite-core';

export const workspaces = sqliteTable(
  'workspaces',
  {
    id: text('id').primaryKey(),
    ownerUserId: text('owner_user_id').notNull(),
    name: text('name').notNull(),
    createdAt: integer('created_at', { mode: 'timestamp_ms' }).notNull(),
    updatedAt: integer('updated_at', { mode: 'timestamp_ms' }).notNull(),
  },
  (table) => [
    index('idx_workspaces_owner_user_id').on(table.ownerUserId),
  ],
);

export const papers = sqliteTable(
  'papers',
  {
    id: text('id').primaryKey(),
    workspaceId: text('workspace_id')
      .notNull()
      .references(() => workspaces.id, { onDelete: 'cascade' }),
    arxivId: text('arxiv_id').notNull(),
    version: integer('version'),
    title: text('title').notNull(),
    sourceUrl: text('source_url').notNull(),
    sourceSha256: text('source_sha256'),
    status: text('status', {
      enum: ['queued', 'extracting', 'ready', 'failed'],
    })
      .notNull()
      .default('queued'),
    createdAt: integer('created_at', { mode: 'timestamp_ms' }).notNull(),
    updatedAt: integer('updated_at', { mode: 'timestamp_ms' }).notNull(),
  },
  (table) => [
    uniqueIndex('uq_papers_workspace_arxiv_version').on(
      table.workspaceId,
      table.arxivId,
      table.version,
    ),
    index('idx_papers_workspace_status').on(table.workspaceId, table.status),
  ],
);

export const importJobs = sqliteTable(
  'import_jobs',
  {
    id: text('id').primaryKey(),
    workspaceId: text('workspace_id')
      .notNull()
      .references(() => workspaces.id, { onDelete: 'cascade' }),
    paperId: text('paper_id').references(() => papers.id, {
      onDelete: 'set null',
    }),
    requestedBy: text('requested_by').notNull(),
    canonicalUrl: text('canonical_url').notNull(),
    status: text('status', {
      enum: ['queued', 'running', 'succeeded', 'failed'],
    })
      .notNull()
      .default('queued'),
    errorCode: text('error_code'),
    createdAt: integer('created_at', { mode: 'timestamp_ms' }).notNull(),
    finishedAt: integer('finished_at', { mode: 'timestamp_ms' }),
  },
  (table) => [
    index('idx_import_jobs_workspace_created').on(
      table.workspaceId,
      table.createdAt,
    ),
    index('idx_import_jobs_status').on(table.status),
  ],
);

export const hypotheses = sqliteTable(
  'hypotheses',
  {
    id: text('id').primaryKey(),
    workspaceId: text('workspace_id')
      .notNull()
      .references(() => workspaces.id, { onDelete: 'cascade' }),
    createdBy: text('created_by').notNull(),
    title: text('title').notNull(),
    transformationJson: text('transformation_json').notNull(),
    status: text('status', {
      enum: [
        'draft',
        'invalid',
        'well_typed',
        'numerically_plausible',
        'symbolically_verified',
        'human_reviewed',
      ],
    })
      .notNull()
      .default('draft'),
    createdAt: integer('created_at', { mode: 'timestamp_ms' }).notNull(),
    updatedAt: integer('updated_at', { mode: 'timestamp_ms' }).notNull(),
  },
  (table) => [
    index('idx_hypotheses_workspace_status').on(
      table.workspaceId,
      table.status,
    ),
  ],
);
