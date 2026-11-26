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
      locations: [{ id: 9, state: "available", label: "原件", managed: false, manageable: true }],
    };
    const comic = { ...image, id: 12, title: "夜航漫画", kind: "comic" };
    const video = { ...image, id: 18, title: "旅行片段", kind: "video" };
    const photo = { ...image, id: 20, title: "山谷剪影" };
    const png =
      "data:image/png;base64,iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+a1ioAAAAASUVORK5CYII=";
    const portrait =
      "data:image/svg+xml," + encodeURIComponent('<svg xmlns="http://www.w3.org/2000/svg" width="180" height="320"><rect width="180" height="320" fill="#397863"/></svg>');
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
    let group = null;
    let previousGroup = null;
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
          if (action === "library_update") {
            Object.assign(library, payload);
            return response(library);
          }
          if (action === "local_library_scan") {
            return response({ id: job.id });
          }
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
            if (new URLSearchParams(location.search).has("telegram_fixture")) {
              const first = { ...image, id: 34 };
              const second = { ...video, id: 35 };
              const items = [
                { ...first, body: "合成转发正文", telegram_items: [first, second],
                  telegram: { content_id: 72, channel: "当前扫描频道", has_url: true, sent_at: 1760000000,
                    forward: { id: "-1000000000042", title: "真正的源频道", url: "", avatar_key: "a".repeat(64) } } },
                { ...photo, body: "合成普通正文", telegram_items: [photo],
                  telegram: { content_id: 73, channel: "当前扫描频道", has_url: true, sent_at: 1760000100, forward: null } },
              ];
              return response({ items, total: items.length, offset: 0 });
            }
            const items = (stored
              ? [comic, ...(group ? [{ ...image, group }] : [image, video, photo])]
              : []).filter(
              (item) =>
                item.libraries.includes(payload.library_id) &&
                (!payload.query || [item.title, item.body].some((text) => text?.includes(payload.query))),
            );
            return response({ items, total: items.length, offset: 0 });
          }
          if (action === "local_group_change") {
            if (payload.operation === "undo") {
              group = previousGroup;
            } else {
              previousGroup = group;
              if (payload.operation === "merge")
                group = { id: 8, title: "旅行合集", cover_id: 8, items: [image, video, photo] };
              if (payload.operation === "edit")
                group = { ...group, title: payload.title, body: payload.body, cover_id: payload.cover_id, items: payload.order.map(id => group.items.find(item => item.id === id)) };
              if (payload.operation === "dissolve") group = null;
            }
            return response({ undo: payload.operation === "undo" ? undefined : "fixture-token" });
          }
          if (action === "local_media_edit") {
            const item = [image, comic, video, photo].find((row) => row.id === payload.id);
            Object.assign(item, { title: payload.title, body: payload.body });
            return response(true);
          }
          if (action === "local_file_manage") return response(true);
          if (action === "local_media_preview") return response(payload.id === 18 ? portrait : png);
          if (action === "tg_forward_avatar") return response({ url: png });
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
  const libraryCard = page
    .locator(".collection-card")
    .filter({ has: page.getByRole("button", { name: "进入测试媒体库" }) });
  await libraryCard.getByRole("button", { name: "编辑信息" }).click();
  const editLibrary = page.getByRole("dialog", { name: "编辑媒体库" });
  await editLibrary.getByLabel("扫描模式").selectOption("comic");
  await editLibrary.getByRole("button", { name: "保存媒体库" }).click();
  await editLibrary.waitFor({ state: "hidden" });
  assert.equal((await request("library_update")).scan_mode, "comic");
  await libraryCard.getByRole("button", { name: "扫描此库" }).click();
  const scanLibrary = page.getByRole("dialog", { name: "扫描 测试媒体库" });
  await scanLibrary.getByLabel("将根目录的散图合成一本漫画").check();
  await scanLibrary.getByRole("button", { name: "开始扫描" }).click();
  await scanLibrary.getByText("确认导入", { exact: true }).waitFor();
  assert.deepEqual(await request("local_library_scan"), {
    library_id: "main",
    include_loose: true,
  });
  assert.equal(await request("local_pick"), undefined);
  await scanLibrary.getByRole("button", { name: "关闭弹窗" }).click();
  await scanLibrary.waitFor({ state: "hidden" });
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
  const videoCard = local.locator("article").filter({ hasText: "旅行片段" });
  await videoCard.locator(".local-preview img").waitFor();
  assert.equal(await videoCard.getAttribute("draggable"), "false");
  assert.equal(await videoCard.getByRole("button", { name: "编辑卡片信息" }).count(), 0);
  await page.waitForFunction(() => {
    const video = Array.from(document.querySelectorAll(".local-card")).find((card) =>
      card.textContent.includes("旅行片段"));
    const frame = video?.querySelector(".local-preview");
    if (!frame) return false;
    const { width, height } = frame.getBoundingClientRect();
    return height > width && getComputedStyle(frame.querySelector("img")).objectFit === "contain";
  });
  assert.ok(
    (await videoCard.locator(".local-card-title").boundingBox()).y <
    (await videoCard.locator(".local-preview").boundingBox()).y,
  );
  await local.getByRole("button", { name: "管理模式", exact: true }).click();
  assert.equal(await videoCard.getAttribute("draggable"), "true");
  await videoCard.getByRole("button", { name: "编辑卡片信息" }).click();
  const videoDetails = page.getByRole("dialog", { name: "视频详情" });
  await videoDetails.getByRole("textbox", { name: "标题", exact: true }).fill("旅行片段（已编辑）");
  await videoDetails.getByRole("textbox", { name: "正文", exact: true }).fill("峡谷全景\n黄昏光线");
  await videoDetails.getByRole("button", { name: "保存卡片信息" }).click();
  assert.deepEqual(await request("local_media_edit"), {
    library_id: "main", id: 18, title: "旅行片段（已编辑）", body: "峡谷全景\n黄昏光线",
  });
  await videoDetails.getByRole("button", { name: "关闭弹窗" }).click();
  await videoCard.getByText("峡谷全景 黄昏光线").waitFor();
  await search.fill("黄昏光线");
  await card.waitFor({ state: "hidden" });
  await videoCard.waitFor();
  await search.fill("");
  await videoCard.getByRole("checkbox").check();
  await search.fill("山谷");
  await videoCard.waitFor({ state: "hidden" });
  await local.locator("article").filter({ hasText: "山谷剪影" }).getByRole("checkbox").check();
  await search.fill("");
  assert.equal(await videoCard.getByRole("checkbox").isChecked(), true);
  await videoCard.locator(".local-preview").dragTo(card.locator(".local-card-title"));
  assert.deepEqual(await request("local_group_change"), {
    library_id: "main", operation: "merge", ids: [18, 20], target_id: 8,
  });
  const collection = local.locator("article").filter({ hasText: "旅行合集" });
  await collection.getByRole("button", { name: "编辑卡片信息" }).click();
  const grouping = page.getByRole("dialog", { name: "媒体集合" });
  await grouping.getByRole("textbox", { name: "集合名称" }).fill("旅行作品");
  await grouping.getByRole("textbox", { name: "正文", exact: true }).fill("一次旅行的照片和视频");
  await grouping.getByText("旅行片段（已编辑）", { exact: true }).locator("..").getByRole("button", { name: "设为封面" }).click();
  await grouping.getByText("山谷剪影", { exact: true }).locator("..").getByRole("button", { name: "上移" }).click();
  await grouping.getByRole("button", { name: "保存集合" }).click();
  assert.deepEqual(await request("local_group_change"), {
    library_id: "main", operation: "edit", group_id: 8,
    title: "旅行作品", body: "一次旅行的照片和视频", cover_id: 18, order: [8, 20, 18],
  });
  await grouping.getByRole("button", { name: "解散集合", exact: true }).click();
  await grouping.getByRole("button", { name: "确认解散" }).click();
  await grouping.waitFor({ state: "hidden" });
  await card.waitFor();
  await local.getByRole("button", { name: "撤销集合操作" }).click();
  const restored = local.locator("article").filter({ hasText: "旅行作品" });
  await restored.getByText("一次旅行的照片和视频").waitFor();
  await restored.getByRole("button", { name: "编辑卡片信息" }).click();
  await grouping.getByRole("button", { name: "解散集合", exact: true }).click();
  await grouping.getByRole("button", { name: "确认解散" }).click();
  await grouping.waitFor({ state: "hidden" });
  await card.waitFor();
  await card.getByRole("button", { name: "编辑卡片信息" }).click();
  const fileDetails = page.getByRole("dialog", { name: "图片详情" });
  await fileDetails.getByRole("button", { name: "重命名" }).click();
  await fileDetails.getByRole("textbox", { name: "新名称" }).fill("新样张");
  await fileDetails.getByRole("button", { name: "保存名称" }).click();
  assert.deepEqual(await request("local_file_manage"), {
    library_id: "main", id: 8, location_id: 9, name: "新样张",
  });
  await fileDetails.getByRole("button", { name: "关闭弹窗" }).click();
  await local.getByRole("button", { name: "退出管理" }).click();
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
  await page.goto(`http://127.0.0.1:${server.address().port}/?desktop=1&telegram_fixture=1`);
  await page.getByRole("button", { name: "进入测试媒体库" }).click();
  const forwarded = page.locator(".local-card").filter({ hasText: "合成转发正文" });
  await forwarded.getByText("真正的源频道", { exact: true }).waitFor();
  await forwarded.locator(".local-telegram-avatar img").waitFor();
  const ordinary = page.locator(".local-card").filter({ hasText: "合成普通正文" });
  await ordinary.locator("time").waitFor();
  assert.equal(await ordinary.locator(".local-telegram-source").count(), 0);
  assert.equal(await page.getByText("当前扫描频道", { exact: true }).count(), 0);
  await forwarded.getByRole("button", { name: "查看原消息" }).click();
  assert.deepEqual(await request("tg_open_source"), { id: 34, library_id: "main", content_id: 72 });
  assert.deepEqual(errors, []);
  console.log("本地导入、预览、集合管理、跨库引用和 Telegram 消息卡片通过");
} finally {
  await browser?.close();
  await new Promise((done) => server.close(done));
}
