import http from "node:http";
import { readFileSync } from "node:fs";
import { fileURLToPath } from "node:url";
import { resolve } from "node:path";
import assert from "node:assert/strict";
import { chromium } from "playwright";
const root = fileURLToPath(new URL("../../local_video/web/", import.meta.url));
const server = http.createServer((req, res) => {
  const path = new URL(req.url, "http://127.0.0.1").pathname;
  if (
    path !== "/" &&
    path !== "/theme-boot.js" &&
    !/^\/assets\/[A-Za-z0-9_-]+\.(js|css)$/.test(path)
  ) {
    res.writeHead(404).end();
    return;
  }
  try {
    res.setHeader(
      "Content-Type",
      path.endsWith(".js")
        ? "text/javascript"
        : path.endsWith(".css")
          ? "text/css"
          : "text/html; charset=utf-8",
    );
    res.end(
      readFileSync(resolve(root, path === "/" ? "index.html" : path.slice(1))),
    );
  } catch {
    res.writeHead(404).end();
  }
});
await new Promise((r) => server.listen(0, "127.0.0.1", r));
server.unref();
const browser = await chromium.launch({ channel: "msedge", headless: true });
try {
  const page = await browser.newPage({
    viewport: { width: 1440, height: 1000 },
  });
  page.setDefaultTimeout(5000);
  const errors = [];
  page.on("pageerror", (e) => errors.push(e.message));
  await page.goto(`http://127.0.0.1:${server.address().port}`);
  await page.locator(".tweet-card").first().waitFor();
  const nav = (name) =>
    page.locator(".sidebar").getByRole("button", { name, exact: true });
  await nav("总览").click();
  await page.locator(".recent-resources .resource-card").first().waitFor();
  assert.equal(await page.locator(".storage").count(), 0);
  assert.equal(await page.locator(".resource-stats button").count(), 3);
  assert.equal(
    await page.locator(".recent-resources .resource-card").count(),
    3,
    "cross-library copies deduplicated",
  );
  assert.equal(await page.locator(".library-capacity progress").count(), 2);
  assert.match(
    await page.locator(".collection-card").first().innerText(),
    /媒体库配额/,
  );
  await page.locator(".resource-stats button").nth(1).click();
  await page.waitForFunction(
    () =>
      document.querySelectorAll(".resource-grid .resource-card").length === 1,
  );
  assert.ok(await nav("总览").evaluate((e) => e.classList.contains("active")));
  assert.equal(
    await page.locator(".operation-status,.stats,.collections-panel").count(),
    0,
  );
  await page.getByLabel("搜索资源").fill("notfound");
  await page.getByLabel("搜索资源").press("Backspace");
  assert.equal(await page.getByLabel("搜索资源").inputValue(), "notfoun");
  await page.getByLabel("搜索资源").fill("");
  await page.waitForFunction(
    () =>
      document.querySelectorAll(".resource-grid .resource-card").length === 1,
  );
  await page.locator(".resource-preview").first().click();
  await page.getByRole("dialog").waitFor();
  assert.equal(
    await page.getByLabel("资源位置与版本").locator("option").count(),
    2,
  );
  await page.getByRole("button", { name: "打开媒体", exact: true }).focus();
  await page.keyboard.press("Backspace");
  assert.equal(await page.getByRole("dialog").count(), 0);
  assert.equal(
    await page.getByLabel("搜索资源").count(),
    1,
    "one key closes exactly one layer",
  );
  await page.locator("h1").click();
  await page.keyboard.press("Backspace");
  await page.locator(".collections-panel").waitFor();
  await page
    .getByRole("button", { name: "封面 日常收藏", exact: true })
    .click();
  await page.getByRole("button", { name: "选择标识 🎬", exact: true }).click();
  for (const width of [1440, 600, 375]) {
    await page.setViewportSize({ width, height: 1000 });
    assert.equal(
      await page.evaluate(
        () => document.documentElement.scrollWidth > innerWidth,
      ),
      false,
      "no horizontal overflow",
    );
    const editor = await page.locator(".cover-editor").boundingBox();
    const actions = await page.locator(".cover-actions").boundingBox();
    assert.ok(
      actions.y >= editor.y + 170,
      "cover actions below preview and picker",
    );
  }
  await page.setViewportSize({ width: 1440, height: 1000 });
  await page.getByRole("button", { name: "保存标识", exact: true }).click();
  assert.match(
    await page.locator(".collection-entry h3").first().innerText(),
    /🎬/,
  );
  await page.getByRole("button", { name: "进入日常收藏", exact: true }).click();
  await page.getByRole("button", { name: "按博主聚类", exact: true }).click();
  await page.getByLabel("搜索博主").fill("fieldnotes");
  await nav("媒体库").click();
  await page.getByRole("button", { name: "进入日常收藏", exact: true }).click();
  assert.ok(
    await nav("媒体库").evaluate((e) => e.classList.contains("active")),
  );
  await page.getByLabel("搜索推文").fill("海边");
  await nav("总览").click();
  assert.equal(
    await page.getByLabel("搜索博主").inputValue(),
    "fieldnotes",
    "library route belongs to its column",
  );
  await page
    .getByRole("button", { name: "打开博主合集 山野来信", exact: true })
    .click({ button: "right" });
  await page.keyboard.press("Backspace");
  assert.equal(await page.getByRole("menu").count(), 0);
  assert.equal(await page.getByLabel("搜索博主").inputValue(), "fieldnotes");
  await page
    .getByRole("button", { name: "打开博主合集 山野来信", exact: true })
    .click();
  await page.locator(".tweet-card").waitFor();
  await page.locator("h1").click();
  await page.keyboard.press("Backspace");
  await page.getByLabel("搜索博主").waitFor();
  assert.equal(await page.getByLabel("搜索博主").inputValue(), "fieldnotes");
  const card = page.locator(".author-card").first();
  await card.hover();
  const hover = await card.evaluate((e) => ({
    card: getComputedStyle(e).backgroundColor,
    footer: getComputedStyle(e.querySelector(".author-link")).backgroundColor,
  }));
  assert.equal(
    hover.footer,
    "rgba(0, 0, 0, 0)",
    "handle footer inherits whole-card hover",
  );
  await nav("媒体库").click();
  assert.equal(await page.getByLabel("搜索推文").inputValue(), "海边");
  await page.locator("h1").click();
  await page.keyboard.press("Backspace");
  await page.locator(".collections-panel").waitFor();
  await nav("媒体工具").click();
  await page.getByRole("button", { name: "媒体转移", exact: true }).click();
  await page.getByRole("button", { name: "集中转移", exact: true }).waitFor();
  await page.locator("h1").click();
  await page.keyboard.press("Backspace");
  await page.getByRole("button", { name: "媒体转移", exact: true }).waitFor();
  assert.equal(
    await page.getByRole("button", { name: "集中转移", exact: true }).count(),
    0,
  );
  assert.deepEqual(errors, []);
  await nav("总览").click();
  if (!(await page.locator(".collections-panel").isVisible()))
    await nav("总览").click();
  await page.screenshot({
    path: fileURLToPath(
      new URL("../../.audit/resources074-overview.png", import.meta.url),
    ),
    fullPage: true,
  });
  // Explicit parent navigation must not resurrect an overview child.
  await page.locator(".resource-stats button").nth(1).click();
  await page.getByLabel("搜索资源").waitFor();
  await nav("设置").click();
  await page.locator("h1").click();
  await page.keyboard.press("Backspace");
  await page.locator(".collections-panel").waitFor();
  assert.equal(await page.getByLabel("搜索资源").count(), 0);
  const long = await browser.newPage({
    viewport: { width: 1440, height: 700 },
  });
  await long.addInitScript(() => {
    const location = (i) => ({
      id: i + 1,
      media_id: String(i),
      media_type: "video",
      extension: "mp4",
      state: "completed",
      error: "",
      bytes: 1024,
      width: 100,
      height: 100,
      quality: "best",
      output: "F:\\Library",
      verification: "sha256-only",
      filename: `${i}.mp4`,
    });
    const rows = Array.from({ length: 120 }, (_, i) => ({
      key: String(i),
      platform: "x",
      source_label: "X",
      message_id: String(i),
      media_id: String(i),
      media_type: "video",
      added_at: null,
      title: "资源 " + i,
      author: "",
      tags: [],
      locations: [location(i)],
    }));
    const libraries = [
      {
        id: "main",
        name: "测试库",
        emoji: "📁",
        path: "F:\\Library",
        quota_bytes: 0,
        online: true,
        default: true,
        used_bytes: 1024,
        disk_free: 10240,
        disk_total: 20480,
        available_bytes: 10240,
        media_count: 120,
        video_count: 120,
        image_count: 0,
      },
    ];
    window.pywebview = {
      api: {
        async call(action, p = {}) {
          if (action === "snapshot")
            return {
              ok: true,
              data: {
                version: "test",
                settings: {
                  theme: "light",
                  accent: "#397863",
                  privacy_mode: false,
                },
                libraries,
                default_library: "main",
                tasks: [],
                counts: { all: 0, pending: 0, completed: 0, ignored: 0 },
                tag_options: [],
                total: 0,
                offset: 0,
                busy: false,
              },
            };
          if (action === "resource_overview")
            return {
              ok: true,
              data: {
                resources: rows.slice(0, 8),
                total: 120,
                offset: 0,
                video_count: 120,
                image_count: 0,
              },
            };
          if (action === "resources")
            return {
              ok: true,
              data: {
                resources: rows.slice(p.offset || 0, (p.offset || 0) + 50),
                total: 120,
                offset: p.offset || 0,
              },
            };
          if (action === "poll")
            return { ok: true, data: { events: [], busy: false } };
          return { ok: true, data: null };
        },
      },
    };
  });
  await long.goto(`http://127.0.0.1:${server.address().port}/?desktop=1`);
  await long
    .locator(".sidebar")
    .getByRole("button", { name: "总览", exact: true })
    .click();
  await long.locator(".recent-resources .resource-card").first().waitFor();
  await long.evaluate(() => window.scrollTo(0, 180));
  const rootScroll = await long.evaluate(() => scrollY);
  await long
    .locator(".resource-stats button")
    .nth(1)
    .evaluate((e) => e.click());
  await long.locator(".resource-grid .resource-card").first().waitFor();
  await long.getByLabel("搜索资源").fill("资源");
  await long.getByRole("button", { name: "下一页", exact: true }).click();
  await long.waitForFunction(
    () =>
      document.querySelector(".resource-card small") &&
      document.querySelector(".pagination").textContent.includes("51"),
  );
  await long.evaluate(() => {
    document.activeElement.blur();
    window.scrollTo(0, 1400);
  });
  const gridScroll = await long.evaluate(() => scrollY);
  await long.keyboard.press("Backspace");
  await long.locator(".collections-panel").waitFor();
  await long.waitForTimeout(100);
  assert.ok(
    Math.abs((await long.evaluate(() => scrollY)) - rootScroll) < 5,
    "return restores overview root scroll",
  );
  await long
    .locator(".resource-stats button")
    .nth(1)
    .evaluate((e) => e.click());
  await long.getByLabel("搜索资源").waitFor();
  await long.waitForTimeout(250);
  assert.equal(await long.getByLabel("搜索资源").inputValue(), "资源");
  assert.match(await long.locator(".pagination").innerText(), /51–100/);
  assert.ok(
    Math.abs((await long.evaluate(() => scrollY)) - gridScroll) < 5,
    "reopening restores resource page scroll",
  );
  await long.close();
  console.log(
    "PASS 074 resource stats/recent/dedup/filter/preview, emoji/cover spacing, library capacity, column isolation, Backspace modal/menu/input/parents, whole card hover, media tool",
  );
} finally {
  await browser.close();
  server.close();
}
