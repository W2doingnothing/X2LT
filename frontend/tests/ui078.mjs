import http from "node:http";
import { readFileSync } from "node:fs";
import { fileURLToPath } from "node:url";
import { resolve } from "node:path";
import assert from "node:assert/strict";
import { chromium } from "playwright";
import { openDownloads } from "./navigation.mjs";
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
  await openDownloads(page);
  await page.getByRole("button", { name: "同步点赞", exact: true }).click();
  const likes = page.getByRole("dialog", { name: "同步点赞", exact: true });
  await likes.waitFor();
  assert.equal(await likes.locator("#tweet-url").count(), 0);
  await likes.getByRole("button", { name: "取消", exact: true }).click();
  await page.getByRole("button", { name: "链接下载", exact: true }).click();
  const link = page.getByRole("dialog", { name: "链接下载", exact: true });
  await link.waitFor();
  await link.getByLabel("推文链接").fill("https://x.com/example/status/123");
  assert.equal(await link.getByText("全部点赞", { exact: true }).count(), 0);
  await link.getByRole("button", { name: "取消", exact: true }).click();
  await page.addInitScript(() => {
    const library = {
      id: "main",
      name: "合成布局库",
      path: "F:\\Synthetic",
      emoji: "📁",
      online: true,
      default: true,
      quota_bytes: 0,
      media_count: 5,
      message_count: 4,
    };
    const tasks = Array.from({ length: 4 }, (_, i) => ({
      id: String(100 + i),
      author: "fixture",
      handle: "fixture",
      display_name: "测试作者",
      title: "内容",
      text: "合成内容",
      ignored: false,
      updated: 0,
      media: Array.from({ length: i === 0 ? 2 : 1 }, (_, n) => ({
        id: i * 10 + n + 1,
        media_id: String(n),
        media_type: "video",
        extension: "mp4",
        state: "completed",
        error: "",
        bytes: 100,
        width: i === 1 ? 2560 : n === 1 ? 720 : 1920,
        height: i === 1 ? 720 : n === 1 ? 1280 : 1080,
        quality: "best",
        output: library.path,
        verification: "length+sha256",
      })),
    }));
    window.__requests = [];
    window.pywebview = {
      api: {
        async call(action, p = {}) {
          window.__requests.push({ action, p });
          if (action === "snapshot")
            return {
              ok: true,
              data: {
                version: "test",
                settings: {
                  theme: "light",
                  accent: "#397863",
                  privacy_mode: false,
                  username: "fixture",
                  confirm_removal: true,
                },
                has_credentials: true,
                credential_error: "",
                libraries: [library],
                default_library: "main",
                tasks,
                counts: { all: 4, completed: 4, pending: 0, ignored: 0 },
                total: 4,
                offset: 0,
                busy: false,
                tag_options: [],
              },
            };
          if (action === "poll")
            return { ok: true, data: { events: [], busy: false } };
          if (action === "resource_overview")
            return {
              ok: true,
              data: {
                resources: [],
                total: 0,
                offset: 0,
                video_count: 5,
                image_count: 0,
              },
            };
          return { ok: true, data: null };
        },
      },
    };
  });
  await page.goto(`http://127.0.0.1:${server.address().port}/?desktop=1`);
  await openDownloads(page);
  await page.getByRole("button", { name: "同步点赞", exact: true }).click();
  const sync = page.getByRole("dialog", { name: "同步点赞", exact: true });
  await sync.getByLabel("同步范围").selectOption("all");
  await sync.getByRole("button", { name: "开始同步", exact: true }).click();
  await sync.waitFor({ state: "hidden" });
  assert.deepEqual(
    await page.evaluate(
      () => window.__requests.filter((r) => r.action === "start").at(-1).p,
    ),
    { mode: "all", library_id: "main" },
  );
  await page.getByRole("button", { name: "链接下载", exact: true }).click();
  await page.getByLabel("推文链接").fill("https://x.com/fixture/status/123");
  await page.getByLabel("推文链接").press("Enter");
  await page
    .getByRole("dialog", { name: "链接下载", exact: true })
    .waitFor({ state: "hidden" });
  assert.equal(
    await page.evaluate(
      () => window.__requests.filter((r) => r.action === "start").at(-1).p.mode,
    ),
    "link",
  );
  await page.getByRole("button", { name: "总览", exact: true }).click();
  await page
    .getByRole("button", { name: "进入合成布局库", exact: true })
    .click();
  const cards = page.locator(".collection-content-list>.tweet-card");
  await cards.nth(2).waitFor();
  const stack = page.locator(".media-stack.multiple").first();
  const nextPreview = stack.getByRole("button", { name: "下一个预览", exact: true });
  await nextPreview.scrollIntoViewIfNeeded();
  await page.waitForTimeout(400);
  const cardBoxes = () => cards.evaluateAll((es) =>
    es.map((e) => {
      const r = e.getBoundingClientRect();
      return { x: r.x, y: r.y, bottom: r.bottom, right: r.right };
    }),
  );
  const boxes = await cardBoxes();
  const first = boxes[0],
    second = boxes[1],
    third = boxes[2];
  assert.ok(
    Math.abs(first.y - second.y) < 3 && second.x > first.x,
    "first row has two columns",
  );
  assert.ok(
    third.y < Math.max(first.bottom, second.bottom) - 20,
    "third card fills the shorter column",
  );
  for (let i = 0; i < boxes.length; i++)
    for (let j = i + 1; j < boxes.length; j++) {
      const a = boxes[i],
        b = boxes[j];
      assert.ok(
        a.right <= b.x + 1 ||
          b.right <= a.x + 1 ||
          a.bottom <= b.y + 1 ||
          b.bottom <= a.y + 1,
        "cards do not overlap",
      );
    }
  const tile = stack.locator(".media-tile:visible");
  const frame = await tile.boundingBox();
  assert.ok(Math.abs(frame.width / frame.height - 4 / 3) < 0.01, "multi-item X preview uses a 4:3 frame");
  await nextPreview.click();
  await stack.locator(".stack-controls").getByText("2 / 2", { exact: true }).waitFor();
  assert.deepEqual(await tile.boundingBox(), frame, "switching keeps the X preview frame in place");
  assert.deepEqual(await cardBoxes(), boxes, "switching keeps the surrounding cards in place");
  await page.screenshot({
    path: fileURLToPath(new URL("../../.audit/layout078.png", import.meta.url)),
    fullPage: true,
  });
  await page.setViewportSize({ width: 900, height: 800 });
  await page.waitForTimeout(300);
  const xs = await cards.evaluateAll((es) =>
    es.map((e) => Math.round(e.getBoundingClientRect().x)),
  );
  assert.equal(new Set(xs).size, 1, "narrow library becomes one column");
  assert.deepEqual(errors, []);
  console.log(
    "PASS 078 distinct likes/link entry points, natural-height masonry, resizing and no overlap",
  );
} finally {
  await browser.close();
  await new Promise((r) => server.close(r));
}
