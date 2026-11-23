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
    const library = { id: "main", name: "合成库", path: "F:\\Fixture", online: true, default: true, quota_bytes: 0 };
    const jobs = [
      { id: "legacy", kind: "download", state: "completed", name: "旧下载样本", message: "" },
      { id: "retry", kind: "upload", state: "failed", name: "待重试样本", message: "网络中断", retryable: true },
    ];
    let connection = { available: true, logged_in: false, state: "SIGNED_OUT" };
    window.requests = [];
    const ok = (data) => ({ ok: true, data });
    window.pywebview = { api: { async call(action, p = {}) {
      window.requests.push({ action, p });
      if (action === "snapshot") return ok({
        version: "fixture", settings: { theme: "light", accent: "#397863", username: "fixture", privacy_mode: false },
        has_credentials: true, credential_error: "", libraries: [library], default_library: library.id,
        tasks: [], counts: { all: 0, completed: 0, pending: 0, ignored: 0 }, total: 0, offset: 0, busy: false, tag_options: [],
      });
      if (action === "poll") return ok({ events: [], busy: false });
      if (action === "resource_overview") return ok({ resources: [], total: 0, offset: 0, video_count: 0, image_count: 0 });
      if (action === "cloud_status") {
        if (connection.code === "LOGIN_PENDING") connection = { available: true, logged_in: true, state: "SUCCEEDED" };
        return ok(connection);
      }
      if (action === "cloud_login") {
        connection = { available: true, logged_in: false, state: null, code: "LOGIN_PENDING" };
        return ok(connection);
      }
      if (action === "cloud_pick_upload") return ok({ id: "opaque", names: ["sample.txt", "sample.png"], bytes: 13 });
      if (action === "cloud_start_upload") {
        jobs.push({ id: "new", kind: "upload", state: "queued", name: "sample.txt", message: "", cancellable: true });
        return ok({ count: 2 });
      }
      if (action === "cloud_jobs") return ok({
        items: jobs,
        counts: {
          cancellable: jobs.filter((job) => job.cancellable).length,
          retryable: jobs.filter((job) => job.retryable).length,
        },
      });
      if (action === "cloud_cancel" || action === "cloud_retry") {
        const job = jobs.find((row) => row.id === p.id);
        job.state = action === "cloud_cancel" ? "cancelled" : "queued";
        job.cancellable = action === "cloud_retry";
        job.retryable = false;
        return ok({ count: 1 });
      }
      return ok(null);
    } } };
  });
  await page.goto(`http://127.0.0.1:${server.address().port}/?desktop=1`);
  await page.getByRole("button", { name: "总览", exact: true }).click();
  await page.getByRole("heading", { name: "总览" }).waitFor();
  assert.equal(await page.getByRole("button", { name: "进入光鸭云盘" }).count(), 0);
  await page.getByRole("button", { name: "媒体工具", exact: true }).click();
  const menu = page.locator("details.cloud-upload");
  const summary = menu.locator("summary");
  const region = page.getByRole("region", { name: "上传文件" });
  const requests = () => page.evaluate(() => window.requests.filter((entry) => entry.action.startsWith("cloud_")));
  assert.equal(await summary.textContent(), "光鸭上传");
  assert.equal(await menu.getAttribute("open"), null);
  assert.equal(await region.isVisible(), false);
  assert.deepEqual(await requests(), []);

  await summary.focus();
  await summary.press("Enter");
  await region.getByRole("button", { name: "连接光鸭" }).click();
  await region.getByText("等待登录", { exact: true }).waitFor();
  await region.getByRole("button", { name: "检查连接" }).click();
  await region.getByRole("button", { name: "选择本地文件" }).click();
  await region.getByText("sample.png", { exact: true }).waitFor();
  assert.equal((await requests()).some((entry) => entry.action === "cloud_start_upload"), false);
  await region.getByRole("button", { name: "上传 2 个文件" }).click();
  await region.getByText("已加入队列，共 2 个文件").waitFor();
  const payload = (action) => requests().then((entries) => entries.find((entry) => entry.action === action)?.p);
  assert.deepEqual(await payload("cloud_start_upload"), { id: "opaque" });
  assert.equal(await region.getByText("旧下载样本").count(), 0);
  const uploaded = region.getByRole("listitem").filter({ hasText: "sample.txt" });
  await uploaded.getByRole("button", { name: "取消", exact: true }).click();
  await page.getByRole("dialog", { name: "取消上传" }).getByRole("button", { name: "确认取消" }).click();
  await uploaded.getByText("已取消", { exact: true }).waitFor();
  assert.deepEqual(await payload("cloud_cancel"), { id: "new" });
  const failed = region.getByRole("listitem").filter({ hasText: "待重试样本" });
  await failed.getByRole("button", { name: "重试", exact: true }).click();
  await failed.getByText("排队中", { exact: true }).waitFor();
  assert.deepEqual(await payload("cloud_retry"), { id: "retry" });

  await summary.click();
  assert.equal(await region.isVisible(), false);
  assert.deepEqual(new Set((await requests()).map((entry) => entry.action)), new Set([
    "cloud_status", "cloud_login", "cloud_pick_upload", "cloud_start_upload", "cloud_jobs", "cloud_cancel", "cloud_retry",
  ]));
  assert.deepEqual(errors, []);
  console.log("PASS collapsed upload-only menu, lazy login, native selection, explicit queue, cancel and retry");
} finally {
  await browser.close();
  await new Promise((done) => server.close(done));
}
