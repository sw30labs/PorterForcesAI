import assert from "node:assert/strict";
import { access, readFile } from "node:fs/promises";
import test from "node:test";

async function render() {
  const workerUrl = new URL("../dist/server/index.js", import.meta.url);
  workerUrl.searchParams.set("test", `${process.pid}-${Date.now()}`);
  const { default: worker } = await import(workerUrl.href);

  return worker.fetch(
    new Request("http://localhost/", { headers: { accept: "text/html" } }),
    {
      ASSETS: { fetch: async () => new Response("Not found", { status: 404 }) },
    },
    { waitUntil() {}, passThroughOnException() {} },
  );
}

test("server-renders the board advisory workspace", async () => {
  const response = await render();
  assert.equal(response.status, 200);
  assert.match(response.headers.get("content-type") ?? "", /^text\/html\b/i);

  const html = await response.text();
  assert.match(html, /<title>Porter Forces AI \| Board Decision Intelligence<\/title>/i);
  assert.match(html, /PORTER FORCES/);
  assert.match(html, /Board intelligence/);
  assert.match(html, /New analysis/);
  assert.match(html, /Ralph monitor/);
  assert.match(html, /Five forces/);
  assert.match(html, /Review &amp; approvals/);
  assert.match(html, /ILLUSTRATIVE WORKSPACE/);
  assert.match(html, /Every claim, source, score, approval, and financial value below is demonstration data/);
  assert.match(html, /Qwen3\.8-27B-4bit/);
  assert.doesNotMatch(html, /Your site is taking shape|react-loading-skeleton/i);
});

test("removes starter artifacts and declares the local API contract", async () => {
  const [page, workspace, layout, packageJson, lockfile, viteConfig] =
    await Promise.all([
      readFile(new URL("../app/page.tsx", import.meta.url), "utf8"),
      readFile(new URL("../app/workspace.tsx", import.meta.url), "utf8"),
      readFile(new URL("../app/layout.tsx", import.meta.url), "utf8"),
      readFile(new URL("../package.json", import.meta.url), "utf8"),
      readFile(new URL("../package-lock.json", import.meta.url), "utf8"),
      readFile(new URL("../vite.config.ts", import.meta.url), "utf8"),
    ]);

  assert.match(page, /AdvisoryWorkspace/);
  assert.match(layout, /Porter Forces AI \| Board Decision Intelligence/);
  assert.doesNotMatch(`${page}\n${packageJson}\n${lockfile}`, /react-loading-skeleton/);
  assert.match(workspace, /public_research_context/);
  assert.match(workspace, /evidence_cutoff: form\.evidenceCutoff/);
  assert.match(workspace, /scenario_economics: form\.finance\.enabled/);
  assert.match(workspace, /target: form\.target/);
  assert.match(workspace, /\/api\/analyses/);
  assert.match(workspace, /\/api\/runs\/\$\{encodeURIComponent\(run\.id\)\}/);
  assert.match(workspace, /setApprovals\(projectApprovals\(details\?\.approvals\)\)/);
  assert.match(workspace, /statusToken === "failed"/);
  assert.match(workspace, /statusToken === "blocked"/);
  assert.match(workspace, /Unique capture-attempt limit/);
  assert.match(workspace, /Failures consume one of 5–50 per-run slots/);
  assert.doesNotMatch(workspace, /Run canary/);
  assert.doesNotMatch(workspace, /Restart demo run/);
  assert.doesNotMatch(workspace, /Demo analysis commissioned/);
  assert.doesNotMatch(workspace, /requireFourApprovals/);
  assert.doesNotMatch(workspace, /paused|Pause|Resume/);
  assert.doesNotMatch(workspace, /local_inference\s*:/);
  assert.doesNotMatch(workspace, /counterevidence\s*:/);
  assert.match(viteConfig, /http:\/\/127\.0\.0\.1:8765/);
  await assert.rejects(access(new URL("../app/_sites-preview/", import.meta.url)));
});
