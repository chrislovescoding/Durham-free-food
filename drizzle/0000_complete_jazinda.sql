CREATE TABLE `event_suggestions` (
	`id` text PRIMARY KEY NOT NULL,
	`idempotency_key` text NOT NULL,
	`payload_hash` text NOT NULL,
	`title` text NOT NULL,
	`source_url` text NOT NULL,
	`details` text NOT NULL,
	`status` text DEFAULT 'pending' NOT NULL,
	`created_at` text NOT NULL
);
--> statement-breakpoint
CREATE UNIQUE INDEX `event_suggestions_idempotency_unique` ON `event_suggestions` (`idempotency_key`);--> statement-breakpoint
CREATE INDEX `event_suggestions_status_created` ON `event_suggestions` (`status`,`created_at`);--> statement-breakpoint
CREATE TABLE `suggestion_rate_limits` (
	`bucket` text PRIMARY KEY NOT NULL,
	`count` integer NOT NULL,
	`expires_at` integer NOT NULL
);
--> statement-breakpoint
CREATE INDEX `suggestion_rate_expiry` ON `suggestion_rate_limits` (`expires_at`);--> statement-breakpoint
CREATE TABLE `suggestion_salts` (
	`day` integer PRIMARY KEY NOT NULL,
	`salt` text NOT NULL,
	`expires_at` integer NOT NULL
);
