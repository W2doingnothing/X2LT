import {
  useCallback,
  useEffect,
  useMemo,
  useRef,
  useState,
  type CSSProperties,
  type FormEvent,
} from "react";
import {
  Download,
  Film,
  Image,
  Radio,
  Search,
  ShieldCheck,
} from "lucide-react";
import { call, desktop } from "./api";
import { Button } from "./components/ui/button";
import { bytes } from "./Preferences";
import { PrivateText, ProtectedImage, usePrivateReveal } from "./Privacy";
import type { Library } from "./types";
import "./telegram-downloads.css";

type Account = { authorized: boolean };
type MediaKind = "video" | "image";
type MediaMetadata = {
  id: string;
  title: string;
  kind: MediaKind;
  body?: string;
  message_key?: string;
  media_key?: string;
  sent_at?: number;
  forward_title?: string;
  forward_id?: string;
  avatar_key?: string;
  extension?: string;
  width?: number;
  height?: number;
};
type Media = MediaMetadata & {
  size: number;
  download_state?: "new" | JobState;
};
type Scan = {
  state: "scanning" | "ready" | "failed" | "cancelled";
  items: Media[];
  error?: string;
  libraryId?: string;
};
type DateRange = { start: string; end: string };
type Channel = { id: string; title: string };
type JobAction = "pause" | "resume" | "cancel" | "retry";
const jobStates = {
  queued: { label: "排队中", controls: ["pause", "cancel"] },
  downloading: { label: "下载中", controls: ["pause", "cancel"] },
  waiting: { label: "等待重试", controls: ["pause", "cancel"] },
  paused: { label: "已暂停", controls: ["resume", "cancel"] },
  failed: { label: "失败", controls: ["retry", "cancel"] },
  canceled: { label: "已取消", controls: ["retry"] },
  completed: { label: "已保存", controls: [] },
} satisfies Record<string, { label: string; controls: JobAction[] }>;
const jobActionLabels: Record<JobAction, string> = {
  pause: "暂停",
  resume: "继续",
  cancel: "取消",
  retry: "重试",
};
const jobActions = Object.keys(jobActionLabels) as JobAction[];
type JobState = keyof typeof jobStates;
type Job = MediaMetadata & {
  library_id: string;
  state: JobState;
  received: number;
  total: number;
  speed: number;
  error: string;
};
type CardMedia = {
  key: string;
  scan?: Media;
  job?: Job;
  metadata: MediaMetadata;
};
const noPendingMedia = new Set<string>();

function localDate(date: Date) {
  const month = String(date.getMonth() + 1).padStart(2, "0");
  const day = String(date.getDate()).padStart(2, "0");
  return `${date.getFullYear()}-${month}-${day}`;
}

function recentDates(days: number): DateRange {
  const end = new Date();
  const start = new Date(end);
  start.setDate(end.getDate() - days + 1);
  return { start: localDate(start), end: localDate(end) };
}

function groupMessages(items: Media[], jobs: Job[]) {
  const media = new Map<string, CardMedia>();
  for (const item of items) {
    const key = item.media_key || `scan:${item.id}`;
    media.set(key, { key, scan: item, metadata: item });
  }
  for (const job of jobs) {
    const key = job.media_key || `job:${job.id}`;
    const scan = media.get(key)?.scan;
    media.set(key, { key, scan, job, metadata: { ...job, ...scan } });
  }

  const messages = new Map<string, CardMedia[]>();
  for (const entry of media.values()) {
    const key = entry.metadata.message_key || `media:${entry.key}`;
    const group = messages.get(key);
    if (group) group.push(entry);
    else messages.set(key, [entry]);
  }
  return [...messages];
}

const messageDate = new Intl.DateTimeFormat("zh-CN", {
  dateStyle: "medium",
  timeStyle: "short",
});

const previewLoaders = new WeakMap<Element, () => void>();
let previewObserver: IntersectionObserver | undefined;

