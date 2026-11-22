import { useEffect, useRef, useState, type FormEvent } from "react";
import { Cloud, Film, Folder, HardDrive, Image as ImageIcon, RefreshCw } from "lucide-react";
import { call } from "./api";
import { bytes } from "./Preferences";
import { PrivateText, ProtectedImage } from "./Privacy";
import { enqueuePreview } from "./Collections";
import { Button } from "./components/ui/button";
import { Modal } from "./components/ui/dialog";
import type { Library, Payload } from "./types";
import { EmojiPicker, validEmoji } from "./EmojiPicker";
import "./cloud-library.css";
import { CloudMediaViewer, CloudPlaybackSettings, type CloudMediaTarget } from "./CloudMediaViewer";

export type CloudLocation = {
  id: number;
  kind: "local" | "cloud";
  state: string;
  parent?: string;
  label: string;
  verification?: string;
};
export type CloudItem = {
  id: number;
  content_id: number;
  media_id?: number;
  has_source?: boolean;
  thumbnail_key?: string;
  view_key?: string;
  title: string;
  text: string;
  author: string;
  kind: "image" | "video";
  filename: string;
  bytes: number;
  tags: string[];
  libraries: string[];
  locations: CloudLocation[];
};
export type CloudStatus = {
  available: boolean;
  logged_in: boolean;
  state: string;
  user_agreement_url?: string;
  privacy_policy_url?: string;
};
type CloudCatalog = { items: CloudItem[]; total: number; offset: number; page_size?: number; count?: number; local_count?: number; local_bytes?: number };
type CloudFile = { id: string; name: string; type: "directory" | "file"; size: number | string; preview_id?: number; thumbnail_key?: string; view_key?: string };
type CloudFolder = { id: string; name: string };
type CloudList = { items: CloudFile[]; has_more: boolean };
type CloudJob = { id: string; kind: string; state: string; message: string; name: string; cancellable?: boolean; retryable?: boolean; done?: number; total?: number; progress?: number };
type CloudJobCounts = { cancellable: number; retryable: number };
type CloudJobTarget = { id: string } | { all: true };
type CloudAction = (name: string, payload?: Payload, message?: string) => Promise<boolean>;
const CHUNK = 40;
const EMPTY_CATALOG: CloudCatalog = { items: [], offset: 0, total: 0 };
const ROOT: CloudFolder = { id: "", name: "光鸭根目录" };
const jobStates: Record<string, string> = {
  queued: "排队中", running: "运行中", pending: "排队中", importing: "正在导入",
  completed: "已完成", succeeded: "已完成", failed: "失败", partial: "部分完成",
  paused: "等待恢复", stopped: "已停止", cancelled: "已取消", interrupted: "已中断",
  cancelling: "正在取消", cancel_unknown: "取消结果待确认",
};
const activeJobStates = ["running", "queued", "importing", "pending", "cancelling"];
const jobCanCancel = (job: CloudJob) => job.cancellable ?? ["queued", "running", "paused"].includes(job.state.toLowerCase());
const jobCanRetry = (job: CloudJob) => job.retryable ?? ["paused", "interrupted"].includes(job.state.toLowerCase());
const locationStates: Record<string, string> = {
  available: "已保存", verified: "已保存", completed: "已保存", present: "已保存",
  missing: "已缺失", released: "已释放", unknown: "待核对", unverified: "待核对",
  unconfirmed: "待核对", unavailable: "账号不可用",
};

export function cloudLoginPending(status: CloudStatus) {
  return ["waiting", "login_pending", "pending"].includes(status.state.toLowerCase());
}

export function CloudLoginConsent({ status }: { status: CloudStatus }) {
  if (!cloudLoginPending(status)) return null;
  const policyUrl = (value?: string) => {
    if (!value) return undefined;
    try {
      const url = new URL(value);
      return url.protocol === "https:" && url.hostname === "app.guangyapan.com" ? url.href : undefined;
    } catch { return undefined; }
  };
  const agreement = policyUrl(status.user_agreement_url);
  const privacy = policyUrl(status.privacy_policy_url);
  return <p className="cloud-consent">请在浏览器完成登录。登录即授权 X2LT 通过光鸭连接访问你的云盘，并表示你同意光鸭的
    {agreement ? <a href={agreement} target="_blank" rel="noopener noreferrer">用户协议</a> : "用户协议"}和
    {privacy ? <a href={privacy} target="_blank" rel="noopener noreferrer">隐私政策</a> : "隐私政策"}。
  </p>;
}

export function isSavedLocation(location: CloudLocation) {
  return ["available", "verified", "completed", "present", "ready", "saved"].includes(location.state.toLowerCase());
}

