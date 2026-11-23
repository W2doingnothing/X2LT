import { ArrowDown, ArrowUp, Eye, Maximize, Shuffle, X } from "lucide-react";
import { useEffect, useRef, useState } from "react";
import type Artplayer from "artplayer";
import { call } from "./api";
import { Button } from "./components/ui/button";
import "./random-feed.css";

type Direction = "current" | "next" | "previous";
type Entry = {
  revision: string;
  title: string;
  library: string;
  index: number;
  count: number;
  canPrevious: boolean;
  privacy: boolean;
};
type Ticket = { url: string; token: string; library?: string };
type View = {
  state: "loading" | "hidden" | "playing" | "manual" | "empty" | "close-error";
  entry?: Entry;
  error?: string;
};
type Controls = {
  step: (direction: Direction) => void;
  reveal: () => void;
  play: () => void;
  fullscreen: () => void;
  close: () => void;
};

const MAX_SKIPS = 8;
const LOAD_LIMIT_MS = 12000;
const WHEEL_IDLE_MS = 235;
const WHEEL_THRESHOLD = 36;

function release(token: string) {
  void call("video_close", { token }).catch(() => {});
}

function playbackUrl(value: string) {
  const url = new URL(value);
  if (url.protocol !== "http:" || url.hostname !== "127.0.0.1" || !url.port || url.username || url.password) {
    throw Error("本地播放地址不可用");
  }
  return url.href;
}

