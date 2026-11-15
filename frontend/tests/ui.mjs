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
import assert from "node:assert/strict";
const browser = await chromium.launch({ channel: "msedge", headless: true });
const page = await browser.newPage({ viewport: { width: 1440, height: 1000 } });
const errors = [];
page.on("pageerror", (e) => errors.push(e.message));
try {
  await page.goto(url);
  await page.locator(".tweet-card").first().waitFor();
  await page.getByRole("button", { name: "设置", exact: true }).click();
  await page.getByRole("textbox", { name: "十六进制主题色" }).fill("#ff0066");
  await page.getByRole("button", { name: "应用颜色", exact: true }).click();
  await page.waitForTimeout(250);
  assert.equal(
    await page.getByRole("textbox", { name: "十六进制主题色" }).inputValue(),
    "#ff0066",
  );
  assert.ok(
    await page
      .locator(".source-preview")
      .innerText()
      .then((t) => t.includes("#FF0066")),
  );

  await page.getByRole("button", { name: /^深色/ }).click();
  await page.waitForTimeout(250);
  assert.equal(await page.locator("html").getAttribute("data-theme"), "dark");
  await page.getByRole("button", { name: "媒体库", exact: true }).click();
  assert.equal(await page.locator(".library-card").count(), 2);
  await page.getByRole("button", { name: "添加媒体库", exact: true }).click();
  await page
    .getByRole("textbox", { name: "库名称", exact: true })
    .fill("视频收藏库");
  await page.getByRole("textbox", { name: "媒体库路径" }).fill("F:\\Video");
  await page.getByRole("spinbutton", { name: "最大存储量" }).fill("25");
  await page.getByRole("button", { name: "保存媒体库", exact: true }).click();
  await page.waitForTimeout(200);
  assert.equal(await page.locator(".library-card").count(), 3);

  await page
    .locator(".library-card")
    .last()
    .getByRole("button", { name: "设为默认库" })
    .click();
  await page.reload();
  await page.locator(".app").waitFor();
  await page.getByRole("button", { name: "设置", exact: true }).click();
  await page.getByRole("button", { name: "媒体库", exact: true }).click();
  assert.equal(await page.locator(".library-card").count(), 3);
  assert.match(
    await page.locator(".library-card").last().innerText(),
    /默认库/,
  );
  await page.getByRole("button", { name: "编辑 视频收藏库" }).click();
  await page.getByRole("spinbutton", { name: "最大存储量" }).fill("12.5");
  await page.getByRole("button", { name: "保存媒体库", exact: true }).click();
  await page.waitForTimeout(150);
  assert.match(
    await page.locator(".library-card").last().innerText(),
    /12.5 GB/,
  );
  await page
    .locator(".library-card")
    .last()
    .getByRole("button", { name: "模拟断开", exact: true })
    .click();
  assert.match(
    await page.locator(".library-card").last().innerText(),
    /路径不可用/,
  );
  await page.getByRole("button", { name: "移除 视频收藏库" }).click();
  await page.getByRole("button", { name: "移除管理关系" }).click();
  await page.waitForTimeout(150);
  assert.equal(await page.locator(".library-card").count(), 2);
  await page.getByRole("button", { name: "外观", exact: true }).click();
  await page.getByRole("button", { name: "恢复默认", exact: true }).click();
  await page.getByRole("button", { name: "浅色", exact: true }).click();
  await page
    .getByRole("button", { name: /下载任务/ })
    .first()
    .click();
  await page.getByRole("button", { name: "下一组媒体", exact: true }).click();
  await page.getByRole("button", { name: "详情", exact: true }).first().click();
  await page.getByRole("button", { name: "下一个媒体", exact: true }).click();
  await page.keyboard.press("Escape");
  await page.getByRole("button", { name: "选择推文 123", exact: true }).click();
  await page.getByRole("button", { name: "批量忽略", exact: true }).click();
  await page.waitForTimeout(150);
  assert.equal(await page.locator(".tweet-card").count(), 2);
  await page.getByRole("button", { name: "重置演示", exact: true }).click();
  await page.waitForTimeout(200);
  await page.setViewportSize({ width: 1040, height: 800 });
  assert.ok(
    await page.evaluate(
      () => document.documentElement.scrollWidth <= innerWidth,
    ),
  );
  const native = await browser.newPage({
    viewport: { width: 1220, height: 900 },
  });
  await native.addInitScript(() => {
    const settings = {
      theme: "light",
      accent: "#397863",
      username: "",
      quality: "best",
      connections: 4,
      max_mb: 4096,
      media_mode: "all",
      request_strategy: "conservative",
    };
    const tasks = Array.from({ length: 40 }, (_, j) => ({
      id: String(1000 + j),
      author: "synthetic",
      avatar_available: true,
      handle: "synthetic",
      title: "合成预览 " + j,
      text: "合成内容",
      ignored: false,
      updated: 1,
      media: Array.from({ length: 3 }, (_, i) => ({
        id: 100 * j + i + 1,
        media_id: String(i),
        media_type: "video",
        extension: "mp4",
        state: "pending",
        error: "",
        bytes: 0,
        width: 320,
        height: 180,
        quality: "best",
        output: "F:\\Synthetic",
        verification: "",
      })),
    }));
    const snapshot = {
      version: "test",
      settings,
      has_credentials: false,
      credential_error: "",
      libraries: [
        {
          id: "one",
          name: "合成库",
          path: "F:\\Synthetic",
          quota_bytes: 0,
          default: true,
          online: true,
          used_bytes: 0,
          disk_free: 100000000,
          available_bytes: 100000000,
        },
      ],
      default_library: "one",
      tasks,
      counts: { all: 40, pending: 40, completed: 0, ignored: 0 },
      total: 40,
      offset: 0,
      busy: false,
    };
    window.__previewRequests = 0;
    window.__activePreviews = 0;
    window.__maxPreviews = 0;
    window.pywebview = {
      api: {
        async call(action, payload) {
          if (action === "snapshot") return { ok: true, data: snapshot };
          if (action === "poll")
            return { ok: true, data: { events: [], busy: false } };
          if (action === "preview" || action === "avatar") {
            window.__previewRequests++;
            window.__activePreviews++;
            window.__maxPreviews = Math.max(
              window.__maxPreviews,
              window.__activePreviews,
            );
            await new Promise((r) => setTimeout(r, 50));
            window.__activePreviews--;
            return {
              ok: true,
              data: "data:image/png;base64,iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+jrrkAAAAASUVORK5CYII=",
            };
          }
          return { ok: true, data: true };
        },
      },
    };
  });
  await native.goto(url + "/?desktop=1");
  await native.locator(".tweet-card").first().locator("img").first().waitFor();
  await native.waitForTimeout(700);
  assert.equal(await native.locator(".tweet-card").count(), 40);
  assert.ok(await native.evaluate(() => window.__previewRequests < 20));
  assert.ok(await native.evaluate(() => window.__maxPreviews <= 2));
  await native.close();
  assert.deepEqual(errors, []);
  console.log(
    "PASS: HEX live preview, contrast themes, multi library add/edit/quota/default/persistence/offline/remove, carousel, details, batch ignore, desktop layout; no JS errors.",
  );
} finally {
  await browser.close();
  server.close();
}
