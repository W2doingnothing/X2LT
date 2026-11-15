// Regression: auto-fit stretched one library across the panel and the global
// inline-flex button rule placed its title beside the cover.
import http from "node:http";
import { readFileSync } from "node:fs";
import { fileURLToPath } from "node:url";
import { resolve } from "node:path";
import assert from "node:assert/strict";
import { chromium } from "playwright";

const root = fileURLToPath(new URL("../../local_video/web/", import.meta.url));
const server = http.createServer((req, res) => {
  const path = new URL(req.url, "http://localhost").pathname;
  if (
    path !== "/" &&
    path !== "/theme-boot.js" &&
    !/^\/assets\/[\w-]+\.(js|css)$/.test(path)
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
const browser = await chromium.launch({ channel: "msedge", headless: true });
try {
  const page = await browser.newPage({
    viewport: { width: 1440, height: 1000 },
  });
  await page.addInitScript(() => {
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
        privacy_mode: false,
      },
      has_credentials: false,
      credential_error: "",
      libraries: [
        {
          id: "one",
          name: "合成几何媒体库",
          path: "F:\\Synthetic",
          quota_bytes: 0,
          default: true,
          online: true,
          message_count: 1,
          media_count: 1,
          used_bytes: 0,
          available_bytes: 1024,
          cover: { kind: "image", value: "synthetic-cover" },
        },
      ],
      default_library: "one",
      tasks: [],
      counts: { all: 0, pending: 0, completed: 0, ignored: 0 },
      tag_options: [],
      total: 0,
      offset: 0,
      busy: false,
      operation: null,
    };
    window.pywebview = {
      api: {
        async call(action, payload = {}) {
          if (action === "settings") {
            Object.assign(snapshot.settings, payload);
            return { ok: true, data: true };
          }
          return {
            ok: true,
            data:
              action === "snapshot"
                ? snapshot
                : action === "cover"
                  ? "data:image/svg+xml,%3Csvg xmlns='http://www.w3.org/2000/svg' width='600' height='200'%3E%3Crect width='600' height='200' fill='teal'/%3E%3C/svg%3E"
                  : action === "poll"
                    ? { events: [], busy: false, operation: null }
                    : null,
          };
        },
      },
    };
  });
  await page.goto(`http://127.0.0.1:${server.address().port}/?desktop=1`);
  await page.getByRole("button", { name: "总览", exact: true }).click();
  await page.locator(".collection-card").waitFor();
  await page.locator(".collection-cover img").waitFor();
  const errors = [];
  for (const width of [1440, 1000, 600, 375]) {
    await page.setViewportSize({ width, height: 1000 });
    const geometry = await page.evaluate(() => {
      const box = (selector) =>
        document.querySelector(selector).getBoundingClientRect().toJSON();
      return {
        card: box(".collection-card"),
        cover: box(".collection-cover"),
        image: box(".collection-cover img"),
        title: box(".collection-entry h3"),
        heading: box(".collections-heading"),
        grid: box(".collections-grid"),
        add: box(".collections-heading button"),
        addText: document
          .querySelector(".collections-heading button")
          .textContent.trim(),
        overflow: document.documentElement.scrollWidth > innerWidth,
      };
    });
    if (geometry.card.width > 300)
      errors.push(
        `${width}: card width ${geometry.card.width} exceeds compact 300px limit`,
      );
    if (geometry.title.y < geometry.cover.bottom)
      errors.push(`${width}: title must sit below cover`);
    if (geometry.cover.width < geometry.card.width - 40)
      errors.push(`${width}: cover must span inner card`);
    if (
      Math.abs(geometry.image.width - geometry.cover.width) > 1 ||
      Math.abs(geometry.image.height - geometry.cover.height) > 1
    )
      errors.push(`${width}: cover photo must fill its cover frame`);
    if (Math.abs(geometry.add.width - geometry.add.height) > 1)
      errors.push(`${width}: add button must be square`);
    if (geometry.addText === "添加媒体库")
      errors.push(`${width}: add button must show only an icon`);
    if (geometry.grid.y - geometry.heading.bottom < 16)
      errors.push(`${width}: heading needs at least 16px separation`);
    if (geometry.overflow) errors.push(`${width}: page overflows horizontally`);
  }
  assert.deepEqual(errors, [], errors.join("\n"));
  assert.equal(
    await page.getByRole("button", { name: "添加媒体库", exact: true }).count(),
    1,
  );
  await page
    .getByRole("button", { name: "封面 合成几何媒体库", exact: true })
    .click();
  await page.getByRole("dialog", { name: "媒体库封面" }).waitFor();
  assert.ok(
    await page
      .getByRole("button", { name: "导入本地图片", exact: true })
      .isVisible(),
  );
  await page.keyboard.press("Escape");
  await page.setViewportSize({ width: 1440, height: 1000 });
  await page.getByRole("button", { name: "设置", exact: true }).click();
  await page.getByRole("button", { name: "隐私", exact: true }).click();
  await page.getByLabel("开启隐私模式").check();
  await page
    .locator("nav")
    .getByRole("button", { name: "总览", exact: true })
    .click();
  await page.locator(".collection-cover img.privacy-blurred").first().waitFor();
  const reveal = page.getByRole("button", { name: "显示封面", exact: true });
  await reveal.focus();
  await reveal.press("Enter");
  assert.equal(
    await page.locator(".collections-grid").count(),
    1,
    "first keyboard reveal does not enter library",
  );
  assert.equal(
    await page.locator(".collection-cover img.privacy-blurred").count(),
    0,
  );
  await page.locator(".collection-entry").click();
  await page.locator(".library-breadcrumb").waitFor();
  await page
    .locator("nav")
    .getByRole("button", { name: "总览", exact: true })
    .click();
  await page.locator(".collection-cover img.privacy-blurred").first().waitFor();
  await page.getByRole("button", { name: "显示封面", exact: true }).click();
  assert.equal(
    await page.locator(".collections-grid").count(),
    1,
    "first image reveal does not enter library",
  );
  console.log(
    "PASS compact single library, stacked full-width cover/title, spaced square accessible add button, cover editor, desktop/narrow geometry",
  );
} finally {
  await browser.close();
  server.close();
}
