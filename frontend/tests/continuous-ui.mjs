import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import http from "node:http";
import { resolve } from "node:path";
import { fileURLToPath } from "node:url";
import { chromium } from "playwright";

const root = fileURLToPath(new URL("../../local_video/web/", import.meta.url));
const server = http.createServer((request, response) => {
  const path = new URL(request.url, "http://127.0.0.1").pathname;
  if (path !== "/" && path !== "/theme-boot.js" && !/^\/assets\/[\w-]+\.(js|css)$/.test(path)) {
    response.writeHead(404).end();
    return;
  }
  try {
    response.setHeader("Content-Type", path.endsWith(".js") ? "text/javascript" :
      path.endsWith(".css") ? "text/css" : "text/html; charset=utf-8");
    response.end(readFileSync(resolve(root, path === "/" ? "index.html" : path.slice(1))));
  } catch {
    response.writeHead(404).end();
  }
});
await new Promise((done) => server.listen(0, "127.0.0.1", done));
let browser;
try {
  browser = await chromium.launch({ channel: "msedge", headless: true });
  const page = await browser.newPage({ viewport: { width: 1320, height: 860 } });
  page.setDefaultTimeout(6000);
  const errors = [];
  page.on("pageerror", (error) => errors.push(error.message));
  await page.addInitScript(() => {
    const library = {
      id: "main", name: "连续加载样本库", path: "F:\\Fixture", online: true, default: true,
      quota_bytes: 0, used_bytes: 100, media_count: 120, video_count: 120, image_count: 0,
    };
    const resources = Array.from({ length: 120 }, (_, id) => ({
      key: String(id), platform: "x", source_label: "X", media_id: String(id),
      message_id: String(id), media_type: "video", author: "", tags: [],
      title: `资源样本 ${id}`, added_at: null,
      locations: [{ id, state: "completed", error: "", media_type: "video", extension: "mp4",
        quality: "best", output: library.path, bytes: 1024, width: 160, height: 100 }],
    }));
    const local = Array.from({ length: 62 }, (_, id) => ({
      id, title: `文件样本 ${id}`, kind: "image", bytes: 128, tags: [], libraries: ["main"],
      locations: [{ id, state: "missing", label: "样本", managed: false }],
    }));
    const state = {
      version: "fixture", libraries: [library], default_library: library.id, tasks: [],
      total: 0, offset: 0, counts: { all: 0, completed: 0, pending: 0, ignored: 0 },
      busy: false, tag_options: [], has_credentials: true, credential_error: "",
      settings: { theme: "light", accent: "#397863", privacy_mode: false, infinite_scroll: true },
    };
    const ok = (data) => ({ ok: true, data });
    const history = [];
    window.x2ltContinuousFixture = { history, releaseSlow: null, slowReleased: false };
    let failOnce = true;
    window.pywebview = { api: { async call(action, p = {}) {
      history.push({ action, offset: p.offset, query: p.query });
      if (action === "bootstrap" || action === "snapshot") return ok(state);
      if (action === "poll") return ok({ events: [], busy: false });
      if (action === "resource_overview") return ok({
        resources: resources.slice(0, 8), total: 120, offset: 0, video_count: 120, image_count: 0,
      });
      if (action === "resources") {
        const offset = p.offset || 0;
        if (offset === 50 && failOnce) {
          failOnce = false;
          return { ok: false, error: "暂时无法读取下一页" };
        }
        if (p.query === "慢关键词") {
          await new Promise((done) => { window.x2ltContinuousFixture.releaseSlow = done; });
          window.x2ltContinuousFixture.slowReleased = true;
          return ok({ resources: [resources[0]], total: 1, offset: 0 });
        }
        if (p.query) return ok({ resources: [resources[119]], total: 1, offset: 0 });
        const batch = resources.slice(offset, offset + 50);
        if (offset === 50) batch.push(resources[49]);
        return ok({ resources: batch, total: resources.length, offset });
      }
      if (action === "local_library") {
        const offset = p.offset || 0;
        const matches = p.query ? local.filter((item) => item.title.endsWith(p.query)) : local;
        return ok({ items: matches.slice(offset, offset + p.limit), total: matches.length, offset });
      }
      if (action === "local_media_edit") {
        const item = local.find(({ id }) => id === p.id);
        Object.assign(item, { title: p.title, body: p.body });
        return ok(true);
      }
      return ok(null);
    } } };
  });
  await page.goto(`http://127.0.0.1:${server.address().port}/?desktop=1`);
  const sidebar = page.locator(".sidebar");
  await sidebar.getByRole("button", { name: "总览", exact: true }).click();
  await page.getByRole("button", { name: "查看全部" }).click();

  const resourceGrid = page.locator(".resource-grid");
  const resourceFooter = page.locator(".resource-browser .continuous-footer");
  await resourceGrid.locator(".resource-card").first().waitFor();
  assert.equal(await resourceGrid.locator(".resource-card").count(), 50);
  await resourceFooter.scrollIntoViewIfNeeded();
  await resourceFooter.getByRole("alert").getByText("暂时无法读取下一页").waitFor();
  const before = await page.evaluate(() => window.scrollY);
  await resourceFooter.getByRole("button", { name: "重试加载" }).click();
  await page.waitForFunction(() => document.querySelectorAll(".resource-grid .resource-card").length >= 100);
  assert.ok(Math.abs((await page.evaluate(() => window.scrollY)) - before) < 80);
  await resourceFooter.scrollIntoViewIfNeeded();
  await resourceFooter.getByText("已加载全部 · 120 项").waitFor();
  assert.equal(await resourceGrid.locator(".resource-card").count(), 120);
  assert.equal(await resourceGrid.getByText("资源样本 49", { exact: true }).count(), 1);
  assert.equal(await resourceGrid.locator(".resource-card").first().evaluate(
    (element) => getComputedStyle(element).contentVisibility), "auto");

  const resourceSearch = page.getByRole("textbox", { name: "搜索资源" });
  await resourceSearch.fill("慢关键词");
  await page.waitForFunction(() => typeof window.x2ltContinuousFixture.releaseSlow === "function");
  await resourceSearch.fill("新关键词");
  await resourceGrid.getByText("资源样本 119", { exact: true }).waitFor();
  await page.evaluate(() => window.x2ltContinuousFixture.releaseSlow());
  await page.waitForFunction(() => window.x2ltContinuousFixture.slowReleased);
  assert.equal(await resourceGrid.getByText("资源样本 119", { exact: true }).count(), 1);
  assert.equal(await resourceGrid.getByText("资源样本 0", { exact: true }).count(), 0);

  await sidebar.getByRole("button", { name: "媒体库", exact: true }).click();
  await page.getByRole("button", { name: "进入连续加载样本库" }).click();
  await page.getByRole("button", { name: "本地导入", exact: true }).click();
  const localGrid = page.locator(".local-grid");
  const localFooter = page.locator(".local-library .continuous-footer");
  await localGrid.locator(".local-card").first().waitFor();
  assert.equal(await localGrid.locator(".local-card").count(), 24);
  await localFooter.scrollIntoViewIfNeeded();
  await page.waitForFunction(() => document.querySelectorAll(".local-grid .local-card").length >= 48);
  await localFooter.scrollIntoViewIfNeeded();
  await localFooter.getByText("已加载全部 · 62 项").waitFor();
  assert.equal(await localGrid.locator(".local-card").count(), 62);

  const offsets = await page.evaluate(() => window.x2ltContinuousFixture.history
    .filter(({ action, query }) => action === "local_library" && !query)
    .map(({ offset }) => offset));
  assert.deepEqual([...new Set(offsets)], [0, 24, 48]);
  assert.equal(await page.getByRole("button", { name: "下一页", exact: true }).count(), 0);

  await page.getByRole("button", { name: "管理模式", exact: true }).click();
  await localGrid.locator(".local-card").filter({ hasText: "文件样本 61" })
    .getByRole("button", { name: "编辑卡片信息" }).click();
  const localDetails = page.getByRole("dialog", { name: "图片详情" });
  await localDetails.getByRole("textbox", { name: "标题", exact: true }).fill("更新后的样本 61");
  await localDetails.getByRole("button", { name: "保存卡片信息" }).click();
  await localGrid.getByText("更新后的样本 61", { exact: true }).waitFor();
  assert.equal(await localGrid.locator(".local-card").count(), 62);
  await localDetails.getByRole("button", { name: "关闭弹窗" }).click();
  await page.getByRole("textbox", { name: "搜索内容" }).fill("61");
  await localGrid.getByText("更新后的样本 61", { exact: true }).waitFor();
  await page.waitForFunction(() => document.querySelectorAll(".local-grid .local-card").length === 1);
  assert.deepEqual(errors, []);
  console.log("PASS local/resources continuous pages, near-bottom load, dedup, retry and stale response guard");
} finally {
  await browser?.close();
  await new Promise((done) => server.close(done));
}
