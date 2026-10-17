CREATE TABLE `submission_jobs` (
	`id` text PRIMARY KEY NOT NULL,
	`source_url` text NOT NULL,
	`status` text DEFAULT 'pending' NOT NULL,
	`reason` text,
	`events_json` text DEFAULT '[]' NOT NULL,
	`checkpoint` text DEFAULT '{}' NOT NULL,
	`lease_token` text,
	`lease_expires_at` integer,
	`started_day` integer,
	`attempts` integer DEFAULT 0 NOT NULL,
	`completion_hash` text,
	`created_at` text NOT NULL,
	`updated_at` text NOT NULL
);
--> statement-breakpoint
CREATE UNIQUE INDEX `submission_jobs_source_unique` ON `submission_jobs` (`source_url`);--> statement-breakpoint
CREATE INDEX `submission_jobs_queue` ON `submission_jobs` (`status`,`lease_expires_at`,`created_at`);--> statement-breakpoint
CREATE INDEX `submission_jobs_budget_day` ON `submission_jobs` (`started_day`);--> statement-breakpoint
ALTER TABLE `event_suggestions` ADD `job_id` text;--> statement-breakpoint
ALTER TABLE `event_suggestions` ADD `reason` text;