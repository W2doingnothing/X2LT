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
try {
  await page.goto(url);
  await page.getByRole("button", { name: "总览", exact: true }).click();
  await page
    .getByRole("button", { name: "进入日常收藏", exact: true })
    .click({ timeout: 3000 });
  assert.equal(await page.locator(".tweet-card").count(), 2);
  await page.getByLabel("添加标签 123").fill("旅行");
  await page.getByLabel("添加标签 123").press("Enter");
  await page.getByLabel("添加标签 123").fill("参考");
  await page.getByLabel("添加标签 123").press("Enter");
  await page
    .getByRole("button", { name: "筛选标签 旅行", exact: true })
    .click();
  await page
    .getByRole("button", { name: "筛选标签 参考", exact: true })
    .click();
  assert.equal(await page.locator(".tweet-card").count(), 1);
  await page.getByLabel("搜索内容").fill("不存在");
  await page.waitForTimeout(100);
  assert.equal(await page.locator(".tweet-card").count(), 0);
  await page.getByRole("button", { name: "总览", exact: true }).first().click();
  await page
    .getByRole("button", { name: "封面 日常收藏", exact: true })
    .click();
  await page.getByLabel("媒体库 emoji").fill("🌄");
  await page.getByRole("button", { name: "保存标识", exact: true }).click();
  await page.reload();
  await page.getByRole("button", { name: "总览", exact: true }).click();
  assert.ok(
    await page
      .locator(".collection-cover")
      .first()
      .innerText()
      .then((t) => t.includes("🌄")),
  );
  assert.ok(
    await page
      .locator(".collection-card")
      .first()
      .innerText()
      .then((t) => t.includes("1 个视频 · 2 张图片")),
  );
  assert.ok(
    await page
      .locator(".collection-card")
      .nth(1)
      .innerText()
      .then((t) => t.includes("1 个视频 · 0 张图片")),
  );
  await page.getByRole("button", { name: "进入日常收藏", exact: true }).click();
  await page.getByLabel("添加标签 125").fill("共享");
  await page.getByLabel("添加标签 125").press("Enter");
  await page.getByRole("button", { name: "总览", exact: true }).first().click();
  await page.getByRole("button", { name: "进入灵感归档", exact: true }).click();
  assert.equal(await page.locator(".tweet-card").count(), 1);
  assert.equal(await page.locator(".tweet-card .media-tile").count(), 1);
  assert.ok(
    await page.getByRole("button", { name: "#共享", exact: true }).isVisible(),
  );
  await page.getByRole("button", { name: "管理标签", exact: true }).click();
  await page.getByLabel("管理原标签").selectOption("共享");
  await page.getByLabel("新标签名称").fill("跨库");
  await page
    .getByRole("button", { name: "重命名 / 合并", exact: true })
    .click();
  await page.getByRole("button", { name: "总览", exact: true }).first().click();
  await page.getByRole("button", { name: "进入日常收藏", exact: true }).click();
  assert.ok(
    await page.getByRole("button", { name: "#跨库", exact: true }).isVisible(),
  );
  await page.getByRole("button", { name: "管理标签", exact: true }).click();
  await page.getByLabel("管理原标签").selectOption("跨库");
  await page.getByRole("button", { name: "删除全部关联", exact: true }).click();
  assert.ok(
    (await page.locator(".tweet-card .tag-chips").allInnerTexts()).some((t) =>
      t.includes("#跨库"),
    ),
  );
  await page.getByRole("button", { name: "确认删除标签", exact: true }).click();
  await page.getByRole("button", { name: "未打标签", exact: true }).click();
  assert.equal(await page.locator(".tweet-card").count(), 1);
  await page.getByRole("button", { name: "总览", exact: true }).first().click();
  await page.setViewportSize({ width: 600, height: 800 });
  assert.ok(
    await page.evaluate(
      () => document.documentElement.scrollWidth <= innerWidth,
    ),
  );
  const native = await browser.newPage({
    viewport: { width: 1200, height: 900 },
  });
  await native.addInitScript(() => {
    window.__calls = [];
    window.__operation = {
      run_id: "test-run",
      phase: "reading",
      reading: true,
      discovered_tweets: 3,
      discovered_media: 5,
      queued: 2,
      processed: 0,
      successful: 0,
      failed: 0,
      skipped: 0,
      total: null,
      message: "合成读取仍在进行",
    };
    const snapshot = {
      version: "test",
      settings: {
        theme: "light",
        accent: "#397863",
        username: "",
        quality: "best",
        connections: 2,
        max_mb: 4096,
        media_mode: "all",
        request_strategy: "conservative",
      },
      has_credentials: false,
      credential_error: "",
      libraries: [
        {
          id: "one",
          name: "合成离线库",
          path: "F:\\Synthetic",
          quota_bytes: 0,
          default: true,
          online: false,
          message_count: 1,
          media_count: 1,
        },
      ],
      default_library: "one",
      tasks: [],
      counts: { all: 0, pending: 0, completed: 0, ignored: 0 },
      tag_options: [],
      total: 0,
      offset: 0,
      busy: true,
    };
    window.pywebview = {
      api: {
        async call(action, payload) {
          window.__calls.push({ action, payload });
          if (action === "snapshot")
            return {
              ok: true,
              data: { ...snapshot, operation: window.__operation },
            };
          if (action === "poll")
            return {
              ok: true,
              data: {
                events: window.__events?.splice(0) || [],
                busy: true,
                operation: window.__operation,
              },
            };
          return { ok: true, data: null };
        },
      },
    };
  });
  await native.goto(url + "/?desktop=1");
  await native.locator(".operation-status.unknown").waitFor();
  assert.equal(await native.locator(".operation-status progress").count(), 0);
  await native.getByRole("button", { name: "总览", exact: true }).click();
  for (const name of ["设置", "媒体库", "媒体工具", "总览"]) {
    await native.getByRole("button", { name, exact: true }).first().click();
    assert.equal(await native.locator(".operation-status").count(), 0, name);
  }
  await native.getByRole("button", { name: "推特下载", exact: true }).click();
  await native.locator(".operation-status").waitFor();
  await native
    .getByRole("button", { name: "总览", exact: true })
    .first()
    .click();
  await native
    .getByRole("button", { name: "进入合成离线库", exact: true })
    .click();
  assert.ok(
    await native
      .getByText("媒体库离线 · 可浏览已有记录", { exact: true })
      .isVisible(),
  );
  assert.ok(
    await native.evaluate(() =>
      window.__calls.some(
        (c) =>
          c.action === "snapshot" &&
          c.payload.library_id === "one" &&
          c.payload.filter === "completed",
      ),
    ),
  );
  await native
    .getByRole("button", { name: "总览", exact: true })
    .first()
    .click();
  await native
    .getByRole("button", { name: "封面 合成离线库", exact: true })
    .click();
  await native
    .getByRole("button", { name: "导入本地图片", exact: true })
    .click();
  assert.ok(
    await native.evaluate(() =>
      window.__calls.some(
        (c) =>
          c.action === "library_cover" &&
          c.payload.id === "one" &&
          c.payload.kind === "image" &&
          !("value" in c.payload),
      ),
    ),
  );
  await native.evaluate(() => {
    window.__operation = {
      ...window.__operation,
      phase: "failed",
      reading: false,
      total: 5,
      processed: 5,
      successful: 3,
      failed: 2,
      message: "合成容量不足",
    };
  });
  await native.getByRole("button", { name: "推特下载", exact: true }).click();
  await native.getByText("合成容量不足", { exact: true }).waitFor();
  assert.equal(await native.locator(".operation-status.unknown").count(), 0);
  assert.equal(
    await native.locator(".operation-status progress").getAttribute("value"),
    "5",
  );
  await native.getByRole("button", { name: "设置", exact: true }).click();
  assert.equal(
    await native.getByText("合成容量不足", { exact: true }).count(),
    0,
  );
  await native.emulateMedia({ reducedMotion: "reduce" });
  await native.evaluate(() => {
    window.__operation = {
      ...window.__operation,
      phase: "reading",
      reading: true,
      total: null,
    };
  });
  await native.getByRole("button", { name: "推特下载", exact: true }).click();
  await native.locator(".operation-status.unknown").waitFor();
  assert.equal(
    await native
      .locator(".operation-status")
      .evaluate((e) => getComputedStyle(e, "::before").animationName),
    "none",
  );
  await native.getByRole("button", { name: "设置", exact: true }).click();
  await native.evaluate(() => {
    window.__events = [
      { type: "message", text: "隐藏的下载消息" },
      {
        type: "operation_progress",
        scope: "organize",
        text: "脱壳",
        done: 1,
        total: 4,
      },
    ];
  });
  await native.waitForTimeout(800);
  assert.equal(
    await native.getByText("隐藏的下载消息", { exact: true }).count(),
    0,
  );
  await native.getByRole("button", { name: "媒体工具", exact: true }).click();
  await native.getByRole("button", { name: "媒体转移", exact: true }).click();
  await native.getByLabel("整理进度").waitFor();
  assert.equal(await native.getByLabel("整理进度").getAttribute("value"), "1");
  assert.equal(await native.locator(".operation-status").count(), 0);
  await native.close();
  const longPage = await browser.newPage({
    viewport: { width: 1440, height: 1000 },
  });
  await longPage.goto(url);
  await longPage.locator(".tweet-card").first().waitFor();
  const longInput = longPage.locator(".tweet-card").first().locator("input");
  for (let i = 0; i < 20; i++) {
    await longInput.fill("长" + "标签".repeat(14) + String(i));
    await longInput.press("Enter");
    await longPage.waitForFunction(
      () => document.querySelector(".tweet-card input").value === "",
    );
  }
  const cardBox = await longPage.locator(".tweet-card").first().boundingBox();
  const railBox = await longPage
    .locator(".tweet-card")
    .first()
    .locator(".message-tags")
    .boundingBox();
  assert.ok(
    railBox.y + railBox.height <= cardBox.y + cardBox.height + 1,
    "twenty long tags remain within their card",
  );
  await longPage.close();
  console.log(
    "PASS collection navigation, completed scope, shared tags AND, search, cover persistence and narrow layout",
  );
} finally {
  await browser.close();
  server.close();
}