function TelegramThumbnail({
  scanId,
  itemId,
  jobId,
  avatarKey,
  kind,
  width,
  height,
}: {
  scanId: string;
  itemId?: string;
  jobId?: string;
  avatarKey?: string;
  kind: MediaKind | "avatar";
  width?: number;
  height?: number;
}) {
  const element = useRef<HTMLSpanElement>(null);
  const [image, setImage] = useState<string | null>(null);
  const avatar = kind === "avatar";
  useEffect(() => {
    const node = element.current;
    if (!node || (!itemId && !jobId && !avatarKey)) return;
    let alive = true;
    async function load() {
      let result: string | null = null;
      if (itemId && scanId) {
        result = await call<string | null>("tg_scan_preview", {
          id: itemId,
          scan_id: scanId,
          kind: avatar ? "avatar" : "media",
        }).catch(() => null);
      }
      if (!alive) return;
      if (!result?.startsWith("data:image/")) {
        if (avatar && avatarKey) {
          result =
            (
              await call<{ url: string | null }>("tg_forward_avatar", {
                key: avatarKey,
              }).catch(() => null)
            )?.url ?? null;
        } else if (!avatar && jobId) {
          result = await call<string | null>("tg_job_preview", {
            id: jobId,
          }).catch(() => null);
        }
      }
      if (alive && result?.startsWith("data:image/")) setImage(result);
    }

    if (typeof IntersectionObserver === "undefined") {
      void load();
    } else {
      previewObserver ??= new IntersectionObserver((entries, observer) => {
        for (const entry of entries) {
          if (!entry.isIntersecting) continue;
          observer.unobserve(entry.target);
          previewLoaders.get(entry.target)?.();
          previewLoaders.delete(entry.target);
        }
      });
      previewLoaders.set(node, () => void load());
      previewObserver.observe(node);
    }
    return () => {
      alive = false;
      previewLoaders.delete(node);
      previewObserver?.unobserve(node);
    };
  }, [scanId, itemId, jobId, avatar, avatarKey]);

  const Icon = kind === "video" ? Film : avatar ? Radio : Image;
  const ratio =
    width && height && width > 0 && height > 0
      ? Math.min(2.4, Math.max(0.75, width / height))
      : kind === "video"
        ? 16 / 9
        : 4 / 3;
  return (
    <span
      ref={element}
      className={avatar ? "telegram-avatar" : "telegram-thumbnail"}
      style={
        avatar
          ? undefined
          : ({ "--telegram-thumbnail-ratio": ratio } as CSSProperties)
      }
    >
      {image ? (
        <ProtectedImage
          src={image}
          alt={avatar ? "转发来源头像" : "媒体预览图"}
          onError={() => setImage(null)}
        />
      ) : (
        <Icon size={avatar ? 16 : 22} aria-hidden="true" />
      )}
    </span>
  );
}

function MediaProgress({
  state,
  received,
  total,
}: {
  state: JobState;
  received: number;
  total: number;
}) {
  const percent =
    state === "completed"
      ? 100
      : total > 0
        ? Math.max(0, Math.min(100, Math.round((100 * received) / total)))
        : undefined;
  return (
    <span
      className="telegram-ring"
      data-state={state}
      data-indeterminate={state === "downloading" && percent === undefined}
      role="progressbar"
      aria-label="媒体进度"
      aria-valuenow={percent}
      aria-valuetext={
        percent === undefined
          ? jobStates[state].label
          : `${jobStates[state].label} ${percent}%`
      }
    >
      <svg viewBox="0 0 36 36" aria-hidden="true">
        <circle className="telegram-ring-track" cx="18" cy="18" r="15" />
        <circle
          className="telegram-ring-value"
          cx="18"
          cy="18"
          r="15"
          pathLength="100"
          strokeDasharray={`${percent ?? (state === "downloading" ? 28 : 0)} 100`}
        />
      </svg>
      <span>{percent === undefined ? "—" : `${percent}%`}</span>
    </span>
  );
}

