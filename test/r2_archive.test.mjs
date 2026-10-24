// Offline tests for scripts/r2_archive.mjs. No AWS calls: the runner is a fake.
// Run from the repo root:  node --test test/r2_archive.test.mjs
import { test } from "node:test";
import assert from "node:assert/strict";
import { mkdtempSync, mkdirSync, writeFileSync, readFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { join, dirname } from "node:path";
import { fileURLToPath } from "node:url";
import {
  BUCKET, ACCOUNT_ID, SOURCES, SECRET_NAMES, endpoint, buildSyncArgs, checkSecrets,
  awsEnv, inventory, manifestHash, manifestLine, runArchive,
} from "../scripts/r2_archive.mjs";

const here = dirname(fileURLToPath(import.meta.url));
const SECRET_KEY_VALUE = "SUPERSECRET-value-do-not-leak-9f8e7d";
const KEY_ID_VALUE = "AKIDVALUE-do-not-leak-1234";
const goodEnv = { R2_ACCESS_KEY_ID: KEY_ID_VALUE, R2_SECRET_ACCESS_KEY: SECRET_KEY_VALUE };

function fixtureRoot() {
  const root = mkdtempSync(join(tmpdir(), "r2arch-"));
  mkdirSync(join(root, "corpus", "e1"), { recursive: true });
  mkdirSync(join(root, "releases", "edition-1"), { recursive: true });
  writeFileSync(join(root, "corpus", "a.csv"), "x,y\n1,2\n");
  writeFileSync(join(root, "corpus", "e1", "b.txt"), "hello");
  writeFileSync(join(root, "releases", "edition-1", "c.csv"), "z");
  return root;
}

test("sync args: additive only, correct bucket path and endpoint", () => {
  const args = buildSyncArgs("/x/corpus", "corpus");
  assert.deepEqual(args.slice(0, 2), ["s3", "sync"]);
  assert.ok(args.includes(`s3://${BUCKET}/corpus/`));
  assert.ok(args.includes(endpoint()));
  assert.equal(endpoint(), `https://${ACCOUNT_ID}.r2.cloudflarestorage.com`);
  assert.ok(!args.includes("--delete"), "must never delete from the archive");
  assert.ok(!args.includes("--dryrun"));
  assert.ok(buildSyncArgs("/x", "corpus", { dryRun: true }).includes("--dryrun"));
});

test("both sources are archived", () => {
  assert.deepEqual(SOURCES, ["corpus", "releases"]);
});

test("missing secrets fail with names only, never values", () => {
  assert.throws(
    () => checkSecrets({ R2_ACCESS_KEY_ID: KEY_ID_VALUE }),
    (e) => {
      assert.match(e.message, /R2_SECRET_ACCESS_KEY/);
      assert.ok(!e.message.includes(KEY_ID_VALUE), "must not echo the set secret");
      return true;
    },
  );
  assert.throws(() => checkSecrets({}), /R2_ACCESS_KEY_ID, R2_SECRET_ACCESS_KEY/);
});

test("awsEnv maps R2 names to the AWS CLI names and sets region auto", () => {
  const e = awsEnv(goodEnv);
  assert.equal(e.AWS_ACCESS_KEY_ID, KEY_ID_VALUE);
  assert.equal(e.AWS_SECRET_ACCESS_KEY, SECRET_KEY_VALUE);
  assert.equal(e.AWS_DEFAULT_REGION, "auto");
  assert.deepEqual(SECRET_NAMES, ["R2_ACCESS_KEY_ID", "R2_SECRET_ACCESS_KEY"]);
});

test("inventory is sorted and reads sizes, not contents", () => {
  const root = fixtureRoot();
  const inv = inventory(root, "corpus");
  assert.deepEqual(inv.map((i) => i.rel), ["corpus/a.csv", "corpus/e1/b.txt"]);
  assert.equal(inv[0].size, 8);
  assert.deepEqual(inventory(root, "missing-folder"), []);
});

test("manifest hash is deterministic and changes with size", () => {
  const items = [{ rel: "a", size: 1 }, { rel: "b", size: 2 }];
  assert.equal(manifestHash(items), manifestHash([...items]));
  assert.notEqual(manifestHash(items), manifestHash([{ rel: "a", size: 1 }, { rel: "b", size: 3 }]));
});

test("manifest line is 7 comma-separated fields with no stray commas", () => {
  const line = manifestLine({ date: "2026-10-28", runId: "123", commit: "abc", files: 4, bytes: 99, manifestSha: "ff", status: "OK" });
  assert.equal(line, "2026-10-28,123,abc,4,99,ff,OK");
  assert.equal(line.split(",").length, 7);
});

test("run: calls aws twice with the mapped env, writes a manifest line, leaks no secret", async () => {
  const root = fixtureRoot();
  const calls = [];
  const logs = [];
  const runner = async (cmd, args, env) => {
    calls.push({ cmd, args, env });
    return 0;
  };
  const res = await runArchive({
    root, env: goodEnv, date: "2026-10-28", runId: "9", commit: "deadbeef",
    runner, log: (m) => logs.push(m),
  });
  assert.equal(calls.length, 2);
  assert.ok(calls.every((c) => c.cmd === "aws" && !c.args.includes("--delete")));
  assert.ok(calls[0].args.includes(`s3://${BUCKET}/corpus/`));
  assert.ok(calls[1].args.includes(`s3://${BUCKET}/releases/`));
  assert.equal(res.status, "OK");
  assert.equal(res.files, 3);
  assert.equal(res.bytes, 8 + 5 + 1);
  assert.match(res.line, /^2026-10-28,9,deadbeef,3,14,[0-9a-f]{64},OK$/);
  const everything = JSON.stringify({ calls: calls.map((c) => c.args), logs, line: res.line });
  assert.ok(!everything.includes(SECRET_KEY_VALUE), "secret value must not appear in args, logs or manifest");
  assert.ok(!everything.includes(KEY_ID_VALUE), "key id must not appear in args, logs or manifest");
});

test("a failed sync is recorded as FAIL in the manifest line, not hidden", async () => {
  const root = fixtureRoot();
  const runner = async (_c, args) => (args.includes(`s3://${BUCKET}/releases/`) ? 1 : 0);
  const res = await runArchive({ root, env: goodEnv, date: "2026-10-28", runner });
  assert.equal(res.status, "FAIL_releases_exit1");
  assert.match(res.line, /FAIL_releases_exit1$/);
});

test("dry run is labelled in the manifest", async () => {
  const root = fixtureRoot();
  const res = await runArchive({ root, env: goodEnv, date: "2026-10-28", dryRun: true, runner: async () => 0 });
  assert.match(res.line, /OK_DRYRUN$/);
});

test("run refuses to start without secrets and calls nothing", async () => {
  const root = fixtureRoot();
  let called = false;
  await assert.rejects(
    runArchive({ root, env: {}, date: "2026-10-28", runner: async () => { called = true; return 0; } }),
    /missing repository secret/,
  );
  assert.equal(called, false);
});

test("workflow is nightly + manual, no PR trigger, no delete, only the two R2 secrets", () => {
  const wf = readFileSync(join(here, "..", ".github", "workflows", "r2_archive_nightly.yml"), "utf8");
  assert.match(wf, /schedule:/);
  assert.match(wf, /workflow_dispatch:/);
  assert.doesNotMatch(wf, /pull_request/);
  assert.doesNotMatch(wf, /--delete/);
  const used = [...wf.matchAll(/secrets\.([A-Z0-9_]+)/g)].map((m) => m[1]);
  assert.ok(used.length > 0);
  assert.ok(used.every((n) => SECRET_NAMES.includes(n)), `unexpected secret(s): ${used}`);
  assert.doesNotMatch(wf, /echo .*\$\{\{ *secrets\./, "no secret echoed");
});
