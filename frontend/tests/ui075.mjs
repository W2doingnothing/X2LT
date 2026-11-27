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
  await page.getByRole("button", { name: "总览", exact: true }).click();
  await page.getByRole("button", { name: "进入日常收藏", exact: true }).click();
  await page.locator(".media-stack.multiple").waitFor();
  await page.screenshot({
    path: fileURLToPath(
      new URL("../../.audit/content075.png", import.meta.url),
    ),
    fullPage: true,
  });
  const cards = page.locator(".collection-content-list>.tweet-card");
  const a = await cards.nth(0).boundingBox(),
    b = await cards.nth(1).boundingBox();
  assert.ok(Math.abs(a.y - b.y) < 2 && b.x > a.x, "two cards in a row");
  const stack = page.locator(".media-stack.multiple").first();
  const waitForSlot = (index) =>
    stack.locator(".stack-controls").getByText(`${index} / 2`, { exact: true }).waitFor();
  await waitForSlot(1);
  await stack.getByRole("button", { name: "上一个预览", exact: true }).click();
  await waitForSlot(2);
  await stack.getByRole("button", { name: "下一个预览", exact: true }).click();
  await waitForSlot(1);
  await stack.focus();
  await page.keyboard.press("ArrowLeft");
  await waitForSlot(2);
  await stack.locator(".stack-front").hover();
  await page.mouse.wheel(0, 120);
  await waitForSlot(1);
  await page.waitForTimeout(200);
  const prevented = await stack.locator(".stack-front").evaluate((e) => {
    const event = new WheelEvent("wheel", {
      deltaY: 120,
      ctrlKey: true,
      bubbles: true,
      cancelable: true,
    });
    e.dispatchEvent(event);
    return event.defaultPrevented;
  });
  assert.equal(prevented, false);
  const single = page.locator(".media-stack.single").first();
  assert.equal(
    await single.locator(".stack-front").evaluate((e) => {
      const event = new WheelEvent("wheel", {
        deltaY: 120,
        bubbles: true,
        cancelable: true,
      });
      e.dispatchEvent(event);
      return event.defaultPrevented;
    }),
    false,
  );
  await page.setViewportSize({ width: 900, height: 1000 });
  const narrow = await cards.nth(1).boundingBox();
  assert.ok(narrow.y > (await cards.nth(0).boundingBox()).y, "single column");
  await page.setViewportSize({ width: 1920, height: 1000 });
  const columns = await page
    .locator(".collection-content-list")
    .evaluate((e) => getComputedStyle(e).gridTemplateColumns.split(" ").length);
  assert.equal(columns, 3);
  await page.setViewportSize({ width: 1440, height: 1000 });
  await page.getByLabel("添加标签 125").fill("堆叠测试");
  await page.getByLabel("添加标签 125").press("Enter");
  await page.waitForFunction(
    () =>
      document.querySelector('input[aria-label="添加标签 125"]')?.value === "",
  );
  await page.getByRole("button", { name: "按来源分类", exact: true }).click();
  await page
    .getByRole("button", { name: "筛选标签 堆叠测试", exact: true })
    .click();
  await page.getByRole("button", { name: "查看来源 X", exact: true }).click();
  await page.locator(".media-stack").first().waitFor();
  assert.equal(
    await page.locator(".collection-content-list>.tweet-card").count(),
    1,
    "source child inherits tags used by its count",
  );
  await page.locator("h1").click();
  await page.keyboard.press("Backspace");
  await page.getByRole("button", { name: "查看来源 X", exact: true }).waitFor();
  assert.equal(await page.getByText("归档推文", { exact: true }).count(), 0);
  assert.equal(await page.getByText("博主", { exact: true }).count(), 0);
  await page.getByRole("button", { name: "设置", exact: true }).click();
  await page.getByRole("button", { name: "隐私", exact: true }).click();
  await page.getByLabel("开启隐私模式").check();
  await page
    .locator("nav")
    .getByRole("button", { name: "总览", exact: true })
    .click();
  await page.getByRole("button", { name: "内容", exact: true }).click();
  const masked = page.locator(".media-stack.multiple").first();
  assert.equal(await masked.locator(".privacy-hint").count(), 1);
  await masked.getByRole("button", { name: "下一个预览", exact: true }).click();
  await masked.locator(".stack-controls").getByText("2 / 2", { exact: true }).waitFor();
  assert.equal(await masked.locator(".privacy-hint").count(), 1);
  assert.equal(await masked.locator(".stack-back img").count(), 0);
  await page.emulateMedia({ reducedMotion: "reduce" });
  assert.equal(
    await masked
      .locator(".stack-back")
      .first()
      .evaluate((e) => getComputedStyle(e).transitionDuration),
    "0s",
  );
  const future = await browser.newPage({
    viewport: { width: 1440, height: 1000 },
  });
  await future.addInitScript(() => {
    const media = (id, filename) => ({
      id,
      media_id: String(id),
      media_type: "video",
      extension: "mp4",
      state: "completed",
      error: "",
      bytes: 1024,
      width: 200,
      height: 400,
      quality: "best",
      output: "F:\Synthetic",
      verification: "sha256-only",
      filename,
    });
    const common = {
      author: "",
      handle: "",
      display_name: "",
      avatar_available: false,
      ignored: false,
      updated: 0,
      tags: [],
    };
    const tasks = [
      {
        ...common,
        id: "site",
        platform: "website",
        source: {
          kind: "website",
          label: "Example",
          title: "合成网页标题",
          domain: "example.test",
        },
        title: "",
        text: "网页摘要",
        media: [media(101, "web.mp4")],
      },
      {
        ...common,
        id: "file",
        platform: "local",
        title: "",
        text: "",
        media: [media(102, "本地旅行.mp4")],
      },
      {
        ...common,
        id: "channel",
        platform: "telegram",
        source: { kind: "message", label: "Telegram", title: "频道收藏" },
        title: "",
        text: "消息内容",
        media: [media(103, "channel.mp4")],
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
                libraries: [
                  {
                    id: "main",
                    name: "合成混合库",
                    path: "F:\Synthetic",
                    emoji: "📁",
                    quota_bytes: 0,
                    default: true,
                    online: true,
                    used_bytes: 1024,
                    available_bytes: 2048,
                    message_count: 3,
                    media_count: 3,
                  },
                ],
                default_library: "main",
                tasks,
                counts: { all: 3, pending: 0, completed: 3, ignored: 0 },
                total: 3,
                offset: 0,
                collection_author_count: 0,
                tag_options: [],
                busy: false,
              },
            };
          if (action === "poll")
            return { ok: true, data: { events: [], busy: false } };
          if (action === "resource_overview")
            return {
              ok: true,
              data: {
                resources: [],
                offset: 0,
                total: 0,
                video_count: 0,
                image_count: 0,
              },
            };
          return { ok: true, data: null };
        },
      },
    };
  });
  await future.goto(`http://127.0.0.1:${server.address().port}/?desktop=1`);
  await future
    .locator("nav")
    .getByRole("button", { name: "总览", exact: true })
    .click();
  await future
    .getByRole("button", { name: "进入合成混合库", exact: true })
    .click();
  await future
    .locator(".collection-content-list>.tweet-card")
    .first()
    .waitFor();
  assert.equal(
    await future
      .getByRole("button", { name: "按作者聚类", exact: true })
      .count(),
    0,
  );
  assert.equal(
    await future
      .locator(
        ".collection-content-list .avatar,.collection-content-list .author-link",
      )
      .count(),
    0,
  );
  for (const title of ["合成网页标题", "本地旅行.mp4", "频道收藏"])
    assert.ok(await future.getByText(title, { exact: true }).isVisible());
  assert.ok(
    await future.getByText("example.test", { exact: true }).isVisible(),
  );
  assert.ok(
    await future
      .locator(".tweet-card")
      .nth(1)
      .getByText("本地", { exact: true })
      .isVisible(),
  );
  await future
    .locator(".tweet-card")
    .first()
    .getByRole("button", { name: "详情", exact: true })
    .click();
  await future.getByRole("dialog").waitFor();
  assert.equal(
    await future
      .getByRole("dialog")
      .getByRole("button", { name: "查看来源", exact: true })
      .count(),
    0,
    "unsupported source never opens hardcoded X URL",
  );
  await future.keyboard.press("Escape");
  await future.close();
  assert.deepEqual(errors, []);
  console.log(
    "PASS 075 responsive content cards, grouped stack loop/wheel/keyboard/privacy, native scroll exemptions, source navigation and neutral terminology",
  );
} finally {
  await browser.close();
  server.close();
}