export function CloudStorageBadges({ groups, total, onOpen }: {
  groups: CloudLocation[][];
  total: number;
  onOpen: (location: CloudLocation) => void;
}) {
  return <div className="cloud-storage-badges">
    {(["local", "cloud"] as const).map((kind) => {
      const rows = groups.map((locations) => locations.find((l) => l.kind === kind && isSavedLocation(l)));
      const saved = rows.filter(Boolean).length;
      const first = rows.find(Boolean);
      const unknown = groups.some((locations) => locations.some((l) => l.kind === kind && !isSavedLocation(l) && !["released", "missing"].includes(l.state.toLowerCase())));
      if (!saved && !unknown && (kind === "cloud" || groups.some((locations) => locations.some((location) => location.kind === "cloud")))) return null;
      const label = `${kind === "local" ? "本地" : "光鸭"} ${saved}/${total}${unknown ? " · 待核对" : ""}`;
      const Icon = kind === "local" ? HardDrive : Cloud;
      return first && saved ? <button className="cloud-location-badge" type="button" key={kind} onClick={() => onOpen(first)}><Icon size={13} />{label}</button> :
        <span className="cloud-location-badge" key={kind}><Icon size={13} />{label}</span>;
    })}
  </div>;
}

type CloudAppearance = { name: string; emoji?: string };
const defaultCloudAppearance: CloudAppearance = { name: "光鸭云盘" };

export function CloudLibraryCard({ status, onOpen, name, emoji, save }: {
  status: CloudStatus; onOpen: () => void; name?: string; emoji?: string;
  save: (payload: Payload) => Promise<boolean>;
}) {
  const [stats, setStats] = useState<CloudCatalog | null>(null);
  const appearance = { name: name?.trim() || defaultCloudAppearance.name, emoji };
  const [draft, setDraft] = useState<CloudAppearance | null>(null);
  const [saveError, setSaveError] = useState(false);
  const [saving, setSaving] = useState(false);
  useEffect(() => {
    let alive = true;
    void call<CloudCatalog>("cloud_catalog").then((result) => { if (alive) setStats(result); }).catch(() => {});
    return () => { alive = false; };
  }, [status]);
  async function saveAppearance(event: FormEvent) {
    event.preventDefault();
    if (saving || !draft?.name.trim() || (draft.emoji && !validEmoji(draft.emoji))) return;
    setSaving(true);
    try {
      const saved = await save({ cloud_card_name: draft.name.trim(), cloud_card_emoji: draft.emoji?.trim() || "" });
      if (saved) setDraft(null);
      setSaveError(!saved);
    } finally {
      setSaving(false);
    }
  }
  return <article className="collection-card cloud-library-card">
    <button className="collection-entry" aria-label={`进入${appearance.name}`} onClick={onOpen}>
      <span className="cloud-library-cover">{appearance.emoji || <Cloud size={45} strokeWidth={1.5} />}</span>
      <h3>{appearance.name} <small>云盘库</small></h3>
    </button>
    <p>{stats ? `${stats.count ?? stats.total} 个云端媒体 · ${stats.local_count ?? 0} 个已下载` : status.logged_in ? "已连接" : "待连接"}</p>
    {stats && <small>本地占用 {bytes(stats.local_bytes ?? 0)}</small>}
    <div className="collection-card-actions cloud-card-actions">
      <Button size="sm" variant="outline" onClick={() => { setSaveError(false); setDraft(appearance); }}>
        编辑信息
      </Button>
    </div>
    {draft && <Modal open title="编辑光鸭卡片" onClose={() => setDraft(null)}>
      <form className="library-form" onSubmit={saveAppearance}>
        <label>
          卡片名称
          <input autoFocus maxLength={80} value={draft.name}
            onChange={(event) => setDraft({ ...draft, name: event.target.value })} />
        </label>
        <EmojiPicker value={draft.emoji ?? ""} onChange={(emoji) => setDraft({ ...draft, emoji })} />
        {saveError && <p role="alert">无法保存本地卡片信息。</p>}
        <div className="modal-footer">
          <Button type="button" variant="outline" onClick={() => setDraft(defaultCloudAppearance)}>恢复默认</Button>
          <Button disabled={saving || !draft.name.trim() || (!!draft.emoji && !validEmoji(draft.emoji))} type="submit">保存卡片</Button>
        </div>
      </form>
    </Modal>}
  </article>;
}

function CloudThumbnail({ kind, extension, previewId, thumbnailKey, open }: {
  kind: "folder" | "image" | "video" | "file";
  extension?: string;
  previewId?: number;
  thumbnailKey?: string;
  open?: () => void;
}) {
  const element = useRef<HTMLDivElement>(null);
  const [loaded, setLoaded] = useState<{ key: string; src: string; local: boolean }>();
  const key = JSON.stringify([previewId, thumbnailKey]);
  useEffect(() => {
    if ((!previewId && !thumbnailKey) || !element.current) return;
    let active = true;
    const observer = new IntersectionObserver(([entry]) => {
      if (!entry.isIntersecting) return;
      observer.disconnect();
      void enqueuePreview(async () => {
        if (!active) return;
        const local = previewId ? await call<string | null>("cloud_preview", { id: previewId }) : null;
        const src = local || (active && thumbnailKey ? await call<string | null>("cloud_thumbnail", { key: thumbnailKey }) : null);
        if (active && src) setLoaded({ key, src, local: !!local });
      }).catch(() => {});
    });
    observer.observe(element.current);
    return () => { active = false; observer.disconnect(); };
  }, [key, previewId, thumbnailKey]);
  const Icon = { folder: Folder, image: ImageIcon, video: Film, file: Cloud }[kind];
  return <div ref={element} className="cloud-thumbnail" role={open ? "button" : undefined} tabIndex={open ? 0 : undefined}
    aria-label={open ? (kind === "image" ? "打开图片" : "播放视频") : undefined}
    onClick={open} onKeyDown={(event) => {
      if (open && event.target === event.currentTarget && ["Enter", " "].includes(event.key)) {
        event.preventDefault();
        open();
      }
    }}>
    {loaded && loaded.key === key
      ? <ProtectedImage src={loaded.src} alt={loaded.local ? "本地缩略图" : "光鸭缩略图"} className="cloud-thumb-image" />
      : <Icon size={34} strokeWidth={1.5} aria-hidden="true" />}
    {extension && <small>{extension.toUpperCase()}</small>}
  </div>;
}

