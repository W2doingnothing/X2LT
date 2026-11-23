import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { createServer } from "node:http";
import { fileURLToPath } from "node:url";
import { chromium } from "playwright";

const files = new URL("../../local_video/web/", import.meta.url);
const app = createServer((request, response) => {
  const path = new URL(request.url, "http://127.0.0.1").pathname;
  if (path === "/generate") {
    response.setHeader("Content-Type", "text/html");
    return response.end("<title>合成视频</title>");
  }
  const name = path === "/" ? "index.html" : path.slice(1);
  if (!/^(index\.html|theme-boot\.js|assets\/[\w-]+\.(js|css))$/.test(name)) {
    return response.writeHead(404).end();
  }
  const type = name.endsWith(".js") ? "text/javascript" : name.endsWith(".css") ? "text/css" : "text/html; charset=utf-8";
  try {
    response.setHeader("Content-Type", type);
    response.end(readFileSync(fileURLToPath(new URL(name, files))));
  } catch {
    response.writeHead(404).end();
  }
});
await new Promise((resolve) => app.listen(0, "127.0.0.1", resolve));
const origin = `http://127.0.0.1:${app.address().port}`;
let clip = Buffer.alloc(0);
const requests = [];
const media = createServer((request, response) => {
  requests.push({ origin: request.headers.origin, range: request.headers.range });
  if (request.headers.origin !== origin) return response.writeHead(403).end();
  const range = /^bytes=(\d+)-(\d*)$/.exec(request.headers.range || "");
  const start = range ? Number(range[1]) : 0;
  const end = range?.[2] ? Math.min(Number(range[2]), clip.length - 1) : clip.length - 1;
  if (start > end) return response.writeHead(416).end();
  response.setHeader("Access-Control-Allow-Origin", origin);
  response.setHeader("Content-Type", "video/webm");
  response.setHeader("Accept-Ranges", "bytes");
  response.setHeader("Content-Length", end - start + 1);
  if (range) response.setHeader("Content-Range", `bytes ${start}-${end}/${clip.length}`);
  response.writeHead(range ? 206 : 200).end(clip.subarray(start, end + 1));
});
await new Promise((resolve) => media.listen(0, "127.0.0.1", resolve));
const url = `http://127.0.0.1:${media.address().port}/fixture.webm`;

