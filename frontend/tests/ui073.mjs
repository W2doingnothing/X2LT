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
  page.setDefaultTimeout(3500);
  await page.goto(`http://127.0.0.1:${server.address().port}`);
  await page.locator(".tweet-card").first().waitFor();
  assert.equal(
    await page
      .getByText("把值得收藏的片段，安心留在本地。", { exact: true })
      .count(),
    0,
  );
  await page.getByRole("button", { name: "设置", exact: true }).click();
  assert.equal(await page.locator(".operation-status").count(), 0);
  assert.equal(
    await page
      .getByText("一个源重点色，自动生成两套完整的明暗配色。", { exact: true })
      .count(),
    0,
  );
  assert.equal(
    await page
      .locator(".color-picker > svg")
      .evaluate((el) => getComputedStyle(el).backgroundColor),
    "rgba(0, 0, 0, 0)",
  );
  await page.getByRole("button", { name: "隐私", exact: true }).click();
  await page.getByLabel("开启隐私模式").check();
  await page.getByRole("button", { name: "推特下载", exact: true }).click();
  const tile = page.locator(".media-tile.unfinished").first();
  assert.equal(await tile.locator(".privacy-hint").count(), 1);
  assert.equal(await tile.locator(".media-progress-ring").count(), 0);
  await tile.click();
  assert.equal(await tile.locator(".privacy-hint").count(), 0);
  assert.equal(await tile.locator(".media-progress-ring").count(), 1);
  await page.getByRole("button", { name: "总览", exact: true }).first().click();
  assert.equal(await page.getByText("当前下载", { exact: true }).count(), 0);
  assert.equal(
    await page.getByRole("button", { name: "新建下载", exact: true }).count(),
    0,
  );
  await page.getByRole("button", { name: "进入日常收藏", exact: true }).click();
  await page.getByRole("button", { name: "按博主聚类", exact: true }).click();
  await page
    .getByRole("button", { name: "星标 山野来信", exact: true })
    .click();
  await page.waitForFunction(
    () =>
      document.querySelector(".author-card strong")?.textContent === "山野来信",
  );
  await page.getByRole("button", { name: "管理分组", exact: true }).click();
  await page.getByLabel("选择博主 山野来信").check();
  await page.getByRole("button", { name: "归入其他博主", exact: true }).click();
  await page.getByRole("button", { name: "关闭弹窗", exact: true }).click();
  await page
    .getByRole("button", { name: "打开其他博主", exact: true })
    .waitFor();
  assert.equal(
    await page
      .getByRole("button", { name: "打开博主合集 山野来信", exact: true })
      .count(),
    0,
  );
  await page.getByRole("button", { name: "打开其他博主", exact: true }).click();
  await page
    .getByRole("button", { name: "取消星标 山野来信", exact: true })
    .waitFor();
  await page.getByLabel("搜索博主").fill("fieldnotes");
  await page
    .getByRole("button", { name: "打开博主合集 山野来信", exact: true })
    .click();
  await page.locator(".tweet-card").first().waitFor();
  await page.getByRole("button", { name: "返回博主列表", exact: true }).click();
  assert.equal(await page.getByLabel("搜索博主").inputValue(), "fieldnotes");
  await page.getByRole("button", { name: "管理分组", exact: true }).click();
  await page.getByLabel("选择博主 山野来信").check();
  await page.getByRole("button", { name: "移出其他博主", exact: true }).click();
  await page.getByRole("button", { name: "关闭弹窗", exact: true }).click();
  await page
    .locator(".library-breadcrumb")
    .getByRole("button", { name: "返回库博主列表", exact: true })
    .click();
  await page
    .getByRole("button", { name: "取消星标 山野来信", exact: true })
    .waitFor();
  await page.getByRole("button", { name: "撤销", exact: true }).click();
  await page
    .getByRole("button", { name: "打开其他博主", exact: true })
    .waitFor();
  await page.reload();
  await page.getByRole("button", { name: "总览", exact: true }).first().click();
  await page.getByRole("button", { name: "进入日常收藏", exact: true }).click();
  await page.getByRole("button", { name: "按博主聚类", exact: true }).click();
  await page.getByRole("button", { name: "打开其他博主", exact: true }).click();
  await page
    .getByRole("button", { name: "取消星标 山野来信", exact: true })
    .waitFor();
  console.log(
    "PASS 073 clean UI, color picker, privacy layering, star/group persistence, hierarchy and undo",
  );
} finally {
  await browser.close();
  server.close();
}
