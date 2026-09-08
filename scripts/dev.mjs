#!/usr/bin/env node
/**
 * Brings the whole stack up with one command, for the console to talk to.
 *
 *     npm run dev
 *
 * Four moving parts have to be running before a question can be answered: the
 * containers, the Python RAG service, the Node API and the worker. Starting
 * them by hand in four terminals is the sort of thing that silently ends with
 * three of them running and a confusing error in the fourth.
 *
 * Written with no dependencies on purpose. A process runner is a small thing to
 * add and a large thing to explain when it behaves differently on someone
 * else's machine.
 */

import { spawn, spawnSync } from "node:child_process";
import { existsSync } from "node:fs";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";

const ROOT = join(dirname(fileURLToPath(import.meta.url)), "..");
const API_PORT = process.env.API_PORT || "3001";
const RAG_PORT = process.env.RAG_SERVICE_PORT || "8000";
const WEB_PORT = process.env.WEB_PORT || "3000";

const COLOURS = {
  rag: "\x1b[35m",
  api: "\x1b[36m",
  worker: "\x1b[33m",
  web: "\x1b[32m",
};
const DIM = "\x1b[2m";
const RESET = "\x1b[0m";

const children = [];
let shuttingDown = false;

function log(name, line) {
  const colour = COLOURS[name] || "";
  process.stdout.write(`${colour}${name.padEnd(6)}${RESET} ${DIM}|${RESET} ${line}\n`);
}

/** The interpreter inside the service's virtualenv, whatever the platform
 *  called the directory. */
function pythonPath() {
  for (const candidate of [
    join(ROOT, "services", "rag", ".venv", "Scripts", "python.exe"),
    join(ROOT, "services", "rag", ".venv", "bin", "python"),
  ]) {
    if (existsSync(candidate)) return candidate;
  }
  return null;
}

/**
 * The tsx CLI, run through this same node binary.
 *
 * Going via `npm run dev:api` would mean spawning npm.cmd, and Node refuses to
 * spawn a .cmd without a shell -- while a shell breaks the venv path, which
 * contains a space. Calling the CLI's own entry point directly avoids both.
 */
const TSX = join(ROOT, "node_modules", "tsx", "dist", "cli.mjs");

function start(name, command, args, cwd) {
  const child = spawn(command, args, {
    cwd,
    // Deliberately not `shell: true`. The repository path contains a space,
    // and a shell would split it -- "E:\RAG Project" becomes "E:\RAG".
    shell: false,
    env: process.env,
  });
  children.push({ name, child });

  for (const stream of [child.stdout, child.stderr]) {
    let buffer = "";
    stream.setEncoding("utf8");
    stream.on("data", (chunk) => {
      buffer += chunk;
      const lines = buffer.split("\n");
      buffer = lines.pop() ?? "";
      for (const line of lines) if (line.trim()) log(name, line);
    });
  }

  child.on("exit", (code) => {
    if (shuttingDown) return;
    log(name, `exited with code ${code}`);
    // One service down means the console will fail in a way that looks like a
    // bug in the pipeline. Take the whole thing down instead.
    shutdown(1);
  });

  return child;
}

function shutdown(code) {
  if (shuttingDown) return;
  shuttingDown = true;
  process.stdout.write(`\n${DIM}stopping…${RESET}\n`);
  for (const { child } of children) {
    try {
      if (process.platform === "win32") {
        spawnSync("taskkill", ["/pid", String(child.pid), "/f", "/t"], {
          stdio: "ignore",
        });
      } else {
        child.kill("SIGTERM");
      }
    } catch { /* already gone */ }
  }
  setTimeout(() => process.exit(code), 400);
}

async function reachable(url) {
  try {
    const response = await fetch(url, { signal: AbortSignal.timeout(1500) });
    return response.status < 500;
  } catch {
    return false;
  }
}

async function waitFor(url, label, seconds = 90) {
  for (let i = 0; i < seconds * 2; i++) {
    if (await reachable(url)) return true;
    await new Promise((resolve) => setTimeout(resolve, 500));
  }
  log("dev", `${label} did not come up within ${seconds}s`);
  return false;
}

// ── containers ─────────────────────────────────────────────
const infra = spawnSync("docker", ["compose", "up", "-d"], {
  cwd: ROOT,
  encoding: "utf8",
});
if (infra.status !== 0) {
  process.stderr.write(
    "Could not start the containers. Is Docker Desktop running?\n" +
      (infra.stderr || infra.error?.message || "") +
      "\n",
  );
  process.exit(1);
}
log("dev", "postgres, redis and qdrant are up");

// ── services ───────────────────────────────────────────────
if (!existsSync(TSX)) {
  process.stderr.write("tsx is missing — run `npm install` first.\n");
  process.exit(1);
}

const python = pythonPath();
if (!python) {
  process.stderr.write(
    "No virtualenv found at services/rag/.venv — create it and install " +
      "requirements.txt first.\n",
  );
  process.exit(1);
}

start(
  "rag",
  python,
  [
    "-m", "uvicorn", "app.main:app",
    "--host", "127.0.0.1", "--port", RAG_PORT,
    // Parity with tsx watch on the Node side: editing the pipeline should not
    // mean restarting the stack.
    "--reload", "--reload-dir", "app",
  ],
  join(ROOT, "services", "rag"),
);
start("api", process.execPath, [TSX, "watch", "src/index.ts"], join(ROOT, "apps", "api"));
start("worker", process.execPath, [TSX, "watch", "src/index.ts"], join(ROOT, "apps", "worker"));
// Next ships its own CLI entry point, spawned through this node binary for the
// same reason as tsx: no .cmd shim, no shell, no path split at the space.
start(
  "web",
  process.execPath,
  [
    join(ROOT, "node_modules", "next", "dist", "bin", "next"),
    "dev",
    "--port",
    WEB_PORT,
  ],
  join(ROOT, "apps", "web"),
);

for (const signal of ["SIGINT", "SIGTERM"]) {
  process.on(signal, () => shutdown(0));
}

const appUrl = `http://localhost:${WEB_PORT}`;
const consoleUrl = `http://localhost:${API_PORT}/console`;

// The console answers as soon as the API binds; Next still has a first compile
// to get through, so it gets a longer grace period and the last word.
await waitFor(consoleUrl, "the console");
if (await waitFor(appUrl, "the web app", 180)) {
  process.stdout.write(
    `\n  \x1b[32m▲\x1b[0m  ExamPrep at \x1b[4m${appUrl}\x1b[0m\n` +
      `     ${DIM}dev console  ${consoleUrl}${RESET}\n` +
      `     ${DIM}rag :${RAG_PORT}   api :${API_PORT}   ctrl-c stops everything${RESET}\n\n`,
  );
}