let browser;
try {
  browser = await chromium.launch({ channel: "msedge", headless: true });
  const generator = await browser.newPage();
  await generator.goto(origin + "/generate");
  clip = Buffer.from(await generator.evaluate(async () => {
    const canvas = document.createElement("canvas");
    canvas.width = 120;
    canvas.height = 160;
    const ctx = canvas.getContext("2d");
    const stream = canvas.captureStream(25);
    const recorder = new MediaRecorder(stream, { mimeType: "video/webm;codecs=vp8" });
    const chunks = [];
    recorder.ondataavailable = (event) => chunks.push(event.data);
    const stopped = new Promise((resolve) => { recorder.onstop = resolve; });
    recorder.start();
    for (let i = 0; i < 32; i++) {
      ctx.fillStyle = i % 2 ? "#58ad67" : "#5b67ae";
      ctx.fillRect(0, 0, canvas.width, canvas.height);
      await new Promise((resolve) => setTimeout(resolve, 40));
    }
    recorder.stop();
    await stopped;
    stream.getTracks().forEach((track) => track.stop());
    return [...new Uint8Array(await new Blob(chunks).arrayBuffer())];
  }));
  assert.ok(clip.length > 100);
  await generator.close();

  const page = await browser.newPage({ viewport: { width: 1360, height: 900 } });
  page.setDefaultTimeout(8000);
  const errors = [];
  const scripts = [];
  page.on("pageerror", (error) => errors.push(error.message));
  page.on("request", (request) => { if (request.resourceType() === "script") scripts.push(request.url()); });
  await page.addInitScript((playUrl) => {
    const location = {
      id: 12, media_id: "0", media_type: "video", extension: "mp4", bytes: 16,
      width: 120, height: 160, output: "F:\Fixture", quality: "best",
      state: "completed", error: "", verification: "length+sha256",
    };
    const library = {
      id: "main", name: "播放测试库", path: location.output,
      online: true, default: true, quota_bytes: 0, message_count: 1, media_count: 1,
    };
    const settings = { theme: "light", accent: "#397863", privacy_mode: true };
    const task = {
      id: "404", handle: "fixture", author: "fixture", display_name: "播放样本",
      title: "播放样本", text: "示例", updated: 0, ignored: false, media: [location],
    };
    const resource = {
      key: "fixture", platform: "x", source_label: "X", message_id: "404", media_id: "0",
      media_type: "video", title: "资源视频", author: "fixture", added_at: 1,
      tags: [], locations: [location],
    };
    const local = {
      id: 17, title: "本地样本", kind: "video", bytes: 16, tags: [], libraries: ["main"],
      locations: [{ id: 21, state: "available", label: "原件", managed: false }],
    };
    const calls = [];
    const png = "data:image/png;base64,iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+a1ioAAAAASUVORK5CYII=";
    window.__calls = calls;
    let token = 0;
    const api = { async call(action, payload = {}) {
      calls.push({ action, payload: JSON.parse(JSON.stringify(payload)) });
      const ok = (data) => ({ ok: true, data });
      if (action === "bootstrap" || action === "snapshot") {
        const tasks = action === "snapshot" && payload.collection ? [task] : [];
        return ok({
          version: "test", libraries: [library], default_library: "main", settings,
          tag_options: [], tasks, total: tasks.length, offset: 0, busy: false,
          counts: { all: tasks.length, completed: tasks.length, pending: 0, ignored: 0 },
        });
      }
      if (action === "poll") return ok({ events: [], busy: false });
      if (action === "resource_overview") return ok({ resources: [resource], video_count: 1, image_count: 0 });
      if (action === "local_library") return ok({ items: [local], total: 1, offset: 0 });
      if (action === "local_media_preview" || action === "preview") return ok(png);
      if (action === "video_open") {
        const result = { url: playUrl, token: "clip-" + ++token };
        if (window.__holdVideo) await new Promise((resolve) => { window.__finishVideo = resolve; });
        return ok(result);
      }
      return ok(null);
    } };
    window.pywebview = { api: {} };
    window.setTimeout(() => { window.pywebview.api = api; }, 450);
  }, url);
  const actionCalls = (action) => page.evaluate((name) => window.__calls.filter((entry) => entry.action === name), action);
  const dialog = page.getByRole("dialog", { name: "播放视频" });
  const close = dialog.getByRole("button", { name: "关闭弹窗" });
  const playing = () => page.waitForFunction(() => {
    const video = document.querySelector(".playback-stage video");
    return video?.videoWidth === 120 && !video.paused && video.currentTime > 0;
  });

  await page.goto(origin + "/?desktop=1");
  const recent = page.locator(".recent-resources .resource-preview");
  await recent.waitFor();
  assert.equal(scripts.some((name) => name.includes("/artplayer-")), false);
  await recent.click();
  assert.equal((await actionCalls("video_open")).length, 0);
  await recent.click();
  await playing();
  assert.deepEqual((await actionCalls("video_open")).at(-1).payload, { source: "x", id: 12 });
  assert.ok(requests.some((request) => request.origin === origin && request.range));
  await page.locator(".playback-stage video").evaluate((video) => {
    video.pause();
    video.dispatchEvent(new Event("canplay"));
  });
  assert.equal(await page.locator(".playback-stage video").evaluate((video) => video.paused), true);
  await close.click();
  await dialog.waitFor({ state: "hidden" });
  await page.waitForFunction(() => window.__calls.some((entry) => entry.action === "video_close" && entry.payload.token === "clip-1"));
  await page.waitForFunction(() => document.activeElement === document.querySelector(".recent-resources .resource-preview"));

  await page.getByRole("button", { name: "进入播放测试库", exact: true }).click();
  const xTile = page.locator(".tweet-card .media-tile");
  await xTile.click();
  await xTile.click();
  await dialog.waitFor();
  assert.deepEqual((await actionCalls("video_open")).at(-1).payload, { source: "x", id: 12 });
  await close.click();
  await dialog.waitFor({ state: "hidden" });

  const localTile = page.locator(".local-card .local-preview");
  await localTile.click();
  await localTile.click();
  await dialog.waitFor();
  assert.deepEqual((await actionCalls("video_open")).at(-1).payload, { source: "local", id: 17 });
  await dialog.getByRole("button", { name: "用系统播放器打开" }).click();
  await dialog.waitFor({ state: "hidden" });
  assert.deepEqual((await actionCalls("local_media_open")).at(-1).payload, { id: 17 });

  await page.evaluate(() => { window.__holdVideo = true; });
  await localTile.click();
  await dialog.waitFor();
  await page.waitForFunction(() => typeof window.__finishVideo === "function");
  await close.click();
  await page.evaluate(() => window.__finishVideo());
  await page.waitForFunction(() => window.__calls.some((entry) => entry.action === "video_close" && entry.payload.token === "clip-4"));
  assert.deepEqual(errors, []);
  console.log("PASS ArtPlayer lazy load and decoding, overview/X/local clicks, privacy, fallback and ticket release");
} finally {
  await browser?.close();
  media.close();
  app.close();
}