function mediaState(
  entry: CardMedia,
  statusesCurrent: boolean,
  pending: Set<string>,
) {
  return (
    entry.job?.state ??
    (pending.has(entry.key)
      ? "queued"
      : statusesCurrent
        ? (entry.scan?.download_state ?? "new")
        : undefined)
  );
}

function TelegramMessageCard({
  messageKey,
  items,
  scanId,
  indices,
  selected,
  changeSelection,
  locked,
  statusesCurrent,
  pending,
  authorized,
  control,
}: {
  messageKey: string;
  items: CardMedia[];
  scanId: string;
  indices: Map<string, number>;
  selected: Set<string>;
  changeSelection: (id: string, checked: boolean) => void;
  locked: boolean;
  statusesCurrent: boolean;
  pending: Set<string>;
  authorized: boolean;
  control: (action: JobAction, ids: string[]) => void;
}) {
  const caption = items.find(({ metadata }) => metadata.body)?.metadata.body;
  const source = items.find(
    ({ metadata }) => metadata.forward_title || metadata.forward_id,
  );
  const sentAt = items.find(({ metadata }) => metadata.sent_at)?.metadata
    .sent_at;
  const date = new Date((sentAt ?? NaN) * 1000);
  const actions = jobActions
    .map((action) => ({
      action,
      ids: items.flatMap(({ job }) =>
        job &&
        (jobStates[job.state].controls as readonly JobAction[]).includes(action)
          ? [job.id]
          : [],
      ),
    }))
    .filter(({ ids }) => ids.length);
  const jobEntries = items.filter((entry) => entry.job);
  return (
    <article
      className="telegram-message tweet-card"
      data-message-key={messageKey}
    >
      {(source || !Number.isNaN(date.getTime())) && (
        <header className="telegram-message-header">
          {source && (
            <div className="telegram-forward">
              <TelegramThumbnail
                scanId={scanId}
                itemId={
                  source.scan &&
                  (source.metadata.forward_id || source.metadata.avatar_key)
                    ? source.scan.id
                    : undefined
                }
                avatarKey={source.metadata.avatar_key}
                kind="avatar"
              />
              <div className="telegram-forward-name">
                <span className="telegram-status">转发自</span>
                <PrivateText
                  text={
                    source.metadata.forward_title ||
                    source.metadata.forward_id ||
                    ""
                  }
                  compact
                />
              </div>
            </div>
          )}
          {!Number.isNaN(date.getTime()) && (
            <time className="telegram-status" dateTime={date.toISOString()}>
              {messageDate.format(date)}
            </time>
          )}
        </header>
      )}
      {caption && <PrivateText text={caption} collapsible />}
      <ul className="telegram-media-list" data-single={items.length === 1}>
        {items.map((entry) => {
          const { key, scan, job, metadata } = entry;
          const state = mediaState(entry, statusesCurrent, pending);
          const kind = metadata.kind === "video" ? "视频" : "图片";
          const total = job?.total || scan?.size || 0;
          const received = job?.received ?? (state === "completed" ? total : 0);
          return (
            <li key={key} className="telegram-media-tile" data-media-key={key}>
              <div className="telegram-media-frame">
                <TelegramThumbnail
                  scanId={scanId}
                  itemId={scan?.id}
                  jobId={job?.id}
                  kind={metadata.kind}
                  width={metadata.width}
                  height={metadata.height}
                />
                {scan && (!state || state === "new") && (
                  <input
                    type="checkbox"
                    aria-label={`选择第 ${indices.get(scan.id)} 项`}
                    checked={selected.has(scan.id)}
                    disabled={locked || !statusesCurrent}
                    onChange={(event) =>
                      changeSelection(scan.id, event.target.checked)
                    }
                  />
                )}
              </div>
              <div className="telegram-media-meta">
                {state && state !== "new" && (
                  <MediaProgress
                    state={state}
                    received={received}
                    total={total}
                  />
                )}
                <div className="telegram-media-info">
                  {!caption && metadata.title && (
                    <PrivateText text={metadata.title} compact />
                  )}
                  <span className="telegram-status">
                    {kind}
                    {metadata.extension
                      ? ` · ${metadata.extension.replace(/^\./, "").toUpperCase()}`
                      : ""}
                    {total > 0 ? ` · ${bytes(total)}` : ""}
                  </span>
                  <span
                    className="telegram-status telegram-media-state"
                    data-state={state}
                  >
                    {state === "new"
                      ? "新媒体"
                      : state
                        ? jobStates[state].label
                        : "正在读取状态…"}
                  </span>
                  {state && state !== "new" && (
                    <span className="telegram-status">
                      {bytes(received)} / {total > 0 ? bytes(total) : "—"}
                      {state === "downloading" && job && job.speed > 0
                        ? ` · ${bytes(job.speed)}/s`
                        : ""}
                    </span>
                  )}
                </div>
              </div>
            </li>
          );
        })}
      </ul>
      {(actions.length > 0 || jobEntries.length > 0) && (
        <footer className="telegram-message-footer">
          {actions.length > 0 && (
            <div
              className="telegram-actions"
              role="group"
              aria-label="消息下载操作"
            >
              {actions.map(({ action, ids }) => (
                <Button
                  key={action}
                  size="sm"
                  variant="outline"
                  type="button"
                  disabled={
                    locked ||
                    (!authorized && (action === "resume" || action === "retry"))
                  }
                  onClick={() => control(action, ids)}
                >
                  {jobActionLabels[action]}
                </Button>
              ))}
            </div>
          )}
          {jobEntries.length > 0 && (
            <details className="telegram-media-details">
              <summary>媒体详情</summary>
              <ul>
                {jobEntries.map(
                  ({ key, job }) =>
                    job && (
                      <li key={key}>
                        <PrivateText
                          text={
                            job.title ||
                            (job.kind === "video" ? "视频" : "图片")
                          }
                          compact
                        />
                        <span className="telegram-status">
                          {jobStates[job.state].label}
                        </span>
                        {job.error && (
                          <div className="telegram-error">
                            <PrivateText text={job.error} compact />
                          </div>
                        )}
                      </li>
                    ),
                )}
              </ul>
            </details>
          )}
        </footer>
      )}
    </article>
  );
}

