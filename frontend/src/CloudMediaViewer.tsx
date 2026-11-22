import { useEffect, useRef, useState, type FormEvent } from "react";
import { call } from "./api";
import { Button } from "./components/ui/button";
import { Modal } from "./components/ui/dialog";
import { PrivateText } from "./Privacy";

export type CloudMediaTarget = {
  name: string;
  kind: "image" | "video";
  key?: string;
  localId?: number;
};

type PlaybackStatus = { state: string; connected: boolean; redirect?: string };

function PlaybackAuthorization({ connected }: { connected: () => void }) {
  const [client, setClient] = useState("");
  const [redirect, setRedirect] = useState("http://127.0.0.1:17863/x2lt/guangyapan");
  const [status, setStatus] = useState<PlaybackStatus>();
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");

  useEffect(() => {
    let disposed = false;
    void call<PlaybackStatus>("cloud_playback_status").then((value) => {
      if (disposed) return;
      setStatus(value);
      if (value.redirect) setRedirect(value.redirect);
    }).catch(() => {});
    return () => { disposed = true; };
  }, []);

  useEffect(() => {
    if (status?.state !== "waiting") return;
    let disposed = false;
    let timer = 0;
    async function check() {
      try {
        const result = await call<PlaybackStatus>("cloud_playback_status");
        if (disposed) return;
        setStatus(result);
        if (result.state === "connected") connected();
        else if (result.state === "waiting") timer = window.setTimeout(check, 1500);
      } catch (err) {
        if (!disposed) setError((err as Error).message);
      }
    }
    timer = window.setTimeout(check, 1500);
    return () => { disposed = true; window.clearTimeout(timer); };
  }, [status?.state, connected]);

  async function authorize(event: FormEvent) {
    event.preventDefault();
    setBusy(true);
    setError("");
    try {
      setStatus(await call<PlaybackStatus>("cloud_playback_connect", {
        client_id: client.trim(), redirect: redirect.trim(),
      }));
    } catch (err) {
      setError((err as Error).message);
    } finally { setBusy(false); }
  }

  const waiting = status?.state === "waiting";
  return <form className="cloud-credentials" onSubmit={(event) => void authorize(event)}>
    <p>在线播放需要已开通点播的光鸭开放平台应用，以及该应用登记的本机回调地址。</p>
    <label>开放平台应用 ID <input value={client} onChange={(event) => setClient(event.target.value)} required disabled={waiting} /></label>
    <label>本机回调地址 <input value={redirect} onChange={(event) => setRedirect(event.target.value)} required disabled={waiting} /></label>
    {waiting ? <p role="status">请在浏览器完成光鸭授权。</p> :
      <Button type="submit" disabled={busy || !client.trim() || !redirect.trim()}>在浏览器授权</Button>}
    {status?.state === "connected" && <p role="status">点播已授权</p>}
    {status?.state === "failed" && <p role="alert">授权失败，请核对应用 ID、回调地址和点播权限。</p>}
    {status?.state === "expired" && <p role="alert">授权等待超时，可以重试。</p>}
    {status?.state === "cancelled" && <p>已取消授权。</p>}
    {error && <p role="alert">{error}</p>}
  </form>;
}

export function CloudPlaybackSettings({ close }: { close: () => void }) {
  return <Modal open title="在线播放授权" onClose={close}>
    <PlaybackAuthorization connected={close} />
  </Modal>;
}

export function CloudMediaViewer({ target, close }: { target: CloudMediaTarget; close: () => void }) {
  const [media, setMedia] = useState<{ kind: string; url: string }>();
  const [error, setError] = useState("");
  const [authorization, setAuthorization] = useState(false);
  const [revision, setRevision] = useState(0);
  const video = useRef<HTMLVideoElement>(null);
  const position = useRef(0);
  const sequence = useRef(0);

  useEffect(() => {
    if (authorization) return;
    const version = ++sequence.current;
    setError("");
    setMedia(undefined);
    const request = (payload: { key?: string; local_id?: number; retry?: boolean }) => call<{ kind: string; url: string }>("cloud_media_view", payload);
    const payload = { ...(target.key ? { key: target.key } : {}), retry: revision > 0 };
    const work = target.localId && target.kind === "image"
      ? request({ local_id: target.localId }).catch((error) => target.key ? request(payload) : Promise.reject(error))
      : request(payload);
    void work.then((result) => { if (sequence.current === version) setMedia(result); })
      .catch((err) => { if (sequence.current === version) setError((err as Error).message); });
    return () => { ++sequence.current; };
  }, [target.key, target.localId, target.kind, revision, authorization]);

  const refresh = () => {
    position.current = video.current?.currentTime || 0;
    setRevision((value) => value + 1);
  };

  return <Modal open title={target.kind === "video" ? "在线播放" : "图片预览"} onClose={close}>
    <PrivateText text={target.name} compact />
    {authorization ? <PlaybackAuthorization connected={() => setAuthorization(false)} /> :
      <div className="cloud-viewer">
        {!media && !error && <p role="status">正在打开媒体…</p>}
        {media?.kind === "image" && <img src={media.url} alt="打开的光鸭图片" />}
        {media?.kind === "video" && <video key={revision} ref={video} controls playsInline preload="metadata" src={media.url}
          onLoadedMetadata={(event) => {
            if (position.current > 0 && position.current < event.currentTarget.duration) event.currentTarget.currentTime = position.current;
          }}
          onError={() => setError("视频无法播放。可刷新点播地址，或下载后使用系统播放器。")} />}
        {error && <p role="alert">{error}</p>}
        {target.kind === "video" && error && <div className="cloud-actions">
          <Button size="sm" variant="outline" onClick={refresh}>刷新点播地址</Button>
          <Button size="sm" variant="outline" onClick={() => setAuthorization(true)}>连接在线播放</Button>
        </div>}
      </div>}
  </Modal>;
}
