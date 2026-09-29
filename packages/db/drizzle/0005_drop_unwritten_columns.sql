ALTER TABLE "message_sources" DROP COLUMN "score";--> statement-breakpoint
ALTER TABLE "messages" DROP COLUMN "prompt_tokens";--> statement-breakpoint
ALTER TABLE "messages" DROP COLUMN "completion_tokens";