function cloudFileKind(extension: string) {
  if (/^(jpe?g|png|webp|gif|bmp|avif)$/i.test(extension)) return "image";
  if (/^(mp4|mov|mkv|webm|m4v|avi|wmv|ts)$/i.test(extension)) return "video";
  return "file";
}

export function CloudFolderBrowser({ start, mode, submit, submitLabel }: {
  start?: string;
  mode: "import" | "upload" | "locate";
  submit?: (folder: CloudFolder, recursive: boolean) => Promise<boolean>;
  submitLabel?: string;
}) {
  const [trail, setTrail] = useState<CloudFolder[]>(start ? [ROOT, { id: start, name: "存储目录" }] : [ROOT]);
  const [page, setPage] = useState(0);
  const [recursive, setRecursive] = useState(false);
  const [pending, setPending] = useState(false);
  const pages = useRef(new Map<string, CloudList>());
  const requestedRefresh = useRef<string | null>(null);
  const [received, setReceived] = useState<{ key: string; value: CloudList } | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState("");
  const [refresh, setRefresh] = useState(0);
  const [chosen, setChosen] = useState<CloudFolder[]>([]);
  const [viewer, setViewer] = useState<CloudMediaTarget | null>(null);
  const current = trail[trail.length - 1];
  const pageKey = JSON.stringify([current.id, page]);
  const listing = received?.key === pageKey ? received.value : pages.current.get(pageKey);
  const navigate = (next: CloudFolder[]) => { setTrail(next); setPage(0); };
  useEffect(() => {
    let alive = true;
    const refreshPage = requestedRefresh.current === pageKey;
    if (refreshPage) requestedRefresh.current = null;
    setError("");
    setLoading(true);
    void call<CloudList>("cloud_list", { parent: current.id, page, ...(refreshPage ? { refresh: true } : {}) }).then((value) => {
      if (!alive) return;
      if (refreshPage) {
        const folderPrefix = JSON.stringify([current.id]).slice(0, -1) + ",";
        for (const key of pages.current.keys()) if (key.startsWith(folderPrefix)) pages.current.delete(key);
      }
      pages.current.delete(pageKey);
      pages.current.set(pageKey, value);
      if (pages.current.size > 48) pages.current.delete(pages.current.keys().next().value!);
      setReceived({ key: pageKey, value });
    }).catch((e: Error) => { if (alive) setError(e.message); })
      .finally(() => { if (alive) setLoading(false); });
    return () => { alive = false; };
  }, [current.id, page, pageKey, refresh]);
  async function confirm() {
    if (!submit) return;
    setPending(true);
    try {
      const folders = chosen.length ? chosen : [current];
      for (const folder of folders) {
        if (!(await submit(folder, mode === "import" && recursive))) return;
      }
    } finally { setPending(false); }
  }
  return <div className="cloud-folder-browser" aria-busy={loading || (!listing && !error)}>
    <nav className="cloud-folder-trail" aria-label="光鸭目录路径">
      {trail.map((folder, index) => <span key={folder.id}>
        {index > 0 && <span aria-hidden="true">/ </span>}
        {folder.id ? <PrivateText text={folder.name} compact onOpen={index === trail.length - 1 ? undefined : () => navigate(trail.slice(0, index + 1))} /> : <button type="button" className="text-btn" disabled={index === trail.length - 1} onClick={() => navigate([ROOT])}>{folder.name}</button>}
      </span>)}
      <Button size="sm" variant="ghost" aria-label="刷新光鸭目录" disabled={loading} onClick={() => {
        requestedRefresh.current = pageKey;
        setRefresh((value) => value + 1);
      }}><RefreshCw size={14} /></Button>
    </nav>
    {error && <p role="alert">{error}</p>}
    {!listing && !error && <p role="status">正在读取光鸭目录…</p>}
    {listing && !listing.items.length && <p>此目录为空。</p>}
    <ul className="cloud-files">
      {listing?.items.map((item) => {
        const extension = item.type === "file" ? item.name.match(/\.([a-z0-9]{1,8})$/i)?.[1] ?? "" : "";
        const kind = item.type === "directory" ? "folder" : cloudFileKind(extension);
        const open = mode !== "upload" && (kind === "image" || kind === "video") && (item.view_key || item.preview_id)
          ? () => setViewer({ name: item.name, kind, key: item.view_key, localId: item.preview_id })
          : undefined;
        return <li key={item.id} className="cloud-file-tile">
        <CloudThumbnail kind={kind} extension={extension} previewId={item.preview_id} thumbnailKey={item.thumbnail_key} open={open} />
        {mode === "import" && item.type === "directory" && <input type="checkbox" aria-label="选择文件夹" checked={chosen.some((folder) => folder.id === item.id)} onChange={(e) => setChosen((value) => e.target.checked ? [...value, item] : value.filter((folder) => folder.id !== item.id))} />}
        <PrivateText text={item.name} compact onOpen={item.type === "directory" ? () => navigate([...trail, item]) : open} />
        {item.type === "file" && <small>{bytes(Number(item.size))}</small>}
      </li>; })}
    </ul>
    {viewer && <CloudMediaViewer target={viewer} close={() => setViewer(null)} />}
    {!!listing && (page > 0 || listing.has_more) && <div className="cloud-actions">
      <Button size="sm" variant="outline" disabled={!page} onClick={() => setPage(page - 1)}>上一页</Button>
      <span>第 {page + 1} 页</span>
      <Button size="sm" variant="outline" disabled={!listing.has_more} onClick={() => setPage(page + 1)}>下一页</Button>
    </div>}
    {!!submit && <div className="cloud-actions">
      {mode === "import" && <label><input type="checkbox" checked={recursive} onChange={(e) => setRecursive(e.target.checked)} /> 同时导入子目录</label>}
      <Button disabled={!listing || pending} onClick={() => void confirm()}>
        {submitLabel ?? (mode === "upload" ? "上传到所选目录" : chosen.length ? `导入所选 ${chosen.length} 个目录` : "导入当前目录")}
      </Button>
    </div>}
  </div>;
}

