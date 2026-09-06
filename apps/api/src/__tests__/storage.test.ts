import { mkdtemp, readdir, rm } from "node:fs/promises";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { afterEach, beforeEach, describe, expect, it } from "vitest";
import { LocalFilesystemStorage, sha256, storageKeyFor } from "../lib/storage.js";

let root: string;
let storage: LocalFilesystemStorage;

beforeEach(async () => {
  root = await mkdtemp(join(tmpdir(), "examprep-storage-"));
  storage = new LocalFilesystemStorage(root);
});

afterEach(async () => {
  await rm(root, { recursive: true, force: true });
});

describe("LocalFilesystemStorage", () => {
  it("round-trips content", async () => {
    await storage.write("user-1/doc.pdf", Buffer.from("%PDF-1.7"));
    expect((await storage.read("user-1/doc.pdf")).toString()).toBe("%PDF-1.7");
  });

  it("creates nested directories", async () => {
    await storage.write("a/b/c/deep.pdf", Buffer.from("x"));
    expect(await storage.exists("a/b/c/deep.pdf")).toBe(true);
  });

  it("reports absence rather than throwing", async () => {
    expect(await storage.exists("missing.pdf")).toBe(false);
  });

  it("overwrites without leaving the temp file behind", async () => {
    await storage.write("doc.pdf", Buffer.from("first"));
    await storage.write("doc.pdf", Buffer.from("second"));

    expect((await storage.read("doc.pdf")).toString()).toBe("second");
    // The write-then-rename must clean up after itself.
    expect((await readdir(root)).filter((f) => f.endsWith(".tmp"))).toHaveLength(0);
  });

  it("deletes idempotently", async () => {
    await storage.write("gone.pdf", Buffer.from("x"));
    await storage.delete("gone.pdf");
    // A retried job must not fail on a cleanup step that already succeeded.
    await expect(storage.delete("gone.pdf")).resolves.toBeUndefined();
  });

  describe("key validation", () => {
    // Keys come from database rows, but treating them as trusted is how a
    // traversal gets in.
    it.each([
      "../outside.pdf",
      "user-1/../../outside.pdf",
      "/absolute/path.pdf",
      "",
    ])("rejects %j", (key) => {
      expect(() => storage.localPath(key)).toThrow();
    });

    it("rejects backslashes", () => {
      // On Windows a backslash is a separator, so allowing it would let
      // "..\\.." past a check written for forward slashes only.
      expect(() => storage.localPath("user-1\\..\\..\\out.pdf")).toThrow();
    });

    it("blocks traversal on write too", async () => {
      await expect(
        storage.write("../escape.pdf", Buffer.from("x")),
      ).rejects.toThrow();
    });

    it("accepts ordinary nested keys", () => {
      expect(storage.localPath("users/abc/lecture-3.pdf")).toContain(
        "lecture-3.pdf",
      );
    });
  });
});

describe("storageKeyFor", () => {
  it("matches the layout the RAG service expects", () => {
    expect(storageKeyFor("user-1", "doc-2", "pdf")).toBe("user-1/doc-2.pdf");
  });
});

describe("sha256", () => {
  it("is stable for identical bytes", () => {
    expect(sha256(Buffer.from("abc"))).toBe(sha256(Buffer.from("abc")));
  });

  it("differs for different bytes", () => {
    expect(sha256(Buffer.from("abc"))).not.toBe(sha256(Buffer.from("abd")));
  });

  it("returns a full-length digest", () => {
    expect(sha256(Buffer.from("x"))).toHaveLength(64);
  });
});
