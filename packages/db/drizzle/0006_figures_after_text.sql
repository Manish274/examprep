CREATE TABLE "vision_cache" (
	"model_key" varchar(160) NOT NULL,
	"content_hash" varchar(64) NOT NULL,
	"reading" text,
	"created_at" timestamp with time zone DEFAULT now() NOT NULL,
	CONSTRAINT "vision_cache_model_key_content_hash_pk" PRIMARY KEY("model_key","content_hash")
);
--> statement-breakpoint
ALTER TABLE "documents" ADD COLUMN "figures_pending" integer;