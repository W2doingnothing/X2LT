import http from "node:http";
import { readFileSync } from "node:fs";
import { fileURLToPath } from "node:url";
import { resolve } from "node:path";
const root = fileURLToPath(new URL("../../local_video/web/", import.meta.url));
const server = http.createServer((req, res) => {
  const path = new URL(req.url, "http://127.0.0.1").pathname;
  if (
    path !== "/" &&
    path !== "/theme-boot.js" &&
    !/^\/assets\/[A-Za-z0-9_-]+\.(?:js|css)$/.test(path)
  ) {
    res.writeHead(404);
    res.end();
    return;
  }
  try {
    const file = resolve(root, path === "/" ? "index.html" : path.slice(1));
    res.writeHead(200, {
      "Content-Type": path.endsWith(".js")
        ? "text/javascript"
        : path.endsWith(".css")
          ? "text/css"
          : "text/html; charset=utf-8",
    });
    res.end(readFileSync(file));
  } catch {
    res.writeHead(404);
    res.end();
  }
});
await new Promise((r) => server.listen(0, "127.0.0.1", r));
server.unref();
const url = `http://127.0.0.1:${server.address().port}`;
import { chromium } from "playwright";
import { openDownloads } from "./navigation.mjs";
import assert from "node:assert/strict";
const browser = await chromium.launch({ channel: "msedge", headless: true });
try {
  const page = await browser.newPage({
    viewport: { width: 1440, height: 1000 },
  });
  page.setDefaultTimeout(2500);
  await page.goto(url);
  await page.getByRole("heading", { name: "总览", exact: true }).waitFor();
  assert.equal(await page.getByRole("button", { name: "下载任务", exact: true }).getAttribute("aria-expanded"), "false");
  await openDownloads(page);
  await page.locator(".tweet-card").first().waitFor();
  await page.getByRole("button", { name: "推特下载", exact: true }).waitFor();
  await page.getByPlaceholder("搜索内容、作者、文件名…").fill("海边");
  await page.waitForFunction(
    () => document.querySelectorAll(".tweet-card").length === 1,
  );
  await page.getByRole("button", { name: "设置", exact: true }).click();
  await page.getByRole("button", { name: "隐私", exact: true }).click();
  await page.getByLabel("开启隐私模式").check();
  await page.getByRole("button", { name: "推特下载", exact: true }).click();
  assert.equal(
    await page.getByPlaceholder("搜索内容、作者、文件名…").inputValue(),
    "海边",
  );
  await page.locator(".media-tile img.privacy-blurred").first().waitFor();
  assert.equal(
    await page.locator(".tweet-card .tweet-title > button").innerText(),
    "内容已折叠 · 点击展开",
  );
  await page.locator(".media-tile").first().click();
  assert.equal(await page.locator("[role=dialog]").count(), 0);
  assert.equal(
    await page.locator(".media-tile img.privacy-blurred").first().count(),
    1,
  );
  assert.equal(
    await page
      .locator(".media-tile")
      .first()
      .locator(".privacy-blurred")
      .count(),
    0,
  );
  await page.locator(".media-tile").first().click();
  await page.getByRole("dialog").waitFor();
  await page.locator(".detail-tile").click();
  await page.getByRole("button", { name: "下一个媒体", exact: true }).click();
  assert.equal(
    await page.locator(".detail-tile .privacy-blurred").count(),
    1,
    "each detail media starts masked",
  );
  await page.keyboard.press("Escape");
  await page
    .locator("nav")
    .getByRole("button", { name: "总览", exact: true })
    .click();
  await page.locator(".collection-entry").first().click();
  assert.ok(
    await page
      .locator("nav")
      .getByRole("button", { name: "总览", exact: true })
      .evaluate((e) => e.classList.contains("active")),
  );
  await page.getByPlaceholder("搜索内容、作者、文件名…").fill("山野");
  await page.getByRole("button", { name: "推特下载", exact: true }).click();
  assert.equal(
    await page.getByPlaceholder("搜索内容、作者、文件名…").inputValue(),
    "海边",
  );
  await page
    .locator("nav")
    .getByRole("button", { name: "总览", exact: true })
    .click();
  assert.equal(
    await page.getByPlaceholder("搜索内容、作者、文件名…").inputValue(),
    "山野",
  );
  await page.getByRole("button", { name: "电报下载", exact: true }).click();
  await page.getByText("电报下载暂未开发", { exact: true }).waitFor();
  assert.equal(
    await page.getByRole("button", { name: "新建下载", exact: true }).count(),
    0,
  );
  await page.getByRole("button", { name: "下载任务", exact: true }).click();
  assert.equal(
    await page.getByRole("button", { name: "推特下载", exact: true }).count(),
    0,
  );
  await page.getByRole("button", { name: "下载任务", exact: true }).click();
  await page.getByRole("button", { name: "推特下载", exact: true }).click();
  await page.locator(".media-tile img.privacy-blurred").first().waitFor();
  await page.reload();
  await openDownloads(page);
  await page.locator(".media-tile img.privacy-blurred").first().waitFor();
  await page.getByPlaceholder("搜索内容、作者、文件名…").fill("");
  await page.waitForFunction(
    () => document.querySelectorAll(".tweet-card").length > 1,
  );
  await page.getByRole("button", { name: "选择内容 123", exact: true }).click();
  await page.getByRole("button", { name: "下一组媒体", exact: true }).click();
  await page.evaluate(() => window.scrollTo(0, 550));
  const savedY = await page.evaluate(() => window.scrollY);
  assert.ok(savedY > 100);
  await page.getByRole("button", { name: "设置", exact: true }).click();
  await page.getByRole("button", { name: "隐私", exact: true }).click();
  assert.equal(await page.getByLabel("开启隐私模式").isChecked(), true);
  await page.getByRole("button", { name: "推特下载", exact: true }).click();
  await page.waitForFunction((y) => Math.abs(window.scrollY - y) < 3, savedY);
  assert.equal(
    await page
      .getByRole("button", { name: "选择内容 123", exact: true })
      .getAttribute("aria-pressed"),
    "true",
  );
  assert.equal(
    await page
      .getByRole("button", { name: "下一组媒体", exact: true })
      .isDisabled(),
    true,
  );
  const race = await browser.newPage();
  await race.addInitScript(() => {
    window.__queries = [];
    window.__saving = false;
    window.__saved = false;
    const media = (id) => ({
      id,
      media_id: String(id),
      media_type: "video",
      extension: "mp4",
      state: "completed",
      error: "",
      bytes: 1,
      width: 320,
      height: 180,
      quality: "best",
      output: "C:/mock",
      verification: "length+sha256",
    });
    const tasks = [1, 2].map((id) => ({
      id: String(id),
      author: "fixture",
      handle: "fixture",
      title: "new-route " + id,
      text: "synthetic",
      ignored: false,
      updated: 1,
      media: [media(id)],
      tags: [],
      platform: "x",
      avatar_available: false,
    }));
    const state = {
      version: "0.7.1",
      settings: {
        privacy_mode: false,
        theme: "light",
        accent: "#397863",
        username: "",
        quality: "best",
        connections: 4,
        max_mb: 4096,
        media_mode: "all",
        request_strategy: "conservative",
      },
      has_credentials: false,
      credential_error: "",
      libraries: [
        {
          id: "one",
          name: "Fixture",
          path: "C:/mock",
          quota_bytes: 0,
          default: true,
          online: true,
        },
      ],
      default_library: "one",
      tag_options: [],
      operation: null,
      busy: false,
    };
    window.pywebview = {
      api: {
        call: async (action, p = {}) => {
          if (action === "settings") {
            window.__saving = true;
            await new Promise((r) => setTimeout(r, 900));
            Object.assign(state.settings, p);
            window.__saved = true;
            return { ok: true, data: true };
          }
          if (action === "snapshot") {
            window.__queries.push(p.query || "");
            const selected = p.query ? [tasks[0]] : tasks;
            return {
              ok: true,
              data: structuredClone({
                ...state,
                tasks: selected,
                counts: {
                  all: selected.length,
                  pending: 0,
                  completed: selected.length,
                  ignored: 0,
                },
                total: selected.length,
                offset: 0,
              }),
            };
          }
          return {
            ok: true,
            data:
              action === "poll"
                ? { events: [], busy: false, operation: null }
                : null,
          };
        },
      },
    };
  });
  await race.goto(url + "?desktop=1");
  await openDownloads(race);
  await race.locator(".tweet-card").first().waitFor();
  await race.getByRole("button", { name: "设置", exact: true }).click();
  await race.getByRole("button", { name: "隐私", exact: true }).click();
  await race.getByLabel("开启隐私模式").click();
  await race.getByRole("button", { name: "推特下载", exact: true }).click();
  await race.getByPlaceholder("搜索内容、作者、文件名…").fill("new-route");
  await race.waitForFunction(() => window.__saved);
  await race.waitForTimeout(250);
  assert.equal(
    await race.evaluate(() => window.__queries.at(-1)),
    "new-route",
    "slow old action must refresh current query",
  );
  assert.equal(await race.locator(".tweet-card").count(), 1);
  await race.close();
  console.log(
    "PASS independent sections, overview library child, accordion/placeholder and persistent privacy with click-only reveal",
  );
} finally {
  await browser.close();
  server.close();
}
