import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { createServer } from "node:http";
import { fileURLToPath } from "node:url";
import { chromium } from "playwright";

const files = new URL("../../local_video/web/", import.meta.url);
const server = createServer((request, response) => {
  const path = new URL(request.url, "http://127.0.0.1").pathname;
  const name = path === "/" ? "index.html" : path.slice(1);
  if (!/^(index\.html|theme-boot\.js|assets\/[\w-]+\.(js|css))$/.test(name)) {
    response.writeHead(404).end();
    return;
  }
  const type = name.endsWith(".js") ? "text/javascript" : name.endsWith(".css") ? "text/css" : "text/html; charset=utf-8";
  response.setHeader("Content-Type", type);
  response.end(readFileSync(fileURLToPath(new URL(name, files))));
});
await new Promise((resolve) => server.listen(0, "127.0.0.1", resolve));
let browser;
try {
  browser = await chromium.launch({ channel: "msedge", headless: true });
  const page = await browser.newPage({ viewport: { width: 1300, height: 850 } });
  page.setDefaultTimeout(8000);
  await page.addInitScript(() => {
    const entries = Array.from({ length: 106 }, (_, i) => ({
      id: String(i + 1000),
      author: "fixture",
      handle: "fixture",
      display_name: "合成条目 " + i,
      title: "合成条目 " + i,
      text: i === 103 ? "只匹配此行" : "连续加载样本",
      updated: 0,
      ignored: false,
      media: [{
        id: i, media_id: "0", media_type: "image", extension: "jpg",
        width: 720, height: 480, output: "F:\Fixture", quality: "best",
        state: "completed", error: "", bytes: 10, verification: "length+sha256",
      }],
    }));
    const library = {
      id: "main", name: "测试连续库", path: "F:\Fixture",
      online: true, default: true, quota_bytes: 0,
      message_count: entries.length, media_count: entries.length,
    };
    const previous = window.sessionStorage.getItem("continuous-pref");
    const settings = { theme: "light", accent: "#397863", privacy_mode: false, infinite_scroll: previous === "1" };
    const jobCounts = { all: 0, completed: 0, pending: 0, ignored: 0 };
    let failNext = true;
    const wait = new Promise((resolve) => { window.__finishSummary = resolve; });
    window.__listCalls = [];
    window.pywebview = { api: { async call(action, p = {}) {
      const response = (data) => ({ ok: true, data });
      const base = {
        version: "0.7.20", libraries: [library], default_library: "main", settings: { ...settings },
        tag_options: [], tasks: [], counts: jobCounts, total: 0, offset: 0, busy: false,
      };
      if (action === "bootstrap") return response({ ...base, libraries: [{ ...library, capacity_pending: true, online: false }] });
      if (action === "snapshot") {
        if (!p.collection) {
          await wait;
          return response(base);
        }
        const offset = p.offset || 0;
        const limit = p.limit || 50;
        window.__listCalls.push({ offset, query: p.query || "" });
        if (!p.query && offset === 50 && failNext) {
          failNext = false;
          return { ok: false, error: "合成分页错误" };
        }
        const rows = p.query ? entries.filter((item) => item.text.includes(p.query)) : entries;
        return response({
          ...base, tasks: rows.slice(offset, offset + limit), total: rows.length, offset,
          counts: { ...jobCounts, all: rows.length, completed: rows.length },
        });
      }
      if (action === "settings") {
        Object.assign(settings,p);
        window.sessionStorage.setItem("continuous-pref", settings.infinite_scroll ? "1" : "0");
        return response(settings);
      }
      if (action === "poll") return response({ events: [], busy: false });
      if (action === "resource_overview") return response({ resources: [], video_count: 0, image_count: 0 });
      if (action === "local_library") return response({ items: [], total: 0, offset: 0 });
      return response(null);
    } } };
  });
  await page.goto(`http://127.0.0.1:${server.address().port}/?desktop=1`);
  const nav = page.locator("nav");
  await page.getByRole("heading", { name: "总览", exact: true }).waitFor();
  const expandLibrary = nav.getByRole("button", { name: "展开媒体库", exact: true });
  const downloads = nav.getByRole("button", { name: "下载任务", exact: true });
  assert.equal(await expandLibrary.getAttribute("aria-expanded"), "false");
  assert.equal(await downloads.getAttribute("aria-expanded"), "false");
  assert.ok((await expandLibrary.boundingBox()).y < (await downloads.boundingBox()).y);
  await expandLibrary.click();
  await nav.getByRole("button", { name: "收起媒体库", exact: true }).click();
  await page.evaluate(() => window.__finishSummary());
  await page.getByRole("button", { name: "设置", exact: true }).click();
  await page.getByRole("button", { name: "外观", exact: true }).click();
  await page.getByLabel("瀑布流连续加载").check();
  await nav.getByRole("button", { name: "媒体库", exact: true }).click();
  await page.getByRole("button", { name: "进入测试连续库", exact: true }).click();
  await page.locator(".tweet-card").first().waitFor();

  const scrollEnd = () => page.evaluate(() => window.scrollTo(0, document.documentElement.scrollHeight));
  await scrollEnd();
  await page.getByRole("button", { name: /重试/ }).click();
  await page.waitForFunction(() => window.__listCalls.filter((call) => call.offset === 50).length >= 2);
  assert.ok(await page.evaluate(() => window.scrollY > 0));
  await page.getByText("合成条目 99", { exact: true }).waitFor();
  await scrollEnd();
  await page.getByText("已加载全部", { exact: false }).waitFor();
  assert.equal(await page.locator(".tweet-card").count(), 106);
  await page.getByPlaceholder("搜索内容、作者、文件名…").fill("只匹配此行");
  await page.waitForFunction(() => document.querySelectorAll(".tweet-card").length === 1);
  assert.deepEqual(await page.evaluate(() => window.__listCalls.at(-1)), { offset: 0, query: "只匹配此行" });
  console.log("PASS startup shell before summary, collapsed navigation, continuous X append, retry and filter reset");
} finally {
  await browser?.close();
  server.close();
}
