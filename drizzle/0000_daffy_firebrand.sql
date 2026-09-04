CREATE TABLE `hypotheses` (
	`id` text PRIMARY KEY NOT NULL,
	`workspace_id` text NOT NULL,
	`created_by` text NOT NULL,
	`title` text NOT NULL,
	`transformation_json` text NOT NULL,
	`status` text DEFAULT 'draft' NOT NULL,
	`created_at` integer NOT NULL,
	`updated_at` integer NOT NULL,
	FOREIGN KEY (`workspace_id`) REFERENCES `workspaces`(`id`) ON UPDATE no action ON DELETE cascade
);
--> statement-breakpoint
CREATE INDEX `idx_hypotheses_workspace_status` ON `hypotheses` (`workspace_id`,`status`);--> statement-breakpoint
CREATE TABLE `import_jobs` (
	`id` text PRIMARY KEY NOT NULL,
	`workspace_id` text NOT NULL,
	`paper_id` text,
	`requested_by` text NOT NULL,
	`canonical_url` text NOT NULL,
	`status` text DEFAULT 'queued' NOT NULL,
	`error_code` text,
	`created_at` integer NOT NULL,
	`finished_at` integer,
	FOREIGN KEY (`workspace_id`) REFERENCES `workspaces`(`id`) ON UPDATE no action ON DELETE cascade,
	FOREIGN KEY (`paper_id`) REFERENCES `papers`(`id`) ON UPDATE no action ON DELETE set null
);
--> statement-breakpoint
CREATE INDEX `idx_import_jobs_workspace_created` ON `import_jobs` (`workspace_id`,`created_at`);--> statement-breakpoint
CREATE INDEX `idx_import_jobs_status` ON `import_jobs` (`status`);--> statement-breakpoint
CREATE TABLE `papers` (
	`id` text PRIMARY KEY NOT NULL,
	`workspace_id` text NOT NULL,
	`arxiv_id` text NOT NULL,
	`version` integer,
	`title` text NOT NULL,
	`source_url` text NOT NULL,
	`source_sha256` text,
	`status` text DEFAULT 'queued' NOT NULL,
	`created_at` integer NOT NULL,
	`updated_at` integer NOT NULL,
	FOREIGN KEY (`workspace_id`) REFERENCES `workspaces`(`id`) ON UPDATE no action ON DELETE cascade
);
--> statement-breakpoint
CREATE UNIQUE INDEX `uq_papers_workspace_arxiv_version` ON `papers` (`workspace_id`,`arxiv_id`,`version`);--> statement-breakpoint
CREATE INDEX `idx_papers_workspace_status` ON `papers` (`workspace_id`,`status`);--> statement-breakpoint
CREATE TABLE `workspaces` (
	`id` text PRIMARY KEY NOT NULL,
	`owner_user_id` text NOT NULL,
	`name` text NOT NULL,
	`created_at` integer NOT NULL,
	`updated_at` integer NOT NULL
);
--> statement-breakpoint
CREATE INDEX `idx_workspaces_owner_user_id` ON `workspaces` (`owner_user_id`);