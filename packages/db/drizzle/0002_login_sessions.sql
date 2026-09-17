CREATE TABLE "login_sessions" (
	"id" uuid PRIMARY KEY DEFAULT gen_random_uuid() NOT NULL,
	"user_id" uuid NOT NULL,
	"ended_at" timestamp with time zone,
	"created_at" timestamp with time zone DEFAULT now() NOT NULL
);
--> statement-breakpoint
DROP INDEX "documents_user_hash_unique";--> statement-breakpoint
ALTER TABLE "chat_sessions" ADD COLUMN "login_session_id" uuid;--> statement-breakpoint
ALTER TABLE "documents" ADD COLUMN "login_session_id" uuid;--> statement-breakpoint
ALTER TABLE "flashcard_sets" ADD COLUMN "login_session_id" uuid;--> statement-breakpoint
ALTER TABLE "refresh_tokens" ADD COLUMN "login_session_id" uuid;--> statement-breakpoint
ALTER TABLE "tests" ADD COLUMN "login_session_id" uuid;--> statement-breakpoint
ALTER TABLE "login_sessions" ADD CONSTRAINT "login_sessions_user_id_users_id_fk" FOREIGN KEY ("user_id") REFERENCES "public"."users"("id") ON DELETE cascade ON UPDATE no action;--> statement-breakpoint
CREATE INDEX "login_sessions_user_idx" ON "login_sessions" USING btree ("user_id");--> statement-breakpoint
ALTER TABLE "chat_sessions" ADD CONSTRAINT "chat_sessions_login_session_id_login_sessions_id_fk" FOREIGN KEY ("login_session_id") REFERENCES "public"."login_sessions"("id") ON DELETE cascade ON UPDATE no action;--> statement-breakpoint
ALTER TABLE "documents" ADD CONSTRAINT "documents_login_session_id_login_sessions_id_fk" FOREIGN KEY ("login_session_id") REFERENCES "public"."login_sessions"("id") ON DELETE cascade ON UPDATE no action;--> statement-breakpoint
ALTER TABLE "flashcard_sets" ADD CONSTRAINT "flashcard_sets_login_session_id_login_sessions_id_fk" FOREIGN KEY ("login_session_id") REFERENCES "public"."login_sessions"("id") ON DELETE cascade ON UPDATE no action;--> statement-breakpoint
ALTER TABLE "refresh_tokens" ADD CONSTRAINT "refresh_tokens_login_session_id_login_sessions_id_fk" FOREIGN KEY ("login_session_id") REFERENCES "public"."login_sessions"("id") ON DELETE cascade ON UPDATE no action;--> statement-breakpoint
ALTER TABLE "tests" ADD CONSTRAINT "tests_login_session_id_login_sessions_id_fk" FOREIGN KEY ("login_session_id") REFERENCES "public"."login_sessions"("id") ON DELETE cascade ON UPDATE no action;--> statement-breakpoint
CREATE INDEX "chat_sessions_login_session_idx" ON "chat_sessions" USING btree ("login_session_id");--> statement-breakpoint
CREATE INDEX "documents_login_session_idx" ON "documents" USING btree ("login_session_id");--> statement-breakpoint
CREATE UNIQUE INDEX "documents_session_hash_unique" ON "documents" USING btree ("login_session_id","content_hash");--> statement-breakpoint
CREATE INDEX "flashcard_sets_login_session_idx" ON "flashcard_sets" USING btree ("login_session_id");--> statement-breakpoint
CREATE INDEX "tests_login_session_idx" ON "tests" USING btree ("login_session_id");