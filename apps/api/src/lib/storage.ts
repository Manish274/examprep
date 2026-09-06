import { createHash } from "node:crypto";
import { mkdir, readFile, rename, rm, stat, writeFile } from "node:fs/promises";
import { dirname, resolve, sep } from "node:path";
import { env } from "../env.js";
import { fromRepoRoot } from "./paths.js";

/**
 * Where uploaded documents live. Callers only ever hold an opaque key, so
 * swapping the local filesystem for S3 later touches this file alone.
 *
 * The Python service has a mirror of this interface. They agree on the key
 * format -- `<userId>/<documentId>.<ext>` -- and nothing else.
 */
export interface StorageProvider {
  readonly name: string;
  write(key: string, data: Buffer): Promise<string>;
  read(key: string): Promise<Buffer>;
  delete(key: string): Promise<void>;
  exists(key: string): Promise<boolean>;
  localPath(key: string): string | null;
}

export class LocalFilesystemStorage implements StorageProvider {
  readonly name = "local";
  private readonly root: string;

  constructor(root: string) {
    // Anchored to the repo root, not the working directory: the RAG service
    // resolves the same configured path independently, and they must agree.
    this.root = fromRepoRoot(root);
  }

  private resolveKey(key: string): string {
    if (!key || key.startsWith("/") || key.includes("\\")) {
      throw new Error(`Invalid storage key: ${key}`);
    }
    const target = resolve(this.root, key);
    // A key reaches here from a database row, but treating it as trusted is
    // how a traversal gets in.
    if (target !== this.root && !target.startsWith(this.root + sep)) {
      throw new Error(`Storage key escapes root: ${key}`);
    }
    return target;
  }

  async write(key: string, data: Buffer): Promise<string> {
    const target = this.resolveKey(key);
    await mkdir(dirname(target), { recursive: true });

    // Write then rename, so a reader never observes a partial document.
    const temp = `${target}.${process.pid}.tmp`;
    await writeFile(temp, data);
    await rename(temp, target);
    return key;
  }

  read(key: string): Promise<Buffer> {
    return readFile(this.resolveKey(key));
  }

  async delete(key: string): Promise<void> {
    await rm(this.resolveKey(key), { force: true });
  }

  async exists(key: string): Promise<boolean> {
    try {
      const info = await stat(this.resolveKey(key));
      return info.isFile();
    } catch {
      return false;
    }
  }

  localPath(key: string): string {
    return this.resolveKey(key);
  }
}

let singleton: StorageProvider | null = null;

export function storage(): StorageProvider {
  singleton ??= new LocalFilesystemStorage(env().STORAGE_LOCAL_PATH);
  return singleton;
}

/** Key layout shared with the RAG service. */
export const storageKeyFor = (
  userId: string,
  documentId: string,
  extension: string,
): string => `${userId}/${documentId}.${extension}`;

export const sha256 = (data: Buffer): string =>
  createHash("sha256").update(data).digest("hex");
