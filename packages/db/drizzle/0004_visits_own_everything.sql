-- Traces orphaned by visits that ended while the link was "set null". They
-- hold those visitors' questions and would otherwise never be removed.
DELETE FROM "traces" WHERE "user_id" IS NULL;--> statement-breakpoint
ALTER TABLE "traces" DROP CONSTRAINT "traces_user_id_users_id_fk";
--> statement-breakpoint
DROP INDEX "users_email_unique";--> statement-breakpoint
ALTER TABLE "chat_sessions" ALTER COLUMN "login_session_id" SET NOT NULL;--> statement-breakpoint
ALTER TABLE "documents" ALTER COLUMN "login_session_id" SET NOT NULL;--> statement-breakpoint
ALTER TABLE "flashcard_sets" ALTER COLUMN "login_session_id" SET NOT NULL;--> statement-breakpoint
ALTER TABLE "refresh_tokens" ALTER COLUMN "login_session_id" SET NOT NULL;--> statement-breakpoint
ALTER TABLE "tests" ALTER COLUMN "login_session_id" SET NOT NULL;--> statement-breakpoint
ALTER TABLE "traces" ADD CONSTRAINT "traces_user_id_users_id_fk" FOREIGN KEY ("user_id") REFERENCES "public"."users"("id") ON DELETE cascade ON UPDATE no action;--> statement-breakpoint
ALTER TABLE "users" DROP COLUMN "email";--> statement-breakpoint
ALTER TABLE "users" DROP COLUMN "password_hash";