// Nightly copy of corpus/ and releases/ to the R2 bucket carter-frontier-archive.
// Additive only: `aws s3 sync` without --delete, so nothing in the archive is ever removed.
// Never logs environment values. Secrets come in as env vars named R2_ACCESS_KEY_ID and
// R2_SECRET_ACCESS_KEY (R2 S3-style keys, not the Cloudflare API token).
//
// Usage (in the Action):  node scripts/r2_archive.mjs --run-id $GITHUB_RUN_ID --commit $GITHUB_SHA --date YYYY-MM-DD [--dry-run]

import { spawn } from "node:child_process";
import { createHash } from "node:crypto";
import { readdirSync, statSync, appendFileSync, existsSync, mkdirSync } from "node:fs";
import { join, relative, sep } from "node:path";

export const BUCKET = "carter-frontier-archive";
// Account id is an identifier, not a credential (it appears in the public wrangler.toml).
export const ACCOUNT_ID = "f78d1a4644be39af1ac801a1ba300a5c";
export const SOURCES = ["corpus", "releases"];
export const SECRET_NAMES = ["R2_ACCESS_KEY_ID", "R2_SECRET_ACCESS_KEY"];
export const MANIFEST_PATH = "status/R2_ARCHIVE_RUNS.csv";

export function endpoint(acct = ACCOUNT_ID) {
  return `https://${acct}.r2.cloudflarestorage.com`;
}

// Additive by design: there is no --delete anywhere in this file.
export function buildSyncArgs(src, prefix, { dryRun = false, acct = ACCOUNT_ID } = {}) {
  const args = ["s3", "sync", src, `s3://${BUCKET}/${prefix}/`, "--endpoint-url", endpoint(acct), "--only-show-errors"];
  if (dryRun) args.push("--dryrun");
  return args;
}

// Names only in the error. Values are never read into a message.
export function checkSecrets(env) {
  const missing = SECRET_NAMES.filter((n) => !env[n]);
  if (missing.length) throw new Error(`missing repository secret(s): ${missing.join(", ")}`);
}

// Map the R2 names onto the names the AWS CLI reads. Region "auto" is what R2 expects.
export function awsEnv(env) {
  checkSecrets(env);
  return {
    ...env,
    AWS_ACCESS_KEY_ID: env.R2_ACCESS_KEY_ID,
    AWS_SECRET_ACCESS_KEY: env.R2_SECRET_ACCESS_KEY,
    AWS_DEFAULT_REGION: "auto",
  };
}

// Inventory of a folder: relative path + size. No file contents are read.
export function inventory(root, folder) {
  const base = join(root, folder);
  if (!existsSync(base)) return [];
  const out = [];
  const walk = (dir) => {
    for (const name of readdirSync(dir)) {
      const p = join(dir, name);
      const st = statSync(p);
      if (st.isDirectory()) walk(p);
      else out.push({ rel: relative(root, p).split(sep).join("/"), size: st.size });
    }
  };
  walk(base);
  return out.sort((a, b) => (a.rel < b.rel ? -1 : a.rel > b.rel ? 1 : 0));
}

// Hash over path and size only. Changes whenever a file is added, removed or resized.
export function manifestHash(items) {
  const h = createHash("sha256");
  for (const it of items) h.update(`${it.rel}\t${it.size}\n`);
  return h.digest("hex");
}

export function manifestLine({ date, runId, commit, files, bytes, manifestSha, status }) {
  return [date, runId, commit, files, bytes, manifestSha, status].join(",");
}

// Core run. The runner is injected so tests never touch AWS.
export async function runArchive({ root, env, date, runId, commit, dryRun = false, runner, log = () => {} }) {
  const aws = awsEnv(env); // throws if secrets are absent
  const items = [];
  let status = "OK";
  for (const folder of SOURCES) {
    const inv = inventory(root, folder);
    items.push(...inv.map((i) => ({ rel: i.rel, size: i.size })));
    const args = buildSyncArgs(join(root, folder), folder, { dryRun });
    const code = await runner("aws", args, aws);
    log(`sync ${folder}: exit ${code}${dryRun ? " (dry run)" : ""}`);
    if (code !== 0) status = `FAIL_${folder}_exit${code}`;
  }
  items.sort((a, b) => (a.rel < b.rel ? -1 : a.rel > b.rel ? 1 : 0));
  const bytes = items.reduce((n, i) => n + i.size, 0);
  const line = manifestLine({
    date, runId: runId || "local", commit: commit || "local", files: items.length, bytes,
    manifestSha: manifestHash(items), status: dryRun ? `${status}_DRYRUN` : status,
  });
  return { line, status, files: items.length, bytes };
}

// Real AWS CLI runner. stdio is inherited (aws prints errors); no env values are logged here.
export function spawnRunner(cmd, args, env) {
  return new Promise((resolve) => {
    const p = spawn(cmd, args, { env, stdio: ["ignore", "inherit", "inherit"] });
    p.on("close", (code) => resolve(code === null ? 1 : code));
    p.on("error", () => resolve(1));
  });
}

const isMain = process.argv[1] && process.argv[1].replace(/\\/g, "/").endsWith("scripts/r2_archive.mjs");
if (isMain) {
  const arg = (k) => { const i = process.argv.indexOf(k); return i > -1 ? process.argv[i + 1] : null; };
  const date = arg("--date") || new Date().toISOString().slice(0, 10);
  const dryRun = process.argv.includes("--dry-run");
  try {
    const res = await runArchive({
      root: process.cwd(), env: process.env, date, runId: arg("--run-id"), commit: arg("--commit"),
      dryRun, runner: spawnRunner, log: (m) => console.log(m),
    });
    if (!existsSync("status")) mkdirSync("status", { recursive: true });
    appendFileSync(MANIFEST_PATH, res.line + "\n");
    console.log(`manifest: ${res.files} files, ${res.bytes} bytes, status ${res.status}`);
    process.exit(res.status === "OK" || res.status.endsWith("_DRYRUN") ? 0 : 1);
  } catch (e) {
    console.error(`archive not run: ${e.message}`);
    process.exit(1);
  }
}