export function CloudLocateDialog({ parent, close }: { parent: string; close: () => void }) {
  return <Modal open title="光鸭存储目录" onClose={close}><CloudFolderBrowser key={parent} start={parent} mode="locate" /></Modal>;
}

export function CloudUploadDialog({ size, count, close, send, status, refreshStatus }: {
  size: number; count: number; close: () => void; send: (parent: string) => Promise<boolean>;
  status: CloudStatus; refreshStatus: (login?: boolean) => Promise<void>;
}) {
  const [connecting, setConnecting] = useState(false);
  return <Modal open title="上传到光鸭" onClose={close}>
    <p>{count} 个媒体 · {bytes(size)}。选择归档根目录，文件将使用随机名称。</p>
    {status.logged_in ? <CloudFolderBrowser mode="upload" submit={async (folder) => {
      const ok = await send(folder.id);
      if (ok) close();
      return ok;
    }} /> : <>
      <Button variant="outline" disabled={connecting} onClick={async () => {
        setConnecting(true);
        try { await refreshStatus(!cloudLoginPending(status)); } finally { setConnecting(false); }
      }}>{cloudLoginPending(status) ? "检查连接" : "连接光鸭"}</Button>
      <CloudLoginConsent status={status} />
    </>}
  </Modal>;
}

export function CloudItemTools({ item, libraries, action, locate, refresh }: {
  item: CloudItem;
  libraries: Library[];
  action: CloudAction;
  locate: (location: CloudLocation) => void;
  refresh: () => void;
}) {
  const [target, setTarget] = useState("");
  const [release, setRelease] = useState<CloudLocation | null>(null);
  const [pending, setPending] = useState(false);
  const selected = libraries.find((library) => library.id === target) || libraries.find((library) => library.default) || libraries[0];
  const remote = item.locations.find((location) => location.kind === "cloud" && isSavedLocation(location));
  const locationLabel = (location: CloudLocation) => location.kind === "cloud" ? "光鸭" : "本地";
  async function apply(name: string, payload: Payload, message: string) {
    setPending(true);
    try {
      const ok = await action(name, payload, message);
      if (ok) refresh();
      return ok;
    } finally { setPending(false); }
  }
  return <div className="cloud-item-tools">
    <ul className="cloud-positions" aria-label="存储位置">
      {item.locations.map((location) => <li key={`${location.kind}:${location.id}`}>
        <span>{locationLabel(location)} · {locationStates[location.state.toLowerCase()] || location.state}{location.verification ? ` · ${location.verification}` : ""}</span>
        {isSavedLocation(location) && <Button size="sm" variant="outline" onClick={() => locate(location)}>打开{locationLabel(location)}目录</Button>}
        {location.kind === "cloud" && location.state === "unconfirmed" &&
          <Button size="sm" variant="outline" disabled={pending} onClick={() => void apply("cloud_check", { id: location.id }, "云端位置已核对")}>核对云端位置</Button>}
        {location.kind === "local" && isSavedLocation(location) && <>
          <Button size="sm" variant="outline" onClick={() => void action("cloud_open", { id: location.id })}>打开文件</Button>
          {remote && <Button size="sm" variant="outline" onClick={() => setRelease(location)}>释放本地空间</Button>}
        </>}
      </li>)}
    </ul>
    {selected && <div className="cloud-actions">
      <select aria-label="收藏或下载目标库" className="library-select" value={selected.id} onChange={(event) => setTarget(event.target.value)}>
        {libraries.map((library) => <option key={library.id} value={library.id}>{library.emoji} {library.name}</option>)}
      </select>
      <Button size="sm" variant="outline" disabled={pending} onClick={() => void apply("cloud_link", { id: item.id, library_id: selected.id, linked: !item.libraries.includes(selected.id) }, item.libraries.includes(selected.id) ? "已移出目标库" : "已加入目标库")}>
        {item.libraries.includes(selected.id) ? "移出目标库" : "添加到目标库"}
      </Button>
      {remote && <Button size="sm" variant="outline" disabled={pending || !selected.online} onClick={() => void apply("cloud_download", { id: remote.id, library_id: selected.id }, "已加入下载队列")}>保存到本地</Button>}
    </div>}
    {release && <Modal open title="释放本地空间" onClose={() => !pending && setRelease(null)}>
      <p>X2LT 会下载临时副本，与本地原文件核对 SHA-256。核对通过后，选中的本地副本才会移入回收站。正文、标签与云端位置会保留。</p>
      <div className="cloud-actions">
        <Button variant="outline" disabled={pending} onClick={() => setRelease(null)}>取消</Button>
        <Button disabled={pending} onClick={async () => { if (await apply("cloud_release", { id: release.id }, "核对任务已排队，可在光鸭云盘任务中查看")) setRelease(null); }}>核对并释放</Button>
      </div>
    </Modal>}
  </div>;
}