function TelegramWorkspace({
  active,
  libraries,
  defaultLibrary,
  onOpenAccount,
  accountReset,
}: {
  active: boolean;
  libraries: Library[];
  defaultLibrary: string | null;
  onOpenAccount: () => void;
  accountReset: number;
}) {
  const [account, setAccount] = useState<Account | null>(null);
  const [mode, setMode] = useState<"link" | "channel">("link");
  const [link, setLink] = useState("");
  const [channel, setChannel] = useState("");
  const [channels, setChannels] = useState<Channel[] | null>(null);
  const [limit, setLimit] = useState("50");
  const [rangeMode, setRangeMode] = useState<"recent" | "dates">("recent");
  const [dates, setDates] = useState(() => recentDates(7));
  const [mediaMode, setMediaMode] = useState<MediaKind | "all">("all");
  const [scanId, setScanId] = useState("");
  const [scan, setScan] = useState<Scan | null>(null);
  const [selection, setSelection] = useState<Record<string, boolean>>({});
  const [library, setLibrary] = useState("");
  const [jobs, setJobs] = useState<Job[] | null>(null);
  const [jobsError, setJobsError] = useState("");
  const [pendingMedia, setPendingMedia] = useState<{
    libraryId: string;
    keys: Set<string>;
  } | null>(null);
  const jobsRequest = useRef(0);
  const [busy, setBusy] = useState("");
  const [error, setError] = useState("");
  const [accountError, setAccountError] = useState("");
  const [notice, setNotice] = useState("");
  const channelPrivacy = usePrivateReveal(String(accountReset));
  const checking = scan?.state === "scanning";
  const target =
    libraries.find((item) => item.id === library) ??
    libraries.find((item) => item.id === defaultLibrary && item.online) ??
    libraries.find((item) => item.online) ??
    libraries[0];
  const targetId = target?.id;
  const authorized = account?.authorized === true;
  const statusesCurrent = authorized && scan?.libraryId === targetId;
  const items = authorized ? scan?.items : undefined;
  const messages = useMemo(
    () =>
      groupMessages(
        items ?? [],
        (jobs ?? []).filter((job) => job.library_id === targetId),
      ),
    [items, jobs, targetId],
  );
  const indices = useMemo(
    () => new Map(items?.map((item, index) => [item.id, index + 1])),
    [items],
  );
  const scanMessageCount = useMemo(
    () => groupMessages(items ?? [], []).length,
    [items],
  );
  const pending =
    pendingMedia && pendingMedia.libraryId === targetId
      ? pendingMedia.keys
      : noPendingMedia;
  const newIds = statusesCurrent
    ? messages.flatMap(([, media]) =>
        media.flatMap((entry) =>
          entry.scan && mediaState(entry, true, pending) === "new"
            ? [entry.scan.id]
            : [],
        ),
      )
    : [];
  const selected = new Set(newIds.filter((id) => selection[id] !== false));
  const source = (mode === "link" ? link : channel).trim();
  const dateRange = mode === "channel" && rangeMode === "dates";
  const invalidDates =
    dateRange && (!dates.start || !dates.end || dates.start > dates.end);

  async function run(action: string, operation: () => Promise<void>) {
    setBusy(action);
    setError("");
    setNotice("");
    try {
      await operation();
    } catch (failure) {
      setError(failure instanceof Error ? failure.message : "操作失败，请重试");
      const latest = await call<Account>("tg_status").catch(() => null);
      if (latest) setAccount(latest);
    } finally {
      setBusy("");
    }
  }

  const refreshJobs = useCallback(async () => {
    const request = ++jobsRequest.current;
    try {
      const result = await call<Job[]>("tg_jobs");
      if (request !== jobsRequest.current) return;
      setJobs(result.map((job) => ({ ...job })));
      setPendingMedia(null);
      setJobsError("");
    } catch {
      if (request === jobsRequest.current)
        setJobsError("无法获取下载任务，正在重试。");
    }
  }, []);

  useEffect(() => {
    setScanId("");
    setScan(null);
    setSelection({});
    setLink("");
    setChannel("");
    setChannels(null);
    setError("");
    setNotice("");
  }, [accountReset]);

  useEffect(() => {
    if (!active) {
      setAccount(null);
      return;
    }
    let alive = true;
    void call<Account>("tg_status")
      .then((result) => {
        if (alive) {
          setAccount(result);
          setAccountError("");
        }
      })
      .catch(() => {
        if (alive) {
          setAccount(null);
          setAccountError("无法读取 Telegram 账号状态，请重试。");
        }
      });
    return () => {
      alive = false;
    };
  }, [active, accountReset]);

  useEffect(() => {
    if (!active || !authorized || !scanId) return;
    let alive = true;
    let timer: number;
    async function poll() {
      try {
        const result = await call<Scan>("tg_scan_status", {
          id: scanId,
          ...(targetId ? { library_id: targetId } : {}),
        });
        if (!alive) return;
        setScan({ ...result, libraryId: targetId });
        if (result.state === "scanning") {
          timer = window.setTimeout(poll, 600);
        } else if (result.state === "failed") {
          const latest = await call<Account>("tg_status").catch(() => null);
          if (alive && latest) setAccount(latest);
        }
      } catch {
        if (alive) {
          setScan((current) => ({
            state: "failed",
            items: current?.items ?? [],
            libraryId: current?.libraryId,
            error: "无法获取扫描结果，请重新扫描。",
          }));
        }
      }
    }
    void poll();
    return () => {
      alive = false;
      window.clearTimeout(timer);
    };
  }, [active, authorized, scanId, targetId]);

  useEffect(() => {
    setSelection({});
  }, [targetId]);

  useEffect(() => {
    if (!active) return;
    let alive = true;
    let timer: number;
    async function poll() {
      await refreshJobs();
      if (alive) timer = window.setTimeout(poll, 1000);
    }
    void poll();
    return () => {
      alive = false;
      jobsRequest.current += 1;
      window.clearTimeout(timer);
    };
  }, [active, accountReset, refreshJobs]);

  function clearScan() {
    setScanId("");
    setScan(null);
    setSelection({});
  }

  function startScan(event: FormEvent) {
    event.preventDefault();
    if (!source || checking || busy || invalidDates) return;
    void run("tg_scan", async () => {
      clearScan();
      const result = await call<{ id: string }>("tg_scan", {
        mode,
        source,
        limit: Number(limit) || 50,
        media_mode: mediaMode,
        ...(dateRange ? { start_date: dates.start, end_date: dates.end } : {}),
      });
      setScanId(result.id);
      setScan({ state: "scanning", items: [] });
    });
  }

  function changeSelection(id: string, checked: boolean) {
    setSelection((previous) => ({ ...previous, [id]: checked }));
  }

  const canEnqueue =
    target?.online &&
    statusesCurrent &&
    Boolean(scan && scan.state !== "scanning");
  function enqueue() {
    if (!targetId || !selected.size || !canEnqueue) return;
    void run("tg_enqueue", async () => {
      const ids = [...selected];
      const submitted = new Set<string>();
      let count = 0;
      try {
        for (let start = 0; start < ids.length; start += 500) {
          const batch = ids.slice(start, start + 500);
          count += await call<number>("tg_enqueue", {
            scan_id: scanId,
            ids: batch,
            library_id: targetId,
          });
          for (const id of batch) submitted.add(id);
        }
      } finally {
        if (count) setNotice(`已提交 ${count} 项媒体下载。`);
        if (submitted.size) {
          setPendingMedia({
            libraryId: targetId,
            keys: new Set(
              messages.flatMap(([, media]) =>
                media.flatMap((entry) =>
                  entry.scan && submitted.has(entry.scan.id) ? [entry.key] : [],
                ),
              ),
            ),
          });
        }
        await refreshJobs();
      }
      if (!count) setNotice("没有新的下载任务。");
    });
  }

  function control(action: JobAction, ids: string[]) {
    if (!ids.length) return;
    void run("tg_control", async () => {
      try {
        for (let start = 0; start < ids.length; start += 500) {
          await call<boolean>("tg_control", {
            action,
            ids: ids.slice(start, start + 500),
          });
        }
      } finally {
        await refreshJobs();
      }
    });
  }

  const locked = Boolean(busy);
  const scanLocked = locked || checking;

  if (!active) return null;
  return (
    <div className="telegram-downloads">
      <h1>电报下载</h1>
      {accountError && (
        <p className="telegram-error" role="alert">
          {accountError}
        </p>
      )}
      {error && (
        <p className="telegram-error" role="alert">
          {error}
        </p>
      )}
      {notice && <p role="status">{notice}</p>}
      {account && !authorized && (
        <section className="settings-card telegram-panel telegram-account-link">
          <p>登录 Telegram 后可扫描和下载频道媒体。</p>
          <Button type="button" onClick={onOpenAccount}>
            <ShieldCheck size={16} />
            前往电报账号设置
          </Button>
        </section>
      )}

      {authorized && (
        <section
          className="settings-card telegram-panel"
          aria-labelledby="telegram-scan"
        >
          <div className="telegram-heading">
            <h2 id="telegram-scan">
              <Radio size={18} />
              媒体来源
            </h2>
          </div>
          <div className="telegram-actions" role="group" aria-label="来源方式">
            <Button
              variant={mode === "link" ? "default" : "outline"}
              type="button"
              aria-pressed={mode === "link"}
              disabled={scanLocked}
              onClick={() => {
                setMode("link");
                clearScan();
              }}
            >
              消息链接
            </Button>
            <Button
              variant={mode === "channel" ? "default" : "outline"}
              type="button"
              aria-pressed={mode === "channel"}
              disabled={scanLocked}
              onClick={() => {
                setMode("channel");
                clearScan();
              }}
            >
              频道
            </Button>
          </div>
          <form className="telegram-panel" onSubmit={startScan}>
            {mode === "link" ? (
              <label className="telegram-field">
                Telegram 消息链接
                <input
                  required
                  maxLength={512}
                  type="url"
                  placeholder="https://t.me/频道/消息编号"
                  value={link}
                  disabled={scanLocked}
                  onChange={(event) => {
                    setLink(event.target.value);
                    clearScan();
                  }}
                />
              </label>
            ) : (
              <>
                <div className="telegram-actions telegram-fields">
                  <Button
                    type="button"
                    variant="outline"
                    disabled={scanLocked}
                    onClick={() =>
                      void run("tg_channels", async () => {
                        channelPrivacy.reveal();
                        const result = await call<Channel[]>("tg_channels");
                        setChannels(result);
                        if (!result.some((item) => item.id === channel)) {
                          setChannel("");
                          clearScan();
                        }
                      })
                    }
                  >
                    {busy === "tg_channels" ? "正在载入…" : "载入频道"}
                  </Button>
                  {channels && !channelPrivacy.hidden && (
                    <label className="telegram-field">
                      已加入的频道
                      <select
                        disabled={scanLocked || !channels.length}
                        value={channel}
                        onChange={(event) => {
                          setChannel(event.target.value);
                          clearScan();
                        }}
                      >
                        <option value="">
                          {channels.length ? "选择频道" : "没有可用频道"}
                        </option>
                        {channels.map((item) => (
                          <option key={item.id} value={item.id}>
                            {item.title}
                          </option>
                        ))}
                      </select>
                    </label>
                  )}
                </div>
              </>
            )}
            <div className="telegram-actions telegram-fields">
              <label>
                媒体类型
                <select
                  value={mediaMode}
                  disabled={scanLocked}
                  onChange={(event) => {
                    setMediaMode(event.target.value as typeof mediaMode);
                    clearScan();
                  }}
                >
                  <option value="all">视频和图片</option>
                  <option value="video">仅视频</option>
                  <option value="image">仅图片</option>
                </select>
              </label>
              {mode === "channel" && (
                <label>
                  扫描范围
                  <select
                    value={rangeMode}
                    disabled={scanLocked}
                    onChange={(event) => {
                      setRangeMode(
                        event.target.value === "dates" ? "dates" : "recent",
                      );
                      clearScan();
                    }}
                  >
                    <option value="recent">最近 N 条消息</option>
                    <option value="dates">日期范围</option>
                  </select>
                </label>
              )}
              {mode === "channel" && !dateRange && (
                <label>
                  最近消息数
                  <input
                    type="number"
                    min="1"
                    max="500"
                    required
                    value={limit}
                    disabled={scanLocked}
                    onChange={(event) => {
                      setLimit(event.target.value);
                      clearScan();
                    }}
                  />
                </label>
              )}
            </div>
            {dateRange && (
              <div className="telegram-date-range">
                <div className="telegram-actions telegram-fields">
                  <label>
                    开始日期
                    <input
                      type="date"
                      required
                      max={dates.end || undefined}
                      value={dates.start}
                      disabled={scanLocked}
                      onChange={(event) => {
                        setDates({ ...dates, start: event.target.value });
                        clearScan();
                      }}
                    />
                  </label>
                  <label>
                    结束日期
                    <input
                      type="date"
                      required
                      min={dates.start || undefined}
                      value={dates.end}
                      disabled={scanLocked}
                      onChange={(event) => {
                        setDates({ ...dates, end: event.target.value });
                        clearScan();
                      }}
                    />
                  </label>
                  {[7, 30].map((days) => (
                    <Button
                      key={days}
                      type="button"
                      variant="outline"
                      disabled={scanLocked}
                      onClick={() => {
                        setDates(recentDates(days));
                        clearScan();
                      }}
                    >
                      最近 {days} 天
                    </Button>
                  ))}
                </div>
                <p className="telegram-status">
                  按本地日期，包含开始和结束当天。
                </p>
                {invalidDates && (
                  <p className="telegram-error" role="alert">
                    请选择有效的开始和结束日期。
                  </p>
                )}
              </div>
            )}
            <div className="telegram-actions">
              <Button
                type="submit"
                disabled={!source || scanLocked || invalidDates}
              >
                <Search size={16} />
                扫描媒体
              </Button>
              {checking && (
                <Button
                  type="button"
                  variant="outline"
                  disabled={locked}
                  onClick={() =>
                    void run("tg_scan_cancel", async () => {
                      await call<boolean>("tg_scan_cancel", { id: scanId });
                    })
                  }
                >
                  取消扫描
                </Button>
              )}
            </div>
          </form>
          {checking && (
            <p role="status">
              正在扫描媒体…
              {items?.length
                ? `已找到 ${scanMessageCount} 条消息，共 ${items.length} 项媒体。`
                : ""}
            </p>
          )}
          {scan?.state === "cancelled" && <p role="status">扫描已取消</p>}
          {scan?.state === "failed" && (
            <p className="telegram-error" role="alert">
              {scan.error || "扫描失败，请重试。"}
            </p>
          )}
          {scan?.state === "ready" && !scan.items.length && (
            <p role="status">没有找到符合条件的媒体。</p>
          )}
        </section>
      )}

      <section className="telegram-panel" aria-labelledby="telegram-media">
        <div className="telegram-heading">
          <h2 id="telegram-media">电报媒体</h2>
          {newIds.length > 0 && (
            <Button
              size="sm"
              variant="outline"
              disabled={locked}
              onClick={() => {
                const checked = selected.size !== newIds.length;
                setSelection((previous) => ({
                  ...previous,
                  ...Object.fromEntries(newIds.map((id) => [id, checked])),
                }));
              }}
            >
              {selected.size === newIds.length ? "取消全选" : "全选新媒体"}
            </Button>
          )}
        </div>
        <div className="telegram-actions telegram-fields">
          <label>
            目标媒体库
            <select
              disabled={locked || !libraries.length}
              value={targetId || ""}
              onChange={(event) => setLibrary(event.target.value)}
            >
              {!libraries.length && <option value="">没有可用媒体库</option>}
              {libraries.map((item) => (
                <option key={item.id} value={item.id}>
                  {item.name}
                  {!item.online ? "（离线）" : ""}
                </option>
              ))}
            </select>
          </label>
          {!!items?.length && (
            <Button
              type="button"
              disabled={locked || !selected.size || !canEnqueue}
              onClick={enqueue}
            >
              <Download size={16} />
              下载所选 {selected.size} 项
            </Button>
          )}
        </div>
        {jobsError && (
          <p className="telegram-error" role="alert">
            {jobsError}
          </p>
        )}
        {!messages.length && !jobsError && (
          <p className="telegram-status">
            {jobs ? "暂无媒体。" : "正在读取媒体…"}
          </p>
        )}
        <div className="telegram-messages">
          {messages.map(([key, media]) => (
            <TelegramMessageCard
              key={key}
              messageKey={key}
              items={media}
              scanId={scanId}
              indices={indices}
              selected={selected}
              changeSelection={changeSelection}
              locked={locked}
              statusesCurrent={statusesCurrent}
              pending={pending}
              authorized={authorized}
              control={control}
            />
          ))}
        </div>
      </section>
    </div>
  );
}

export function TelegramDownloads(
  props: Parameters<typeof TelegramWorkspace>[0],
) {
  if (!props.active && !desktop) return null;
  return desktop ? (
    <TelegramWorkspace {...props} />
  ) : (
    <section className="settings-card telegram-panel">
      <h1>电报下载</h1>
      <p>请在 Windows 桌面版使用 Telegram 下载。</p>
    </section>
  );
}
