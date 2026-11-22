import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import http from "node:http";
import { resolve } from "node:path";
import { fileURLToPath } from "node:url";
import { chromium } from "playwright";

const root = fileURLToPath(new URL("../../local_video/web/", import.meta.url));
const server = http.createServer((request, response) => {
  const path = new URL(request.url, "http://127.0.0.1").pathname;
  if (
    path !== "/" &&
    path !== "/theme-boot.js" &&
    !/^\/assets\/[\w-]+\.(js|css)$/.test(path)
  ) {
    response.writeHead(404).end();
    return;
  }
  try {
    response.setHeader(
      "Content-Type",
      path.endsWith(".js")
        ? "text/javascript"
        : path.endsWith(".css")
          ? "text/css"
          : "text/html; charset=utf-8",
    );
    response.end(
      readFileSync(resolve(root, path === "/" ? "index.html" : path.slice(1))),
    );
  } catch {
    response.writeHead(404).end();
  }
});
await new Promise((done) => server.listen(0, "127.0.0.1", done));
let browser;
try {
  browser = await chromium.launch({ channel: "msedge", headless: true });
  const page = await browser.newPage({
    viewport: { width: 1320, height: 960 },
  });
  page.setDefaultTimeout(5000);
  const errors = [];
  page.on("pageerror", (error) => errors.push(error.message));
  await page.addInitScript(() => {
    const library = {
      id: "main",
      name: "测试媒体库",
      path: "F:\\Fixture",
      online: true,
      default: true,
      quota_bytes: 0,
    };
    const other = {
      ...library,
      id: "other",
      name: "其他媒体库",
      default: false,
    };
    const image = {
      id: 8,
      title: "旅行样张",
      kind: "image",
      bytes: 128,
      tags: [],
      libraries: ["main"],
      locations: [{ id: 9, state: "available", label: "原件", managed: false }],
    };
    const comic = { ...image, id: 12, title: "夜航漫画", kind: "comic" };
    const png =
      "data:image/png;base64,iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+a1ioAAAAASUVORK5CYII=";
    const job = {
      id: "import-1",
      stage: "preview",
      total: 2,
      done: 2,
      duplicate: 1,
      bytes: 256,
      failed: 0,
      items: [
        {
          id: "unique",
          title: "旅行样张",
          kind: "image",
          bytes: 128,
          duplicate: false,
        },
        {
          id: "duplicate",
          title: "重复图片",
          kind: "image",
          bytes: 128,
          duplicate: true,
        },
      ],
    };
    let stored = false;
    window.requests = [];
    const response = (data) => ({ ok: true, data });
    window.pywebview = {
      api: {
        async call(action, payload = {}) {
          window.requests.push({ action, payload });
          if (action === "snapshot")
            return response({
              version: "fixture",
              settings: {
                theme: "light",
                accent: "#397863",
                username: "fixture",
                privacy_mode: false,
                confirm_removal: true,
              },
              has_credentials: true,
              credential_error: "",
              libraries: [library, other],
              default_library: library.id,
              tasks: [],
              counts: { all: 0, completed: 0, pending: 0, ignored: 0 },
              total: 0,
              offset: 0,
              busy: false,
              tag_options: [],
            });
          if (action === "poll") return response({ events: [], busy: false });
          if (action === "resource_overview")
            return response({
              resources: [],
              total: 0,
              offset: 0,
              video_count: 0,
              image_count: 0,
            });
          if (action === "local_pick")
            return response({ id: "chosen-1", names: ["图片文件夹"] });
          if (action === "local_scan") return response({ id: job.id });
          if (action === "local_import_status") return response(job);
          if (action === "local_import_start") {
            Object.assign(job, {
              stage: "failed",
              failed: 1,
              retryable: 1,
              failures: [
                { id: "unique", title: "旅行样张", error: "文件暂时不可用" },
              ],
            });
            return response(true);
          }
          if (action === "local_import_retry") {
            stored = true;
            Object.assign(job, {
              stage: "completed",
              failed: 0,
              retryable: 0,
              failures: [],
            });
            return response(true);
          }
          if (action === "local_library") {
            const items = (stored ? [image, comic] : []).filter(
              (item) =>
                item.libraries.includes(payload.library_id) &&
                (!payload.query || item.title.includes(payload.query)),
            );
            return response({ items, total: items.length, offset: 0 });
          }
          if (action === "local_media_preview") return response(png);
          if (action === "local_media_tags") {
            image.tags = payload.tags;
            return response(true);
          }
          if (action === "local_media_link") {
            image.libraries.push(payload.library_id);
            return response(true);
          }
          if (action === "local_media_remove") {
            image.libraries = image.libraries.filter(
              (lib) => lib !== payload.library_id,
            );
            return response(true);
          }
          return response(null);
        },
      },
    };
  });
  await page.goto(`http://127.0.0.1:${server.address().port}/?desktop=1`);
  const request = (name) =>
    page.evaluate(
      (action) =>
        window.requests.filter((row) => row.action === action).at(-1)?.payload,
      name,
    );
  await page.getByRole("button", { name: "总览", exact: true }).click();
  await page.getByRole("button", { name: "进入测试媒体库" }).click();
  await page.getByRole("button", { name: "导入本地" }).click();
  const importer = page.getByRole("dialog", { name: "导入到 测试媒体库" });
  await importer.getByLabel("导入来源").selectOption("folder");
  await importer.getByRole("button", { name: "选择文件夹" }).click();
  assert.deepEqual(await request("local_pick"), {
    kind: "media",
    selection: "folder",
  });
  await importer.getByRole("button", { name: "扫描", exact: true }).click();
  await importer.getByText("确认导入", { exact: true }).waitFor();
  assert.deepEqual(await request("local_scan"), {
    selection: "chosen-1",
    library_id: "main",
    kind: "media",
    storage: "reference",
    recursive: true,
    batch: false,
  });
  await importer
    .getByText("重复图片", { exact: true })
    .locator("..")
    .getByRole("checkbox")
    .uncheck();
  await importer.getByRole("button", { name: "导入 1 项" }).click();
  await importer.getByText("导入失败", { exact: true }).waitFor();
  assert.deepEqual(await request("local_import_start"), {
    id: "import-1",
    ids: ["unique"],
  });
  await importer.getByRole("button", { name: "重试此项" }).click();
  await importer.getByText("导入完成", { exact: true }).waitFor();
  assert.deepEqual(await request("local_import_retry"), {
    id: "import-1",
    item_id: "unique",
  });
  await page.keyboard.press("Backspace");
  await importer.waitFor({ state: "hidden" });
  const local = page.locator(".local-library");
  await local.getByText("夜航漫画", { exact: true }).waitFor();
  await page.getByRole("button", { name: "本地导入", exact: true }).click();
  const search = page.getByRole("textbox", { name: "搜索内容" });
  await search.fill("夜航");
  await local.getByText("夜航漫画", { exact: true }).waitFor();
  await local
    .getByText("旅行样张", { exact: true })
    .waitFor({ state: "hidden" });
  assert.equal((await request("local_library")).query, "夜航");
  await search.fill("");
  const card = local.locator("article").filter({ hasText: "旅行样张" });
  await card.getByRole("button", { name: "图片 · 打开" }).click();
  const picture = page.getByRole("dialog", { name: "图片预览" });
  await picture.getByRole("img", { name: "本地图片" }).waitFor();
  assert.deepEqual(await request("local_media_preview"), {
    id: 8,
    large: true,
  });
  await picture.getByRole("button", { name: "关闭弹窗" }).click();
  await card.getByRole("button", { name: "本地媒体详情" }).click();
  const details = page.getByRole("dialog", { name: "图片详情" });
  await details.getByRole("textbox", { name: "标签" }).fill("#旅行，假日");
  await details.getByRole("button", { name: "保存标签" }).click();
  await details.getByText("标签已保存").waitFor();
  assert.deepEqual(await request("local_media_tags"), {
    id: 8,
    tags: ["旅行", "假日"],
  });
  await details
    .getByRole("combobox", { name: "添加到其他媒体库" })
    .selectOption("other");
  await details.getByRole("button", { name: "添加引用" }).click();
  await details.getByText("已添加到媒体库").waitFor();
  assert.deepEqual(await request("local_media_link"), {
    id: 8,
    library_id: "other",
    linked: true,
  });
  assert.equal(
    await details.getByRole("button", { name: "移入回收站" }).count(),
    0,
  );
  await details.getByRole("button", { name: "从当前媒体库移除" }).click();
  await details.getByRole("button", { name: "确认", exact: true }).click();
  await card.waitFor({ state: "hidden" });
  assert.deepEqual(await request("local_media_remove"), {
    id: 8,
    library_id: "main",
    recycle: false,
  });
  assert.deepEqual(errors, []);
  console.log("本地导入、预览与跨库引用通过");
} finally {
  await browser?.close();
  await new Promise((done) => server.close(done));
}
