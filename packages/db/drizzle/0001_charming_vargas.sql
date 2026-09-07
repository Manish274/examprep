CREATE TYPE "public"."content_source" AS ENUM('text', 'vision');--> statement-breakpoint
ALTER TABLE "document_chunks" ADD COLUMN "source" "content_source" DEFAULT 'text' NOT NULL;