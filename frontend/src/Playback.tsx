import {
  createContext, useCallback, useContext, useEffect, useRef, useState,
  type ReactNode,
} from "react";
import type Artplayer from "artplayer";
import { call } from "./api";
import { Button } from "./components/ui/button";
import { Modal } from "./components/ui/dialog";
import "./playback.css";

type Target = { id: number; source: "local" | "x"; location_id?: number };
type Session = { url: string; token: string };
type Request = { target: Target; sequence: number };
type View = "loading" | "ready" | "manual" | "error";

const PlaybackContext = createContext<((target: Target) => void) | null>(null);

export function usePlayback() {
  const play = useContext(PlaybackContext);
  if (!play) throw Error("视频播放组件未就绪");
  return play;
}

export function PlaybackProvider({ children }: { children: ReactNode }) {
  const [request, setRequest] = useState<Request | null>(null);
  const sequence = useRef(0);
  const opener = useRef<{ element: HTMLElement | null; scroll: number } | null>(null);
  const play = useCallback((target: Target) => {
    opener.current = {
      element: document.activeElement instanceof HTMLElement ? document.activeElement : null,
      scroll: window.scrollY,
    };
    setRequest({ target, sequence: ++sequence.current });
  }, []);
  const close = useCallback(() => {
    const closed = sequence.current;
    const prior = opener.current;
    opener.current = null;
    setRequest(null);
    requestAnimationFrame(() => {
      if (!prior || closed !== sequence.current) return;
      if (prior.element?.isConnected) prior.element.focus({ preventScroll: true });
      if (window.scrollY !== prior.scroll) window.scrollTo(0, prior.scroll);
    });
  }, []);
  return (
    <PlaybackContext.Provider value={play}>
      {children}
      {request && <PlaybackDialog key={request.sequence} target={request.target} close={close} />}
    </PlaybackContext.Provider>
  );
}

function release(token: string) {
  void call("video_close", { token }).catch(() => {});
}

function loopbackUrl(value: string) {
  const url = new URL(value);
  if (url.protocol !== "http:" || url.hostname !== "127.0.0.1" || !url.port ||
      url.username || url.password) {
    throw Error("本地播放地址不可用");
  }
  return url.href;
}

function PlaybackDialog({ target, close }: { target: Target; close: () => void }) {
  const host = useRef<HTMLDivElement>(null);
  const player = useRef<Artplayer | null>(null);
  const alive = useRef(false);
  const [view, setView] = useState<View>("loading");
  const [error, setError] = useState("");
  const [openingExternal, setOpeningExternal] = useState(false);

  function playVideo(instance: Artplayer, isCurrent: () => boolean) {
    void instance.video.play().catch((reason: unknown) => {
      if (!isCurrent()) return;
      if (reason instanceof DOMException && reason.name === "NotAllowedError") {
        setView("manual");
      }
    });
  }

  useEffect(() => {
    alive.current = true;
    let current = true;
    let session: Session | undefined;
    const ticket = call<Session>("video_open", target).then((result) => {
      if (!current) release(result.token);
      else session = result;
      return result;
    });
    void Promise.all([ticket, import("artplayer")]).then(([result, module]) => {
      if (!current || !host.current) return;
      const ArtPlayer = module.default;
      ArtPlayer.LOG_VERSION = false;
      ArtPlayer.RECONNECT_TIME_MAX = 0;
      const instance = new ArtPlayer({
        container: host.current,
        url: loopbackUrl(result.url),
        lang: "zh-cn",
        theme: "var(--accent)",
        autoplay: true,
        playsInline: true,
        setting: true,
        playbackRate: true,
        fullscreen: true,
        moreVideoAttr: { crossOrigin: "anonymous", preload: "metadata" },
      });
      player.current = instance;
      let ready = false;
      const onReady = () => {
        if (!current || ready) return;
        ready = true;
        setView("ready");
        if (instance.video.paused) playVideo(instance, () => current);
      };
      const onError = () => {
        if (!current) return;
        setView("error");
        setError("应用内无法播放这个视频，可以使用系统播放器。");
      };
      instance.on("video:canplay", onReady);
      instance.on("video:playing", () => { if (current) setView("ready"); });
      instance.on("video:error", onError);
      if (instance.video.error) onError();
      else if (instance.video.readyState >= 3) onReady();
    }).catch((reason: unknown) => {
      if (!current) return;
      setError(reason instanceof Error ? reason.message : "无法打开视频");
      setView("error");
      current = false;
      if (session) release(session.token);
      session = undefined;
    });
    return () => {
      current = false;
      alive.current = false;
      player.current?.destroy();
      player.current = null;
      if (session) release(session.token);
    };
  }, [target]);

  async function openExternal() {
    setOpeningExternal(true);
    try {
      if (target.source === "local") {
        await call("local_media_open", { id: target.id, location_id: target.location_id });
      } else {
        await call("open_media", { job_id: target.id });
      }
      if (alive.current) close();
    } catch (reason) {
      if (!alive.current) return;
      setError(reason instanceof Error ? reason.message : "系统播放器无法打开文件");
      setOpeningExternal(false);
    }
  }

  return (
    <Modal open onClose={close} title="播放视频">
      <div className="playback">
        <div className="playback-stage" ref={host} />
        {view === "loading" && <p role="status">正在打开视频…</p>}
        {error && <p role="alert">{error}</p>}
        <div className="playback-actions">
          {view === "manual" && (
            <Button type="button" onClick={() => {
              if (player.current) playVideo(player.current, () => alive.current);
            }}>播放</Button>
          )}
          <Button type="button" variant="outline" disabled={openingExternal}
            onClick={() => void openExternal()}>用系统播放器打开</Button>
        </div>
      </div>
    </Modal>
  );
}