function CloudCatalogCard({ items, libraries, action, locate, refresh, checked, toggle }: {
  items: CloudItem[]; libraries: Library[]; action: CloudAction; locate: (location: CloudLocation) => void;
  refresh: () => void; checked: boolean; toggle: () => void;
}) {
  const first = items[0];
  const [picked, setPicked] = useState(first.id);
  const [tools, setTools] = useState(false);
  const [viewer, setViewer] = useState<CloudMediaTarget | null>(null);
  const [preview, setPreview] = useState<{ id: number; image: string | null } | null>(null);
  const item = items.find((row) => row.id === picked) || first;
  const localImage = item.kind === "image" ? item.locations.find((row) => row.kind === "local" && isSavedLocation(row)) : undefined;
  const media = new Map<number, CloudLocation[]>();
  for (const row of items) {
    const identity = row.media_id ?? row.id;
    media.set(identity, [...(media.get(identity) || []), ...row.locations]);
  }
  const [tagText, setTagText] = useState(first.tags.join(", "));
  async function submitTags(event: FormEvent) {
    event.preventDefault();
    const tags = [...new Set(tagText.split(/[,，]/).map((tag) => tag.trim().replace(/^#/, "")).filter(Boolean))];
    if (await action("cloud_tags", { id: item.id, tags }, "标签已保存")) refresh();
  }
  return <article className="cloud-content-card">
    <header><input type="checkbox" aria-label="选择云端内容" checked={checked} onChange={toggle} /><PrivateText text={first.title || first.filename} compact /></header>
    <CloudThumbnail kind={item.kind} previewId={localImage?.id} thumbnailKey={item.thumbnail_key}
      open={item.view_key || localImage ? () => setViewer({ name: item.filename, kind: item.kind, key: item.view_key, localId: localImage?.id }) : undefined} />
    {viewer && <CloudMediaViewer target={viewer} close={() => setViewer(null)} />}
    {first.text && first.text !== first.title && <PrivateText text={first.text} collapsible />}
    <CloudStorageBadges groups={[...media.values()]} total={media.size} onOpen={locate} />
    <div className="cloud-actions">
      <small>{media.size} 个媒体{items.length > media.size ? ` · ${items.length} 个版本` : ""} · {bytes(items.reduce((sum, row) => sum + row.bytes, 0))}</small>
      <Button size="sm" variant="outline" aria-expanded={tools} onClick={() => setTools(!tools)}>{tools ? "收起详情" : "管理位置与标签"}</Button>
    </div>
    {tools && <>
      {items.length > 1 && <select className="library-select" aria-label="选择媒体" value={item.id} onChange={(e) => setPicked(Number(e.target.value))}>
        {items.map((row, index) => <option key={row.id} value={row.id}>媒体 {index + 1} · {row.kind === "image" ? "图片" : "视频"}</option>)}
      </select>}
      <PrivateText text={item.filename} compact />
      <div className="cloud-actions">
        {item.has_source && <Button size="sm" variant="outline" onClick={() => void action("cloud_source", { id: item.id })}>打开原推文</Button>}
        {localImage && <Button size="sm" variant="outline" onClick={async () => {
          if (preview?.id === localImage.id) { setPreview(null); return; }
          try {
            const image = await call<string | null>("cloud_preview", { id: localImage.id });
            setPreview({ id: localImage.id, image });
          } catch { setPreview({ id: localImage.id, image: null }); }
        }}>{preview?.id === localImage.id ? "关闭预览" : "显示本地图片"}</Button>}
      </div>
      {preview && preview.id === localImage?.id && (preview.image ?
        <ProtectedImage className="cloud-local-preview" src={preview.image} alt="本地媒体预览" /> :
        <p>此图片无法生成缩略图，可打开原文件。</p>)}
      <form className="cloud-actions" onSubmit={(e) => void submitTags(e)}>
        <input aria-label="标签，逗号分隔" placeholder="标签，逗号分隔" value={tagText} onChange={(e) => setTagText(e.target.value)} />
        <Button type="submit" size="sm" variant="outline">保存标签</Button>
      </form>
      <CloudItemTools item={item} libraries={libraries} action={action} locate={locate} refresh={refresh} />
    </>}
  </article>;
}

export function CloudLibrary({ status, libraries, libraryId, action, locate, refreshStatus }: {
  status: CloudStatus;
  libraries: Library[];
  libraryId?: string;
  action: CloudAction;
  locate: (location: CloudLocation) => void;
  refreshStatus: (login?: boolean) => Promise<void>;
}) {
  const [view, setView] = useState<"catalog" | "browse" | "jobs">("catalog");
  const [browseVisited, setBrowseVisited] = useState(false);
  const [jobsVisited, setJobsVisited] = useState(false);
  const viewRef = useRef(view);
  viewRef.current = view;
  const [catalog, setCatalog] = useState<CloudCatalog>(EMPTY_CATALOG);
  const [jobs, setJobs] = useState<CloudJob[]>([]);
  const [jobCounts, setJobCounts] = useState<CloudJobCounts>({ cancellable: 0, retryable: 0 });
  const [jobsRevision, setJobsRevision] = useState(0);
  const [jobControlBusy, setJobControlBusy] = useState(false);
  const [jobControlError, setJobControlError] = useState("");
  const [jobControlNotice, setJobControlNotice] = useState("");
  const [cancelTarget, setCancelTarget] = useState<CloudJobTarget | null>(null);
  const [query, setQuery] = useState("");
  const [tag, setTag] = useState("");
  const [offset, setOffset] = useState(0);
  const [revision, setRevision] = useState(0);
  const [selected, setSelected] = useState<number[]>([]);
  const [target, setTarget] = useState("");
  const [error, setError] = useState("");
  const [jobsError, setJobsError] = useState("");
  const [pending, setPending] = useState(false);
  const [imported, setImported] = useState(false);
  const [loginBusy, setLoginBusy] = useState(false);
  const [previewSettings, setPreviewSettings] = useState(false);
  const [playbackSettings, setPlaybackSettings] = useState(false);
  const [previewRevision, setPreviewRevision] = useState(0);
  const groupName = libraryId ? "跨库收藏" : "光鸭媒体";
  const refresh = () => setRevision((value) => value + 1);
  const targetLibrary = libraries.find((row) => row.id === target) || libraries.find((row) => row.default) || libraries[0];
  const pageSize = catalog.page_size || CHUNK;
  const jobsContinuation = useRef(0);
  const jobSignature = useRef<string | undefined>(undefined);
  useEffect(() => {
    let alive = true;
    setError("");
    const timer = setTimeout(() => void call<CloudCatalog>("cloud_catalog", { ...(libraryId ? { library_id: libraryId } : {}), query, tag: tag.trim().replace(/^#/, ""), offset }).then((result) => {
      if (alive) setCatalog(result);
    }).catch((error: Error) => { if (alive) setError(error.message); }), 180);
    return () => { alive = false; clearTimeout(timer); };
  }, [libraryId, query, tag, offset, revision, status.logged_in]);
  useEffect(() => {
    if (!jobsVisited) return;
    const version = ++jobsContinuation.current;
    let timer: ReturnType<typeof setTimeout>;
    let running = true;
    function next() {
      if (viewRef.current !== "jobs" && !running) {
        timer = setTimeout(next, 2200);
        return;
      }
      void call<{ items: CloudJob[]; counts?: CloudJobCounts }>("cloud_jobs").then((result) => {
        if (jobsContinuation.current !== version) return;
        const latest = JSON.stringify(result.items.map(({ id, state }) => [id, state]));
        if (latest !== jobSignature.current) setRevision((value) => value + 1);
        jobSignature.current = latest;
        running = result.items.some((job) => activeJobStates.includes(job.state.toLowerCase()));
        setJobs(result.items);
        setJobCounts(result.counts ?? {
          cancellable: result.items.filter(jobCanCancel).length,
          retryable: result.items.filter(jobCanRetry).length,
        });
        setJobsError("");
        timer = setTimeout(next, 2200);
      }).catch((e: Error) => {
        if (jobsContinuation.current !== version) return;
        setJobsError(e.message);
        timer = setTimeout(next, 2200);
      });
    }
    next();
    return () => { ++jobsContinuation.current; clearTimeout(timer); };
  }, [jobsVisited, jobsRevision]);
  const grouped = new Map<number, CloudItem[]>();
  for (const item of catalog.items) {
    const existing = grouped.get(item.content_id);
    if (existing) existing.push(item);
    else grouped.set(item.content_id, [item]);
  }
  const switchView = (next: typeof view) => {
    if (next === "browse") setBrowseVisited(true);
    if (next === "jobs") setJobsVisited(true);
    setView(next);
  };
  const settle = async (name: string, payload: Payload, message: string) => {
    const ok = await action(name, payload, message);
    if (ok) refresh();
    return ok;
  };
  async function controlJobs(kind: "cancel" | "retry", target: CloudJobTarget) {
    if (jobControlBusy) return;
    setJobControlBusy(true);
    setJobControlError("");
    setJobControlNotice("");
    try {
      const result = await call<{ count: number }>(kind === "cancel" ? "cloud_cancel" : "cloud_retry", target);
      if (kind === "cancel") setCancelTarget(null);
      const count = result?.count;
      if (typeof count === "number") {
        setJobControlNotice(count === 0 ? "没有可处理的任务" : kind === "cancel"
          ? `已请求取消 ${count} 个任务` : `已安排重试 ${count} 个任务`);
      } else {
        setJobControlNotice(kind === "cancel" ? "已请求取消任务" : "已安排重试");
      }
    } catch (error) {
      setJobControlError((error as Error).message);
    } finally {
      setJobsRevision((value) => value + 1);
      setJobControlBusy(false);
    }
  }
  async function bulkDownload() {
    if (!targetLibrary) return;
    setPending(true);
    try {
      for (const item of catalog.items) {
        if (!selected.includes(item.content_id)) continue;
        const source = item.locations.find((location) => location.kind === "cloud" && isSavedLocation(location));
        if (source && !(await action("cloud_download", { id: source.id, library_id: targetLibrary.id }, "已加入下载队列"))) break;
      }
      switchView("jobs");
    } finally { setPending(false); }
  }
  const displayStatus = status.logged_in ? "已连接" : !status.available ? "连接组件不可用" : cloudLoginPending(status) ? "请在浏览器完成登录" : "尚未连接";
  return <section className="cloud-library-panel" aria-label={groupName}>
    <div className="cloud-actions cloud-library-tabs">
      <Button size="sm" variant={view === "catalog" ? "default" : "outline"} onClick={() => switchView("catalog")}>{groupName}</Button>
      {!libraryId && <><Button size="sm" variant={view === "browse" ? "default" : "outline"} onClick={() => switchView("browse")}>浏览并导入</Button>
      <Button size="sm" variant={view === "jobs" ? "default" : "outline"} onClick={() => switchView("jobs")}>云盘任务</Button></>}
    </div>
    {!libraryId && <div className="cloud-actions cloud-account-row">
      <span role="status">{displayStatus}</span>
      <Button size="sm" variant="outline" disabled={loginBusy} onClick={async () => {
        setLoginBusy(true);
        try { await refreshStatus(!status.logged_in && displayStatus !== "请在浏览器完成登录"); } finally { setLoginBusy(false); }
      }}>{status.logged_in || displayStatus === "请在浏览器完成登录" ? "检查连接" : "连接光鸭"}</Button>
      <Button size="sm" variant="outline" onClick={() => void settle("cloud_backup", {}, "索引备份已保存")}>备份索引</Button>
      <Button size="sm" variant="outline" onClick={() => void settle("cloud_restore", {}, "索引已恢复")}>恢复索引</Button>
      <Button size="sm" variant="outline" onClick={() => setPreviewSettings(true)}>缩略图设置</Button>
      <Button size="sm" variant="outline" onClick={() => setPlaybackSettings(true)}>在线播放授权</Button>
    </div>}
    {playbackSettings && <CloudPlaybackSettings close={() => setPlaybackSettings(false)} />}
    {previewSettings && <PreviewCredentials action={action} close={() => setPreviewSettings(false)} saved={() => {
      setPreviewSettings(false);
      setPreviewRevision((value) => value + 1);
      refresh();
    }} />}
    {!libraryId && <CloudLoginConsent status={status} />}
    {browseVisited && <div hidden={view !== "browse"}>{status.logged_in ? <CloudFolderBrowser key={previewRevision} mode="import" submit={async (folder, recursive) => {
      const result = await settle("cloud_import", { parent: folder.id, name: folder.name, recursive }, "目录导入已排队");
      if (result) setImported(true);
      return result;
    }} /> : <p>连接光鸭后可浏览目录。</p>}</div>}
    {view === "browse" && imported && <Button variant="outline" onClick={() => switchView("jobs")}>查看导入任务</Button>}
    <div className="cloud-catalog-view" hidden={view !== "catalog"}>
      <div className="cloud-actions">
        <input aria-label="搜索光鸭资料" value={query} placeholder="搜索正文、作者、文件名" onChange={(e) => { setQuery(e.target.value); setOffset(0); setSelected([]); }} />
        <input aria-label="按标签筛选光鸭资料" value={tag} placeholder="#标签" onChange={(e) => { setTag(e.target.value); setOffset(0); setSelected([]); }} />
        <Button size="sm" variant="outline" onClick={refresh}>刷新结果</Button>
      </div>
      {!!selected.length && targetLibrary && <div className="cloud-actions">
        <span>已选 {selected.length} 条内容</span>
        <select className="library-select" aria-label="批量下载目标库" value={targetLibrary.id} onChange={(e) => setTarget(e.target.value)}>{libraries.map((library) => <option key={library.id} value={library.id}>{library.name}</option>)}</select>
        <Button size="sm" disabled={pending || !targetLibrary.online} onClick={() => void bulkDownload()}>批量保存到本地</Button>
      </div>}
      <div className="cloud-catalog-grid">
        {[...grouped].map(([id, items]) => <CloudCatalogCard key={id} items={items} libraries={libraries} action={action} locate={locate} refresh={refresh} checked={selected.includes(id)} toggle={() => setSelected((values) => values.includes(id) ? values.filter((value) => value !== id) : [...values, id])} />)}
      </div>
      {!catalog.items.length && !error && <p>{query || tag ? "没有匹配的媒体。" : "暂无收藏内容。"}</p>}
      {catalog.total > pageSize && <div className="cloud-actions">
        <Button size="sm" variant="outline" disabled={!catalog.offset} onClick={() => { setOffset(Math.max(0, catalog.offset - pageSize)); setSelected([]); }}>上一页</Button>
        <span>{catalog.offset + 1}–{Math.min(catalog.total, catalog.offset + pageSize)} / {catalog.total}</span>
        <Button size="sm" variant="outline" disabled={catalog.offset + pageSize >= catalog.total} onClick={() => { setOffset(catalog.offset + pageSize); setSelected([]); }}>下一页</Button>
      </div>}
    {error && <p role="alert">{error}</p>}
    </div>
    {jobsVisited && <div hidden={view !== "jobs"}>
      <div className="cloud-actions cloud-job-toolbar" aria-label="批量任务控制">
        <Button size="sm" variant="outline" disabled={jobControlBusy || !jobCounts.cancellable}
          onClick={() => { setJobControlError(""); setCancelTarget({ all: true }); }}>
          取消待处理{jobCounts.cancellable ? `（${jobCounts.cancellable}）` : ""}
        </Button>
        <Button size="sm" variant="outline" disabled={jobControlBusy || !jobCounts.retryable}
          onClick={() => void controlJobs("retry", { all: true })}>
          批量重试{jobCounts.retryable ? `（${jobCounts.retryable}）` : ""}
        </Button>
        {jobControlNotice && <span role="status">{jobControlNotice}</span>}
      </div>
      <ul className="cloud-jobs">
      {jobs.map((job) => {
        const state = job.state.toLowerCase();
        const pct = typeof job.progress === "number" ? job.progress : job.total && typeof job.done === "number" ? (100 * job.done) / job.total : undefined;
        return <li key={job.id}>
          <div><strong>{{ import: "导入", upload: "上传", download: "下载", release: "释放本地副本" }[job.kind] || job.kind}</strong> <span>{jobStates[state] || job.state}</span></div>
          {job.name && <PrivateText text={job.name} compact />}
          {job.message && <PrivateText text={job.message} compact />}
          {activeJobStates.includes(state) && <progress aria-label="云盘任务进度" max={100} value={state === "cancelling" ? undefined : pct} />}
          {(jobCanCancel(job) || jobCanRetry(job)) && <div className="cloud-actions">
            {jobCanCancel(job) && <Button size="sm" variant="outline" disabled={jobControlBusy}
              onClick={() => { setJobControlError(""); setCancelTarget({ id: job.id }); }}>取消</Button>}
            {jobCanRetry(job) && <Button size="sm" variant="outline" disabled={jobControlBusy}
              onClick={() => void controlJobs("retry", { id: job.id })}>{state === "paused" ? "继续" : "重试"}</Button>}
          </div>}
        </li>;
      })}
      {!jobs.length && <li>暂无云盘任务。</li>}
      </ul>
      {jobsError && <p role="alert">{jobsError}</p>}
      {!cancelTarget && jobControlError && <p role="alert">{jobControlError}</p>}
    </div>}
    {cancelTarget && <Modal open title={"all" in cancelTarget ? "取消待处理任务" : "取消云盘任务"}
      onClose={() => { if (!jobControlBusy) setCancelTarget(null); }}>
      <p>已完成的文件会保留。正在执行的云端操作可能继续，最终结果以任务状态为准。</p>
      {jobControlError && <p role="alert">{jobControlError}</p>}
      <div className="cloud-actions">
        <Button variant="outline" disabled={jobControlBusy} onClick={() => setCancelTarget(null)}>返回</Button>
        <Button disabled={jobControlBusy} onClick={() => void controlJobs("cancel", cancelTarget)}>确认取消</Button>
      </div>
    </Modal>}
  </section>;
}

function PreviewCredentials({ action, close, saved }: {
  action: CloudAction;
  close: () => void;
  saved: () => void;
}) {
  const [client, setClient] = useState("");
  const [secret, setSecret] = useState("");
  const [saving, setSaving] = useState(false);
  async function save(event: FormEvent) {
    event.preventDefault();
    setSaving(true);
    try {
      if (await action("cloud_preview_credentials", { client_id: client, client_secret: secret }, "缩略图凭证已加密保存")) saved();
    } finally {
      setSaving(false);
    }
  }
  return <Modal open title="光鸭缩略图设置" onClose={saving ? () => {} : close}>
    <p>填写与当前光鸭账号对应的 TOKEN 管理开发者凭证。只需填写应用 ID 和密钥。</p>
    <form className="cloud-credentials" onSubmit={(event) => void save(event)}>
      <label>开发者应用 ID <input required autoComplete="off" value={client} onChange={(event) => setClient(event.target.value)} /></label>
      <label>开发者密钥 <input required type="password" autoComplete="new-password" value={secret} onChange={(event) => setSecret(event.target.value)} /></label>
      <Button disabled={saving || !client.trim() || !secret.trim()} type="submit">验证并保存</Button>
    </form>
  </Modal>;
}
