import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import http from "node:http";
import { resolve } from "node:path";
import { fileURLToPath } from "node:url";
import { chromium } from "playwright";

const root = fileURLToPath(new URL("../../local_video/web/", import.meta.url));
const server = http.createServer((request, response) => {
  const path = new URL(request.url, "http://127.0.0.1").pathname;
  if (path !== "/" && path !== "/theme-boot.js" && !/^\/assets\/[\w-]+\.(js|css)$/.test(path)) {
    response.writeHead(404).end();
    return;
  }
  try {
    response.setHeader("Content-Type", path.endsWith(".js") ? "text/javascript" : path.endsWith(".css") ? "text/css" : "text/html; charset=utf-8");
    response.end(readFileSync(resolve(root, path === "/" ? "index.html" : path.slice(1))));
  } catch {
    response.writeHead(404).end();
  }
});
await new Promise((done) => server.listen(0, "127.0.0.1", done));
const browser = await chromium.launch({ channel: "msedge", headless: true });
try {
  const page = await browser.newPage({ viewport: { width: 1440, height: 1000 } });
  page.setDefaultTimeout(5000);
  const errors = [];
  page.on("pageerror", (error) => errors.push(error.message));
  await page.addInitScript(() => {
    const library = { id: "main", name: "旅行收藏", path: "F:\\Fixture", online: true, default: true, quota_bytes: 0 };
    const item = {
      id: 31, content_id: 22, title: "合成云端条目", text: "合成资料正文", author: "", kind: "video", filename: "fixture.mp4", bytes: 2048,
      tags: [], libraries: [], locations: [{ id: -4, kind: "cloud", state: "present", label: "光鸭" }],
    };
    const job = { id: "import-fixture", kind: "import", state: "completed", name: "根目录", message: "已导入 1 个媒体" };
    const failedUpload = { id: "failed-upload", kind: "upload", state: "failed", name: "失败样本", message: "建目录失败", retryable: true, cancellable: false };
    const activeUpload = { id: "active-upload", kind: "upload", state: "running", name: "运行样本", message: "", retryable: false, cancellable: true };
    const media = { id: 3, media_id: "7", media_type: "video", extension: "mp4", state: "completed", error: "", bytes: 512, width: 720, height: 480, quality: "best", output: library.path, verification: "" };
    const tweet = { id: "500", title: "本地条目", text: "合成推文正文", author: "fixture", handle: "fixture", ignored: false, updated: 0, media: [media] };
    let imported = false;
    window.requests = [];
    function response(data) { return { ok: true, data }; }
    window.pywebview = { api: { async call(action, p = {}) {
      window.requests.push({ action, p });
      if (action === "snapshot") return response({
        version: "fixture", settings: { theme: "light", accent: "#397863", username: "fixture", privacy_mode: false, confirm_removal: true },
        has_credentials: true, credential_error: "", libraries: [library], default_library: library.id,
        tasks: [tweet], counts: { all: 1, completed: 1, pending: 0, ignored: 0 }, total: 1, offset: 0, busy: false, tag_options: [],
      });
      if (action === "poll") return response({ events: [], busy: false });
      if (action === "resource_overview") return response({ resources: [], total: 0, offset: 0, video_count: 0, image_count: 0 });
      if (action === "cloud_status") return response({ available: true, logged_in: true, state: "SUCCEEDED" });
      if (action === "cloud_list") return response({ items: p.parent ? [
        { id: "file", name: "fixture.mp4", type: "file", size: 2048, thumbnail_key: "fixture-thumb", view_key: "fixture-video" },
        { id: "image", name: "fixture.png", type: "file", size: 128, preview_id: 42, view_key: "fixture-image" },
      ] : [{ id: "folder", name: "合成目录", type: "directory", size: 0 }], has_more: false });
      if (action === "cloud_preview" || action === "cloud_thumbnail") return response("data:image/png;base64,iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+a1ioAAAAASUVORK5CYII=");
      if (action === "cloud_media_view") return response(p.local_id ? { kind: "image", url: "data:image/png;base64,iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+a1ioAAAAASUVORK5CYII=" } :
        { kind: "video", url: "https://sample.guangyacdn.com/synthetic.mp4" });
      if (action === "cloud_import") { imported = true; return response(job); }
      if (action === "cloud_jobs") return response({
        items: imported ? [job, failedUpload, activeUpload] : [],
        counts: { retryable: Number(failedUpload.retryable), cancellable: Number(activeUpload.cancellable) },
      });
      if (action === "cloud_retry") {
        failedUpload.retryable = false;
        failedUpload.state = "queued";
        return response({ count: 1 });
      }
      if (action === "cloud_cancel") {
        activeUpload.cancellable = false;
        activeUpload.state = "cancelling";
        return response({ count: 1 });
      }
      if (action === "cloud_catalog") {
        const items = imported && (!p.library_id || item.libraries.includes(p.library_id)) && (!p.tag || item.tags.includes(p.tag)) ? [item] : [];
        return response({ items, total: items.length, offset: 0, count: items.length, local_count: 0, local_bytes: 0 });
      }
      if (action === "cloud_tags") { item.tags = p.tags; return response(true); }
      if (action === "cloud_link") { item.libraries = p.linked ? [p.library_id] : []; return response(true); }
      if (action === "cloud_locate") return response(p.id < 0 ? { parent: "folder" } : {});
      if (action === "cloud_positions") return response({ 3: { ...item, id: 60, locations: [{ id: 99, kind: "local", state: "present", label: "旅行收藏" }] } });
      if (action === "cloud_archive_plan") {
        const tag = p.tag_overrides?.content || "旅行";
        const leaf = p.mode === "tag" ? tag : p.aliases?.group || "其他博主";
        const path = ["X2LT归档", "库代号", ...(p.mode === "source" ? ["推特"] : []), leaf];
        return response({
          id: `archive-${p.mode}-${leaf}`, media_count: 1, queued_count: 1, known_count: 0, bytes: 512, unavailable_count: 0,
          folders: [{ path, count: 1, known: 0 }], aliases: [{ key: "group", label: leaf, path }],
          tag_choices: p.mode === "tag" ? [{ id: "content", title: "合成推文正文", tags: ["旅行", "风景"], selected: tag }] : [],
        });
      }
      if (action === "cloud_archive_start") return response({ queued: 1 });
      if (action === "cloud_upload" || action === "cloud_download") return response(job);
      return response(null);
    } } };
  });
  await page.goto(`http://127.0.0.1:${server.address().port}/?desktop=1`);
  const request = async (action) => page.evaluate((name) => window.requests.filter((row) => row.action === name).at(-1)?.p, action);
  await page.getByRole("button", { name: "总览", exact: true }).click();
  await page.getByRole("button", { name: "进入光鸭云盘" }).click();
  await page.getByRole("button", { name: "浏览并导入" }).click();
  await page.getByRole("button", { name: "合成目录" }).click();
  await page.getByText("fixture.mp4", { exact: true }).waitFor();
  const directoryReads = () => page.evaluate(() => window.requests.filter((row) => row.action === "cloud_list").length);
  const initialReads = await directoryReads();
  await page.getByText("fixture.mp4", { exact: true }).locator("..").getByText("MP4", { exact: true }).waitFor({ timeout: 1800 });
  await page.getByRole("img", { name: "本地缩略图" }).waitFor();
  assert.deepEqual(await request("cloud_preview"), { id: 42 });
  await page.getByRole("img", { name: "光鸭缩略图" }).waitFor();
  assert.deepEqual(await request("cloud_thumbnail"), { key: "fixture-thumb" });
  await page.getByRole("button", { name: "打开图片", exact: true }).click();
  const picture = page.getByRole("dialog", { name: "图片预览" });
  await picture.getByRole("img", { name: "打开的光鸭图片" }).waitFor();
  assert.deepEqual(await request("cloud_media_view"), { local_id: 42 });
  await picture.getByRole("button", { name: "关闭弹窗" }).click();
  await page.route("https://sample.guangyacdn.com/**", (route) => route.fulfill({ status: 404 }));
  await page.getByRole("button", { name: "播放视频", exact: true }).click();
  const player = page.getByRole("dialog", { name: "在线播放" });
  await player.locator("video").waitFor();
  assert.deepEqual(await request("cloud_media_view"), { key: "fixture-video", retry: false });
  await player.getByRole("button", { name: "关闭弹窗" }).click();
  await page.getByRole("checkbox", { name: "同时导入子目录" }).check();
  await page.getByRole("button", { name: "光鸭媒体", exact: true }).click();
  await page.getByRole("button", { name: "浏览并导入" }).click();
  await page.getByText("fixture.mp4", { exact: true }).waitFor();
  assert.equal(await directoryReads(), initialReads);
  assert.equal(await page.getByRole("checkbox", { name: "同时导入子目录" }).isChecked(), true);
  await page.getByRole("button", { name: "刷新光鸭目录" }).click();
  assert.deepEqual(await request("cloud_list"), { parent: "folder", page: 0, refresh: true });
  await page.getByRole("checkbox", { name: "同时导入子目录" }).uncheck();
  await page.getByRole("button", { name: "导入当前目录" }).click();
  assert.deepEqual(await request("cloud_import"), { parent: "folder", name: "合成目录", recursive: false });
  await page.getByRole("button", { name: "查看导入任务" }).click();
  await page.getByText("已导入 1 个媒体", { exact: true }).waitFor();
  await page.getByRole("button", { name: "重试", exact: true }).click();
  assert.deepEqual(await request("cloud_retry"), { id: "failed-upload" });
  await page.getByText("已安排重试 1 个任务", { exact: true }).waitFor();
  await page.getByRole("button", { name: "取消待处理（1）", exact: true }).click();
  const cancellation = page.getByRole("dialog", { name: "取消待处理任务", exact: true });
  await cancellation.getByRole("button", { name: "确认取消", exact: true }).click();
  assert.deepEqual(await request("cloud_cancel"), { all: true });
  await page.getByText("正在取消", { exact: true }).waitFor();
  await page.getByRole("button", { name: "光鸭媒体", exact: true }).click();
  await page.getByText("合成云端条目", { exact: true }).waitFor();
  await page.getByRole("button", { name: "光鸭 1/1", exact: true }).click();
  const remote = page.getByRole("dialog", { name: "光鸭存储目录" });
  await remote.getByText("fixture.mp4", { exact: true }).waitFor();
  await remote.getByRole("button", { name: "关闭弹窗" }).click();
  await page.getByRole("button", { name: "管理位置与标签" }).click();
  await page.getByRole("textbox", { name: "标签，逗号分隔" }).fill("#归档，视频");
  await page.getByRole("button", { name: "保存标签" }).click();
  await page.getByText("标签已保存", { exact: true }).waitFor();
  assert.deepEqual(await request("cloud_tags"), { id: 31, tags: ["归档", "视频"] });
  await page.getByRole("button", { name: "添加到目标库" }).click();
  await page.getByRole("button", { name: "移出目标库" }).waitFor();
  await page.getByRole("button", { name: "保存到本地", exact: true }).click();
  await page.getByText("已加入下载队列", { exact: true }).waitFor();
  assert.deepEqual(await request("cloud_download"), { id: -4, library_id: "main" });
  await page.getByRole("button", { name: "总览", exact: true }).last().click();
  await page.getByRole("button", { name: "进入旅行收藏" }).click();
  await page.getByRole("button", { name: "跨库收藏", exact: true }).click();
  await page.getByText("合成云端条目", { exact: true }).waitFor();
  await page.getByRole("button", { name: "库内内容", exact: true }).click();
  await page.getByRole("button", { name: "全选当前页" }).click();
  await page.getByRole("button", { name: "按层级上传所选" }).click();
  const archive = page.getByRole("dialog", { name: "按层级上传到光鸭" });
  await archive.getByRole("checkbox", { name: "合并小分组" }).check();
  await archive.getByRole("spinbutton", { name: "合并阈值" }).fill("5");
  await archive.getByRole("button", { name: "合成目录" }).click();
  await archive.getByRole("button", { name: "使用此目录" }).click();
  await archive.getByRole("button", { name: "生成目录预览" }).click();
  const queueArchive = archive.getByRole("button", { name: "确认并加入上传队列" });
  await queueArchive.waitFor();
  assert.deepEqual(await request("cloud_archive_plan"), { library_id: "main", parent: "folder", mode: "source", ids: ["500"], threshold: 5, aliases: {} });
  await archive.getByText("编辑目录别名", { exact: true }).click();
  await archive.getByRole("textbox", { name: "目录别名 1" }).fill("小组");
  assert.equal(await queueArchive.isDisabled(), true);
  await archive.getByRole("button", { name: "重新生成预览" }).click();
  await archive.getByRole("list", { name: "归档目录预览" }).getByText("X2LT归档 / 库代号 / 推特 / 小组").waitFor();
  await archive.getByLabel("整理方式").selectOption("tag");
  assert.equal(await queueArchive.isDisabled(), true);
  await archive.getByRole("button", { name: "重新生成预览" }).click();
  await archive.getByText("调整多标签内容的归档标签", { exact: true }).click();
  await archive.getByRole("combobox", { name: "归档标签 1" }).selectOption("风景");
  assert.equal(await queueArchive.isDisabled(), true);
  await archive.getByRole("button", { name: "重新生成预览" }).click();
  await archive.getByRole("list", { name: "归档目录预览" }).getByText("X2LT归档 / 库代号 / 风景").waitFor();
  await queueArchive.click();
  await archive.waitFor({ state: "hidden" });
  assert.deepEqual(await request("cloud_archive_start"), { id: "archive-tag-风景" });
  await page.getByRole("button", { name: "详情", exact: true }).click();
  await page.getByRole("button", { name: "上传到光鸭", exact: true }).click();
  const upload = page.getByRole("dialog", { name: "上传到光鸭" });
  await upload.getByRole("button", { name: "合成目录" }).click();
  await upload.getByText("fixture.mp4", { exact: true }).waitFor();
  await upload.getByRole("button", { name: "上传到所选目录" }).click();
  await upload.waitFor({ state: "hidden" });
  assert.deepEqual(await request("cloud_upload"), { ids: [3], parent: "folder" });
  assert.deepEqual(errors, []);
  console.log("PASS cloud library import, tags, cross-library association, position browsing and upload payload");
} finally {
  await browser.close();
  await new Promise((done) => server.close(done));
}
