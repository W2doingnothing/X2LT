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
    viewport: { width: 1440, height: 800 },
    reducedMotion: "reduce",
  });
  page.setDefaultTimeout(3000);
  await page.goto(`http://127.0.0.1:${server.address().port}`);
  await openDownloads(page);
  await page.locator(".tweet-card").first().waitFor();
  const top = page.getByRole("button", { name: "回到顶部", exact: true });
  const bottom = page.getByRole("button", { name: "前往底部", exact: true });
  await bottom.waitFor();
  assert.equal(await top.isDisabled(), true);
  await bottom.click();
  await page.waitForFunction(
    () =>
      window.scrollY > 0 &&
      Math.abs(document.documentElement.scrollHeight - innerHeight - scrollY) <
        3,
  );
  await page.waitForFunction(
    () => document.querySelector('[aria-label="前往底部"]').disabled,
  );
  await top.click();
  await page.waitForFunction(() => scrollY < 2);
  const title = page.locator(".tweet-card").first().locator(".author-heading");
  assert.match(await title.innerText(), /山野来信/);
  assert.match(await title.innerText(), /@fieldnotes/);
  assert.match(
    await page
      .locator(".tweet-card")
      .first()
      .locator(".tweet-body")
      .innerText(),
    /把周末留给山野/,
  );
  const bodyToggle = page
    .locator(".tweet-card")
    .first()
    .getByRole("button", { name: "展开正文" });
  await bodyToggle.click();
  assert.equal(
    await page
      .locator(".tweet-card")
      .first()
      .getByRole("button", { name: "收起正文" })
      .getAttribute("aria-expanded"),
    "true",
  );
  const ring = page.locator(".media-progress-ring").first();
  const styles = await ring.evaluate((el) => ({
    shadow: getComputedStyle(el).boxShadow,
    strokes: [...el.querySelectorAll("circle")].map(
      (c) => getComputedStyle(c).stroke,
    ),
  }));
  assert.equal(styles.shadow, "none");
  assert.notEqual(styles.strokes[0], styles.strokes[1]);
  const badge = await page
    .locator(".media-type")
    .first()
    .evaluate((el) => ({
      bg: getComputedStyle(el).backgroundColor,
      pad: getComputedStyle(el).paddingLeft,
    }));
  assert.match(badge.bg, /rgba\(0, 0, 0, 0\.6\)/);
  assert.ok(parseFloat(badge.pad) >= 7);
  for (const theme of ["light", "dark"]) {
    await page.evaluate((theme) => {
      document.documentElement.dataset.theme = theme;
    }, theme);
    for (const selector of [".media-type", ".media-done", ".media-size"]) {
      const bg = await page
        .locator(selector)
        .first()
        .evaluate((el) => getComputedStyle(el).backgroundColor);
      assert.equal(bg, "rgba(0, 0, 0, 0.6)", `${theme} ${selector}`);
    }
  }
  await page.getByPlaceholder("搜索内容、作者、文件名…").fill("不会匹配任何推文-072");
  await page.waitForFunction(
    () => document.querySelectorAll(".tweet-card").length === 0,
  );
  // Empty pages may retain a small layout overflow; neither direction may move past its boundary.
  await page.evaluate(() => window.scrollTo(0, 0));
  await page.setViewportSize({ width: 1440, height: 3000 });
  await page.waitForFunction(
    () => !document.querySelector(".scroll-navigation"),
  );
  await page.setViewportSize({ width: 1440, height: 900 });
  await page.getByRole("button", { name: "总览", exact: true }).first().click();
  await page.getByRole("button", { name: "进入日常收藏", exact: true }).click();
  assert.equal(await page.getByText("当前下载", { exact: true }).count(), 0);
  assert.equal(await page.getByRole("button", { name: /^未完成/ }).count(), 0);
  assert.equal(await page.locator(".media-tile.unfinished").count(), 0);
  await page.getByLabel("添加标签 123").fill("聚类筛选");
  await page.getByLabel("添加标签 123").press("Enter");
  await page.getByRole("button", { name: "按作者聚类", exact: true }).click();
  await page.locator(".author-card").first().waitFor();
  assert.equal(await page.locator(".author-card").count(), 2);
  await page.getByLabel("搜索作者").fill("fieldnotes");
  await page
    .getByRole("button", { name: "筛选标签 聚类筛选", exact: true })
    .click();
  await page.waitForFunction(
    () => document.querySelectorAll(".author-card").length === 1,
  );
  await page
    .getByRole("button", { name: "打开作者合集 山野来信", exact: true })
    .click();
  await page.locator(".tweet-card").first().waitFor();
  assert.equal(await page.locator(".tweet-card").count(), 1);
  assert.equal(await page.locator(".media-tile").count(), 1);
  assert.equal(
    await page
      .getByRole("button", { name: "筛选标签 聚类筛选", exact: true })
      .getAttribute("aria-pressed"),
    "true",
  );
  await page.getByRole("button", { name: "返回作者列表", exact: true }).click();
  assert.equal(await page.getByLabel("搜索作者").inputValue(), "fieldnotes");
  await page.getByRole("button", { name: "设置", exact: true }).click();
  await page
    .locator("aside nav")
    .getByRole("button", { name: "总览", exact: true })
    .click();
  await page.locator(".author-card").first().waitFor();
  assert.equal(await page.getByLabel("搜索作者").inputValue(), "fieldnotes");
  await page.close();
  console.log(
    "PASS 072 author header/body, progress contrast, badges and scroll boundaries",
  );
} finally {
  await browser.close();
  server.close();
}
