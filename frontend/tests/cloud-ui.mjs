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
        { id: "file", name: "fixture.mp4", type: "file", size: 2048, thumbnail_key: "fixture-thumb" },
        { id: "image", name: "fixture.png", type: "file", size: 128, preview_id: 42 },
      ] : [{ id: "folder", name: "合成目录", type: "directory", size: 0 }], has_more: false });
      if (action === "cloud_preview" || action === "cloud_thumbnail") return response("data:image/png;base64,iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+a1ioAAAAASUVORK5CYII=");
      if (action === "cloud_import") { imported = true; return response(job); }
      if (action === "cloud_jobs") return response({ items: imported ? [job] : [] });
      if (action === "cloud_catalog") {
        const items = imported && (!p.library_id || item.libraries.includes(p.library_id)) && (!p.tag || item.tags.includes(p.tag)) ? [item] : [];
        return response({ items, total: items.length, offset: 0, count: items.length, local_count: 0, local_bytes: 0 });
      }
      if (action === "cloud_tags") { item.tags = p.tags; return response(true); }
      if (action === "cloud_link") { item.libraries = p.linked ? [p.library_id] : []; return response(true); }
      if (action === "cloud_locate") return response(p.id < 0 ? { parent: "folder" } : {});
      if (action === "cloud_positions") return response({ 3: { ...item, id: 60, locations: [{ id: 99, kind: "local", state: "present", label: "旅行收藏" }] } });
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
  await page.getByRole("checkbox", { name: "同时导入子目录" }).check();
  await page.getByRole("button", { name: "光鸭媒体", exact: true }).click();
  await page.getByRole("button", { name: "浏览并导入" }).click();
  await page.getByText("fixture.mp4", { exact: true }).waitFor();
  assert.equal(await directoryReads(), initialReads);
  assert.equal(await page.getByRole("checkbox", { name: "同时导入子目录" }).isChecked(), true);
  await page.getByRole("checkbox", { name: "同时导入子目录" }).uncheck();
  await page.getByRole("button", { name: "导入当前目录" }).click();
  assert.deepEqual(await request("cloud_import"), { parent: "folder", name: "合成目录", recursive: false });
  await page.getByRole("button", { name: "查看导入任务" }).click();
  await page.getByText("已导入 1 个媒体", { exact: true }).waitFor();
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