export default function RandomFeed() {
  const stage = useRef<HTMLDivElement>(null);
  const host = useRef<HTMLDivElement>(null);
  const controls = useRef<Controls | null>(null);
  const [view, setView] = useState<View>({ state: "loading" });
  const [skipped, setSkipped] = useState(false);

  useEffect(() => {
    const surface = stage.current!;
    const container = host.current!;
    let alive = true;
    let closing = false;
    let revision = 0;
    let lastApplied = 0;
    let entry: Entry | undefined;
    let steps = Promise.resolve();
    let player: Artplayer | undefined;
    let token: string | undefined;
    let deadline: number | undefined;

    function clearDeadline() {
      window.clearTimeout(deadline);
      deadline = undefined;
    }

    function stop() {
      clearDeadline();
      const instance = player;
      player = undefined;
      if (instance) {
        instance.video.pause();
        instance.video.removeAttribute("src");
        instance.video.load();
        instance.destroy();
      }
      if (token) release(token);
      token = undefined;
    }

    const isCurrent = (id: number) => alive && !closing && revision === id;

    function play() {
      const instance = player;
      if (!instance) return;
      void instance.video.play().catch((reason: unknown) => {
        if (player !== instance) return;
        if (reason instanceof DOMException && reason.name === "NotAllowedError") {
          setView({ state: "manual", entry });
        }
      });
    }

    function open(item: Entry, id: number, direction: Direction, remaining: number) {
      setView({ state: "loading", entry: item });
      const fail = () => {
        if (!isCurrent(id)) return;
        const nextDirection = direction === "previous" && item.canPrevious ? "previous" : "next";
        if (remaining > 1) {
          setSkipped(true);
          step(nextDirection, Math.min(remaining - 1, item.count));
          return;
        }
        ++revision;
        lastApplied = revision;
        stop();
        setSkipped(false);
        const error = item.count > MAX_SKIPS ? "连续尝试的视频无法播放，可以切换到下一条" : undefined;
        setView({ state: "empty", entry: item, error });
      };
      deadline = window.setTimeout(fail, LOAD_LIMIT_MS);

      const ticket = call<Ticket>("random_feed_play", { revision: item.revision }).then((result) => {
        if (isCurrent(id)) token = result.token;
        else release(result.token);
        return result;
      });
      void Promise.all([ticket, import("artplayer")]).then(([result, module]) => {
        if (!isCurrent(id)) return;
        const ArtPlayer = module.default;
        ArtPlayer.LOG_VERSION = false;
        ArtPlayer.RECONNECT_TIME_MAX = 0;
        const instance = new ArtPlayer({
          container,
          url: playbackUrl(result.url),
          lang: "zh-cn",
          theme: "var(--accent)",
          playsInline: true,
          hotkey: false,
          moreVideoAttr: { crossOrigin: "anonymous", preload: "auto", loop: true },
        });
        player = instance;
        let ready = false;
        const onReady = () => {
          if (!isCurrent(id) || ready) return;
          ready = true;
          clearDeadline();
          if (result.library) item = { ...item, library: result.library };
          entry = item;
          setView({ state: "playing", entry: item });
          play();
        };
        instance.on("video:canplay", onReady);
        instance.on("video:playing", () => {
          if (isCurrent(id)) setView({ state: "playing", entry: item });
        });
        instance.on("video:error", fail);
        if (instance.video.error) fail();
        else if (instance.video.readyState >= 3) onReady();
      }).catch(fail);
    }

    function step(direction: Direction, remaining = MAX_SKIPS) {
      if (!alive || closing || (revision === lastApplied && direction === "previous" && !entry?.canPrevious)) return;
      const id = ++revision;
      stop();
      setView({ state: "loading" });
      // Advance the native queue in input order without waiting for video tickets or decoding.
      steps = steps.then(async () => {
        if (!alive || closing) return;
        try {
          const item = await call<Entry | null>("random_feed_step", { direction });
          if (!isCurrent(id)) return;
          lastApplied = id;
          if (!item?.count) {
            entry = undefined;
            setView({ state: "empty" });
            return;
          }
          entry = item;
          if (item.privacy) setView({ state: "hidden", entry: item });
          else open(item, id, direction, Math.min(remaining, item.count));
        } catch {
          if (!isCurrent(id)) return;
          lastApplied = id;
          setView({ state: "empty", entry, error: "无法读取播放队列" });
        }
      });
    }

    function navigate(direction: Direction) {
      setSkipped(false);
      step(direction);
    }

    function close() {
      if (closing) return;
      closing = true;
      ++revision;
      stop();
      void call("random_feed_close").catch(() => {
        if (!alive) return;
        closing = false;
        lastApplied = revision;
        setView({ state: "close-error", entry });
      });
    }

    function fullscreen() {
      if (document.fullscreenElement) void document.exitFullscreen().catch(() => {});
      else void surface.requestFullscreen().catch(() => {});
    }

    let wheelTime = -Infinity;
    let wheelDelta = 0;
    let wheelUsed = false;
    const onWheel = (event: WheelEvent) => {
      if (event.ctrlKey || event.deltaY === 0 || Math.abs(event.deltaX) > Math.abs(event.deltaY)) return;
      event.preventDefault();
      const now = performance.now();
      if (now - wheelTime > WHEEL_IDLE_MS) {
        wheelUsed = false;
        wheelDelta = 0;
      }
      wheelTime = now;
      if (wheelUsed) return;
      const unit = event.deltaMode === 1 ? 24 : event.deltaMode === 2 ? surface.clientHeight : 1;
      wheelDelta += event.deltaY * unit;
      if (Math.abs(wheelDelta) < WHEEL_THRESHOLD) return;
      wheelUsed = true;
      navigate(wheelDelta > 0 ? "next" : "previous");
    };

    const onKey = (event: KeyboardEvent) => {
      if (event.altKey || event.ctrlKey || event.metaKey) return;
      const key = event.key.toLowerCase();
      if (!["escape", "f", "arrowup", "arrowdown", "arrowleft", "arrowright", " "].includes(key)) return;
      if (key === " " && !player) return;
      event.preventDefault();
      event.stopImmediatePropagation();
      if (event.repeat && !["arrowleft", "arrowright"].includes(key)) return;
      switch (key) {
        case "escape":
          if (document.fullscreenElement) fullscreen();
          else close();
          break;
        case "f":
          fullscreen();
          break;
        case "arrowup":
          navigate("previous");
          break;
        case "arrowdown":
          navigate("next");
          break;
        case "arrowleft":
        case "arrowright":
          if (player && Number.isFinite(player.video.duration)) {
            const video = player.video;
            const seconds = key === "arrowleft" ? -5 : 5;
            video.currentTime = Math.max(0, Math.min(video.duration, video.currentTime + seconds));
          }
          break;
        case " ":
          if (player?.video.paused) play();
          else player?.video.pause();
          break;
      }
    };

    controls.current = {
      step: navigate, play, fullscreen, close,
      reveal: () => {
        if (!entry?.privacy || player || deadline !== undefined || !isCurrent(lastApplied)) return;
        open(entry, revision, "next", Math.min(MAX_SKIPS, entry.count));
      },
    };
    surface.addEventListener("wheel", onWheel, { capture: true, passive: false });
    window.addEventListener("keydown", onKey, true);
    // A discarded StrictMode mount should never advance the native queue.
    queueMicrotask(() => { if (alive) step("current"); });
    return () => {
      alive = false;
      controls.current = null;
      surface.removeEventListener("wheel", onWheel, true);
      window.removeEventListener("keydown", onKey, true);
      stop();
    };
  }, []);

  const item = view.entry;
  const showTitle = item && (!item.privacy || view.state === "playing" || view.state === "manual");
  return (
    <main className="random-feed">
      <header className="random-feed-header">
        <h1><Shuffle size={20} aria-hidden="true" />随机播放</h1>
        <div className="random-feed-actions">
          <Button type="button" variant="ghost" size="sm" aria-label="全屏" title="全屏 (F)" onClick={() => controls.current?.fullscreen()}>
            <Maximize size={17} aria-hidden="true" />
          </Button>
          <Button type="button" variant="ghost" size="sm" aria-label="关闭随机播放" title="关闭 (Esc)" onClick={() => controls.current?.close()}>
            <X size={20} aria-hidden="true" />
          </Button>
        </div>
      </header>
      <div className="random-feed-stage" ref={stage}>
        <div className="random-feed-player" ref={host} />
        {view.state !== "playing" && (
          <div className="random-feed-overlay">
            {view.state === "loading" && <p role="status">正在打开视频…</p>}
            {view.state === "hidden" && (
              <div className="random-feed-private">
                <p>隐私模式已隐藏视频</p>
                <Button type="button" onClick={() => controls.current?.reveal()}>
                  <Eye size={17} aria-hidden="true" />显示并播放
                </Button>
              </div>
            )}
            {view.state === "manual" && <Button type="button" onClick={() => controls.current?.play()}>播放视频</Button>}
            {view.state === "empty" && <p role="status">{view.error ?? "没有可播放的视频"}</p>}
            {view.state === "close-error" && <p role="alert">关闭失败，请重试。</p>}
          </div>
        )}
      </div>
      <footer className="random-feed-footer">
        <div className="random-feed-details" aria-live="polite">
          {showTitle && <strong title={item.title}>{item.title}</strong>}
          {item && (!item.privacy || showTitle) && <span>{item.library}</span>}
          {skipped && <span role="status">已跳过无法播放的视频</span>}
        </div>
        <div className="random-feed-actions">
          {item && <span className="random-feed-count" aria-label={`第 ${item.index + 1} 个，共 ${item.count} 个`}>{item.index + 1} / {item.count}</span>}
          <Button type="button" variant="outline" size="sm" aria-label="上一个视频" title="上一个 (↑)" disabled={!item?.canPrevious} onClick={() => controls.current?.step("previous")}>
            <ArrowUp size={17} aria-hidden="true" />
          </Button>
          <Button type="button" variant="outline" size="sm" aria-label="下一个视频" title="下一个 (↓)" disabled={!item?.count} onClick={() => controls.current?.step("next")}>
            <ArrowDown size={17} aria-hidden="true" />
          </Button>
        </div>
      </footer>
    </main>
  );
}
