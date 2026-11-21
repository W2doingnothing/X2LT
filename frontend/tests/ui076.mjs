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
  const first = page.locator(".tweet-card").first();
  const id = (
    await first.locator(".checkbox").getAttribute("aria-label")
  ).replace("选择内容 ", "");
  await first.click({ button: "right" });
  await page.getByRole("menuitem", { name: "移除", exact: true }).click();
  await page.getByRole("dialog", { name: "移除内容" }).waitFor();
  await page.getByRole("button", { name: "取消", exact: true }).click();
  assert.equal(await first.count(), 1);
  await page.getByRole("button", { name: "总览", exact: true }).click();
  await page.getByRole("button", { name: "进入日常收藏", exact: true }).click();
  const cards = page.locator(".collection-content-list>.tweet-card");
  await cards.first().waitFor();
  assert.ok(
    await cards
      .first()
      .getByRole("button", { name: /选择内容/ })
      .count(),
  );
  const ratios = await cards
    .locator(".media-tile")
    .evaluateAll((elements) =>
      elements.map((e) => getComputedStyle(e).aspectRatio),
    );
  assert.ok(new Set(ratios).size > 1, "media keep their own aspect ratios");
  await cards
    .first()
    .getByRole("button", { name: /选择内容/ })
    .click();
  await page.getByRole("button", { name: "批量移除", exact: true }).click();
  await page.getByLabel("以后不再询问").check();
  await page.getByRole("button", { name: "确认移除", exact: true }).click();
  await page
    .getByRole("dialog", { name: "移除内容" })
    .waitFor({ state: "hidden" });
  await page.getByRole("button", { name: "设置", exact: true }).click();
  await page.getByRole("button", { name: "媒体管理", exact: true }).click();
  await page.getByLabel("搜索过滤名单").fill(id);
  assert.equal(
    await page.getByLabel("移除前确认（单项与批量）").isChecked(),
    false,
  );
  await page.locator(".filter-item").first().waitFor();
  await page
    .locator(".filter-item")
    .first()
    .getByRole("button", { name: "取消过滤", exact: true })
    .click();
  await page.locator(".filter-item").waitFor({ state: "hidden" });
  await page.getByRole("button", { name: "总览", exact: true }).click();
  // Still in the same library, and clearing filters must not resurrect removed cards.
  assert.equal(
    await page
      .locator(
        `.collection-content-list .checkbox[aria-label="选择内容 ${id}"]`,
      )
      .count(),
    0,
  );
  assert.deepEqual(errors, []);
  const paging = await browser.newPage({
    viewport: { width: 1440, height: 1000 },
  });
  await paging.addInitScript(() => {
    const library = {
      id: "main",
      name: "分页测试",
      path: "F:\\Synthetic",
      emoji: "📁",
      quota_bytes: 0,
      default: true,
      online: true,
    };
    window.pywebview = {
      api: {
        async call(action, p = {}) {
          if (action === "snapshot") {
            const offset = Number(p.offset || 0);
            const tasks = Array.from(
              { length: Math.min(50, 65 - offset) },
              (_, i) => ({
                id: String(1000 + offset + i),
                author: "测试作者",
                handle: "fixture",
                display_name: "测试作者",
                title: "内容",
                text: "合成正文",
                ignored: false,
                updated: 0,
                media: [
                  {
                    id: 1000 + offset + i,
                    media_id: "1",
                    media_type: "video",
                    extension: "mp4",
                    state: "completed",
                    error: "",
                    bytes: 10,
                    width: 1920,
                    height: 1080,
                    quality: "best",
                    output: library.path,
                    verification: "sha256-only",
                  },
                ],
              }),
            );
            return {
              ok: true,
              data: {
                version: "test",
                settings: {
                  theme: "light",
                  accent: "#397863",
                  privacy_mode: false,
                  confirm_removal: true,
                },
                libraries: [library],
                default_library: "main",
                tasks,
                counts: { all: 65, completed: 65, pending: 0, ignored: 0 },
                total: 65,
                offset,
                busy: false,
                tag_options: [],
              },
            };
          }
          if (action === "poll")
            return { ok: true, data: { events: [], busy: false } };
          if (action === "preview" && p.job_id === 1000)
            return {
              ok: true,
              data:
                "data:image/svg+xml," +
                encodeURIComponent(
                  '<svg xmlns="http://www.w3.org/2000/svg" width="20" height="40"><rect width="20" height="40" fill="teal"/></svg>',
                ),
            };
          return { ok: true, data: null };
        },
      },
    };
  });
  await paging.goto(`http://127.0.0.1:${server.address().port}/?desktop=1`);
  await paging.locator('.checkbox[aria-label="选择内容 1000"]').waitFor();
  await paging.waitForFunction(
    () =>
      getComputedStyle(document.querySelector(".tweet-card .media-tile"))
        .aspectRatio === "20 / 40",
  );
  await paging.getByRole("button", { name: "下一页", exact: true }).click();
  await paging.locator('.checkbox[aria-label="选择内容 1050"]').waitFor();
  await paging.waitForFunction(() => scrollY === 0);
  await paging.evaluate(() => window.scrollTo(0, 600));
  await paging.waitForTimeout(900);
  assert.ok(
    await paging.evaluate(() => scrollY > 500),
    "background polling preserves scroll",
  );
  await paging.getByRole("button", { name: "上一页", exact: true }).click();
  await paging.locator('.checkbox[aria-label="选择内容 1000"]').waitFor();
  await paging.waitForFunction(() => scrollY === 0);
  await paging.close();
  console.log(
    "ui076: context removal, batch confirmation, filter clearing and adaptive previews passed",
  );
} finally {
  await browser.close();
  await new Promise((r) => server.close(r));
}
