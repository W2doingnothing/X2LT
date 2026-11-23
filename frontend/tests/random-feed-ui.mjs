import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { createServer } from "node:http";
import { chromium } from "playwright";

const files = new URL("../../local_video/web/", import.meta.url);
let clip;
const fetched = [];
const server = createServer((request, response) => {
  const name = new URL(request.url, "http://127.0.0.1").pathname.slice(1);
  if (name === "generate") {
    response.setHeader("Content-Type", "text/html; charset=utf-8");
    return response.end("<title>合成视频</title>");
  }
  if (name === "fixture.webm" || name === "broken.webm") {
    fetched.push(name);
    response.setHeader("Content-Type", "video/webm");
    return response.end(name === "fixture.webm" ? clip : "invalid video");
  }
  if (!/^(|theme-boot\.js|assets\/[\w-]+\.(js|css))$/.test(name)) {
    return response.writeHead(404).end();
  }
  try {
    response.setHeader("Content-Type", name.endsWith(".js") ? "text/javascript" : name.endsWith(".css") ? "text/css" : "text/html; charset=utf-8");
    response.end(readFileSync(new URL(name || "index.html", files)));
  } catch {
    response.writeHead(404).end();
  }
});
await new Promise((resolve) => server.listen(0, "127.0.0.1", resolve));
const origin = `http://127.0.0.1:${server.address().port}`;

let browser;
try {
  browser = await chromium.launch({ channel: "msedge", headless: true });
  const page = await browser.newPage({ viewport: { width: 1000, height: 700 } });
  page.setDefaultTimeout(5000);
  const errors = [];
  page.on("pageerror", (error) => errors.push(error.message));
  await page.addInitScript(() => {
    let index = 0;
    let revision = 0;
    const params = new URLSearchParams(location.search);
    const broken = params.has("broken");
    const skip = params.has("skip");
    const privacy = !broken && !skip;
    const count = privacy ? 3 : 2;
    const calls = (window.feedCalls = []);
    window.pywebview = { api: { async call(action, payload) {
      calls.push({ action, payload });
      const ok = (data) => ({ ok: true, data });
      if (action === "random_feed_step") {
        if (payload.direction === "next") index = (index + 1) % count;
        if (payload.direction === "previous") index = Math.max(0, index - 1);
        return ok({ revision: String(++revision), index, count, canPrevious: index > 0, privacy,
          title: ["竖屏样本", "第二个样本", "第三个样本"][index], library: "合成媒体库" });
      }
      if (action === "random_feed_play") {
        const token = "clip-" + payload.revision;
        if (index === 1 && privacy) await new Promise((resolve) => { window.releaseFixture = resolve; });
        return ok({ token, url: `${location.origin}/${broken || (skip && index === 0) ? "broken" : "fixture"}.webm` });
      }
      return ok(null);
    } } };
  });
  await page.goto(origin + "/?desktop=1&view=random-feed");
  const reveal = page.getByRole("button", { name: "显示并播放" });
  await reveal.waitFor({ timeout: 3000 });
  const generate = await browser.newPage();
  await generate.goto(origin + "/generate");
  clip = Buffer.from(await generate.evaluate(async () => {
    const canvas = document.createElement("canvas");
    canvas.width = 90;
    canvas.height = 160;
    const context = canvas.getContext("2d");
    const stream = canvas.captureStream(25);
    const recorder = new MediaRecorder(stream, { mimeType: "video/webm;codecs=vp8" });
    const chunks = [];
    recorder.ondataavailable = (event) => chunks.push(event.data);
    const stopped = new Promise((resolve) => { recorder.onstop = resolve; });
    recorder.start();
    for (let i = 0; i < 22; i++) {
      context.fillStyle = i % 2 ? "#58ad67" : "#5b67ae";
      context.fillRect(0, 0, 90, 160);
      await new Promise((resolve) => setTimeout(resolve, 40));
    }
    recorder.stop();
    await stopped;
    stream.getTracks().forEach((track) => track.stop());
    return [...new Uint8Array(await new Blob(chunks).arrayBuffer())];
  }));
  await generate.close();
  assert.ok(clip.length > 100);

  const calls = (name) => page.evaluate((action) => window.feedCalls.filter((entry) => entry.action === action), name);
  const stage = page.locator(".random-feed-stage");
  const video = stage.locator("video");
  const playing = () => page.waitForFunction(() => {
    const video = document.querySelector(".random-feed-stage video");
    return video?.videoWidth === 90 && !video.paused && video.currentTime > 0;
  });
  assert.equal(await video.count(), 0);
  assert.equal(fetched.length, 0);
  assert.deepEqual(await calls("random_feed_play"), []);
  assert.equal(await page.getByText("竖屏样本", { exact: true }).count(), 0);
  await reveal.click();
  await playing();
  assert.equal(await video.evaluate((element) => element.loop && getComputedStyle(element).objectFit === "contain"), true);
  await page.keyboard.press("Space");
  await page.waitForFunction(() => document.querySelector(".random-feed-stage video")?.paused);
  await page.keyboard.press("Space");
  await playing();

  await page.getByRole("heading", { name: "随机播放" }).hover();
  await page.mouse.wheel(0, 140);
  assert.equal((await calls("random_feed_step")).length, 1);
  await stage.hover();
  await stage.evaluate(async (element) => {
    for (const deltaY of [5, 45, 30, 14, 8, 4]) {
      element.dispatchEvent(new WheelEvent("wheel", { deltaY, bubbles: true, cancelable: true }));
      await new Promise((resolve) => setTimeout(resolve, 35));
    }
  });
  await reveal.waitFor();
  assert.deepEqual((await calls("random_feed_step")).map((entry) => entry.payload.direction), ["current", "next"]);
  assert.equal(await video.count(), 0);
  await reveal.click();
  await page.waitForFunction(() => window.releaseFixture);
  await page.keyboard.press("ArrowDown");
  await reveal.waitFor();
  await page.evaluate(() => window.releaseFixture());
  await page.waitForFunction(() => window.feedCalls.some((entry) => entry.action === "video_close" && entry.payload.token === "clip-2"));
  assert.equal(await video.count(), 0);
  assert.equal(fetched.length, 1);
  await page.keyboard.press("ArrowUp");
  await page.waitForFunction(() => window.feedCalls.at(-1)?.payload.direction === "previous");
  await reveal.waitFor();
  await page.keyboard.press("f");
  await page.waitForFunction(() => Boolean(document.fullscreenElement));
  await page.keyboard.press("Escape");
  await page.waitForFunction(() => !document.fullscreenElement);
  assert.equal((await calls("random_feed_close")).length, 0);
  await page.keyboard.press("Escape");
  await page.waitForFunction(() => window.feedCalls.some((entry) => entry.action === "random_feed_close"));

  await page.goto(origin + "/?desktop=1&view=random-feed&skip=1");
  await playing();
  await page.getByText("已跳过无法播放的视频", { exact: true }).waitFor();
  assert.equal((await calls("random_feed_play")).length, 2);

  await page.goto(origin + "/?desktop=1&view=random-feed&broken=1");
  await page.getByText("没有可播放的视频", { exact: true }).waitFor();
  assert.equal((await calls("random_feed_play")).length, 2);
  assert.equal((await calls("video_close")).length, 2);
  assert.equal(await video.count(), 0);
  assert.deepEqual(errors, []);
  console.log("PASS random feed privacy, looping, navigation, stale tickets, fullscreen and decoder failure");
} finally {
  await browser?.close();
  server.close();
}
