import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { createServer } from "node:http";
import { chromium } from "playwright";

const web = new URL("../../local_video/web/", import.meta.url);
const server = createServer((request, response) => {
  const pathname = new URL(request.url, "http://127.0.0.1").pathname;
  const asset = pathname === "/" ? "index.html" : pathname.slice(1);
  if (!/^(index\.html|theme-boot\.js|assets\/[\w-]+\.(js|css))$/.test(asset)) {
    response.writeHead(404).end();
    return;
  }
  response.setHeader("Content-Type", asset.endsWith(".js") ? "text/javascript" :
    asset.endsWith(".css") ? "text/css" : "text/html; charset=utf-8");
  response.end(readFileSync(new URL(asset, web)));
});
await new Promise((resolve) => server.listen(0, "127.0.0.1", resolve));
let browser;
try {
  browser = await chromium.launch({ channel: "msedge", headless: true });
  const page = await browser.newPage({ viewport: { width: 1440, height: 1000 } });
  page.setDefaultTimeout(8000);
  await page.addInitScript(() => {
    const library = {
      id: "fixture", name: "合成库", path: "F:\\Synthetic", quota_bytes: 0,
      used_bytes: 12, disk_total: 100, disk_free: 88, default: true, online: true,
      video_count: 1, image_count: 0, media_count: 1, message_count: 1,
    };
    const resource = {
      key: "x:1:0", platform: "x", message_id: "1", media_id: "0", media_type: "video",
      title: "合成视频", author: "fixture", source_label: "X",
      locations: [{ id: 1, extension: "mp4", output: library.path, bytes: 12, width: 16, height: 9, quality: "best" }],
      tags: [],
    };
    const settings = {
      theme: "light", accent: "#397863", privacy_mode: false,
      overview_layout: localStorage.getItem("fixture-overview-layout") || "",
    };
    window.layoutCalls = [];
    window.pywebview = { api: { async call(action, payload = {}) {
      const ok = (data) => ({ ok: true, data });
      window.layoutCalls.push({ action, payload });
      if (action === "bootstrap" || action === "snapshot") return ok({
        version: "0.7.21", settings: { ...settings }, tasks: [], total: 0, offset: 0,
        counts: { all: 0, pending: 0, completed: 0, ignored: 0 }, tag_options: [],
        libraries: [library], default_library: library.id, busy: false,
      });
      if (action === "resource_overview") return ok({
        ...(payload.stats === false ? {} : { video_count: 1, image_count: 0 }),
        resources: payload.recent === false ? [] : [resource],
        total: payload.recent === false ? 0 : 1, offset: 0,
      });
      if (action === "resources") return ok({ resources: [resource], total: 1, offset: 0 });
      if (action === "settings") {
        Object.assign(settings, payload);
        localStorage.setItem("fixture-overview-layout", settings.overview_layout);
        return ok(settings);
      }
      if (action === "poll") return ok({ events: [], busy: false });
      return ok(null);
    } } };
  });
  await page.goto(`http://127.0.0.1:${server.address().port}/?desktop=1`);
  const grid = page.locator(".overview-layout-grid");
  const overviewModules = () => grid.locator(":scope > [data-module]").evaluateAll(
    (nodes) => nodes.map((node) => [node.dataset.module, node.dataset.width]),
  );
  await grid.getByText("合成视频", { exact: true }).waitFor();
  assert.deepEqual(await overviewModules(), [["stats", "full"], ["recent", "full"], ["libraries", "full"]]);
  await page.getByRole("button", { name: "编辑布局", exact: true }).click();
  const editor = page.getByRole("region", { name: "编辑总览布局" });
  await editor.getByRole("button", { name: "下移媒体统计" }).press("Enter");
  assert.equal(await editor.locator("[data-module]").first().getAttribute("data-module"), "recent");
  await editor.getByRole("button", { name: "上移媒体统计" }).press("Enter");
  await editor.getByLabel("显示最近添加").uncheck();
  await editor.getByLabel("媒体库宽度").selectOption("half");
  await editor.getByLabel("媒体统计宽度").selectOption("half");
  await editor.locator('[data-module="libraries"] .overview-module-handle').dragTo(
    editor.locator('[data-module="stats"]'),
  );
  await editor.getByRole("button", { name: "保存布局", exact: true }).click();
  await editor.waitFor({ state: "hidden" });
  assert.deepEqual(await overviewModules(), [["libraries", "half"], ["stats", "half"]]);
  await page.waitForFunction(() => window.layoutCalls.some(
    ({ action, payload }) => action === "resource_overview" && payload.stats === true && payload.recent === false,
  ));
  const desktopColumns = await grid.evaluate((node) => getComputedStyle(node).gridTemplateColumns.trim().split(/\s+/).length);
  assert.equal(desktopColumns, 2);
  await page.setViewportSize({ width: 570, height: 825 });
  const narrowColumns = await grid.evaluate((node) => getComputedStyle(node).gridTemplateColumns.trim().split(/\s+/).length);
  assert.equal(narrowColumns, 1);
  await page.reload();
  await grid.getByRole("region", { name: "媒体统计" }).waitFor();
  assert.deepEqual(await overviewModules(), [["libraries", "half"], ["stats", "half"]]);
  await page.getByRole("button", { name: "编辑布局", exact: true }).click();
  await editor.getByLabel("显示媒体统计").uncheck();
  await editor.getByLabel("显示媒体库").uncheck();
  await page.evaluate(() => { window.layoutCalls.length = 0; });
  await editor.getByRole("button", { name: "保存布局", exact: true }).click();
  await editor.waitFor({ state: "hidden" });
  assert.deepEqual(await overviewModules(), []);
  await page.reload();
  await page.getByRole("button", { name: "编辑布局", exact: true }).waitFor();
  assert.deepEqual(await overviewModules(), []);
  assert.equal(await page.evaluate(() => window.layoutCalls.some(
    ({ action }) => action === "resource_overview",
  )), false);
  await page.getByRole("button", { name: "编辑布局", exact: true }).click();
  await editor.getByRole("button", { name: "恢复默认", exact: true }).click();
  await editor.getByRole("button", { name: "保存布局", exact: true }).click();
  await grid.getByText("合成视频", { exact: true }).waitFor();
  assert.deepEqual(await overviewModules(), [["stats", "full"], ["recent", "full"], ["libraries", "full"]]);
  await grid.getByRole("button", { name: "查看全部", exact: true }).click();
  await page.getByLabel("搜索资源").waitFor();
  await page.getByRole("button", { name: "总览", exact: true }).last().click();
  await grid.getByText("合成视频", { exact: true }).waitFor();
  console.log("PASS dashboard customization, selective data, layout persistence, narrow view, reset and back navigation");
} finally {
  await browser?.close();
  server.close();
}
