import {
  useCallback,
  useEffect,
  useMemo,
  useRef,
  useState,
  type CSSProperties,
} from "react";
import {
  Check,
  CheckCircle2,
  ChevronLeft,
  ChevronRight,
  Download,
  Film,
  Image,
  Play,
  Radio,
  Search,
  ShieldCheck,
} from "lucide-react";
import { call, desktop } from "./api";
import { Button } from "./components/ui/button";
import { Modal } from "./components/ui/dialog";
import { LibraryCard, LibraryCardSource } from "./LibraryCard";
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
  card_key?: string;
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
type Backfill = {
  id?: string;
  state: Scan["state"] | "interrupted";
  library_id: string;
  found?: number;
  added?: number;
  error?: string;
};
type DateRange = { start: string; end: string };
type Channel = { id: string; title: string };
type ScanSource = "channel" | "saved" | "link";
type JobAction = "pause" | "resume" | "cancel" | "retry";
type RemovedJob = { library_id: string; media_key: string };
const jobStates = {
  deferred: { label: "待入队", controls: ["pause", "cancel"] },
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
    const key =
      entry.metadata.card_key ||
      entry.metadata.message_key ||
      `media:${entry.key}`;
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
}: {
  scanId: string;
  itemId?: string;
  jobId?: string;
  avatarKey?: string;
  kind: MediaKind | "avatar";
}) {
  const element = useRef<HTMLSpanElement>(null);
  const [image, setImage] = useState<string | null>(null);
  const avatar = kind === "avatar";
  useEffect(() => {
    const node = element.current;
    if (!node) return;
    if (!itemId && !jobId && !avatarKey) return;
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
      if (!alive) return;
      if (result?.startsWith("data:image/")) setImage(result);
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
  return (
    <span
      ref={element}
      className={avatar ? "telegram-avatar" : "telegram-thumbnail"}
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
  taskChecked,
  changeTaskChecked,
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
  taskChecked: boolean;
  changeTaskChecked: (checked: boolean) => void;
  locked: boolean;
  statusesCurrent: boolean;
  pending: Set<string>;
  authorized: boolean;
  control: (action: JobAction, ids: string[]) => void;
}) {
  const [offset, setOffset] = useState(0);
  const pageSize = 3;
  const first = Math.min(
    offset,
    Math.floor((items.length - 1) / pageSize) * pageSize,
  );
  const states = items.map((entry) =>
    mediaState(entry, statusesCurrent, pending),
  );
  const saved = states.filter((state) => state === "completed").length;
  const videos = items.filter(
    ({ metadata }) => metadata.kind === "video",
  ).length;
  const newMedia = items.flatMap(({ scan }, index) =>
    scan && states[index] === "new" ? [scan.id] : [],
  );
  const allSelected = newMedia.every((id) => selected.has(id));
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
    <LibraryCard
      source="telegram"
      className={`telegram-message tweet-card${taskChecked ? " selected" : ""}`}
      data-message-key={messageKey}
    >
      <aside className="message-tags telegram-task-selection">
        {jobEntries.length > 0 && (
          <label className="telegram-task-checkbox">
            <input
              type="checkbox"
              aria-label="选择此任务"
              checked={taskChecked}
              disabled={locked}
              onChange={(event) => changeTaskChecked(event.target.checked)}
            />
            选择任务
          </label>
        )}
        {newMedia.length > 1 && (
          <button
            type="button"
            className="text-btn"
            disabled={locked || !statusesCurrent}
            onClick={() =>
              newMedia.forEach((id) => changeSelection(id, !allSelected))
            }
          >
            {allSelected ? "取消本卡全选" : "全选本卡媒体"}
          </button>
        )}
      </aside>
      <header className="tweet-head library-media-card-header">
        <div className="telegram-message-identity">
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
          <LibraryCardSource source="telegram" />
          {!Number.isNaN(date.getTime()) && (
            <time className="telegram-status" dateTime={date.toISOString()}>
              {messageDate.format(date)}
            </time>
          )}
          {caption && <PrivateText text={caption} collapsible />}
          {!caption && items[0].metadata.title && (
            <PrivateText text={items[0].metadata.title} compact />
          )}
        </div>
        {jobEntries.length > 0 && (
          <span
            className={`status ${saved === items.length ? "success" : "muted"}`}
          >
            {saved === items.length && <CheckCircle2 size={12} />}
            {saved === items.length
              ? "已完成"
              : states.includes("downloading")
                ? "下载中"
                : states.includes("failed")
                  ? "有失败"
                  : saved
                    ? "部分完成"
                    : "待完成"}
          </span>
        )}
      </header>
      <div className="media-grid telegram-media-grid">
        {items.slice(first, first + pageSize).map((entry) => {
          const { key, scan, job, metadata } = entry;
          const state = mediaState(entry, statusesCurrent, pending);
          const Icon = metadata.kind === "video" ? Film : Image;
          const total = job?.total || scan?.size || 0;
          const received = job?.received ?? (state === "completed" ? total : 0);
          const ratio =
            metadata.width && metadata.height
              ? metadata.width / metadata.height
              : metadata.kind === "video"
                ? 16 / 9
                : 4 / 3;
          return (
            <div key={key} className="telegram-media-tile" data-media-key={key}>
              <div
                className="telegram-media-frame media-tile"
                style={{ "--telegram-thumbnail-ratio": ratio } as CSSProperties}
              >
                <TelegramThumbnail
                  scanId={scanId}
                  itemId={scan?.id}
                  jobId={job?.id}
                  kind={metadata.kind}
                />
                <span className="media-type telegram-media-type">
                  <Icon size={13} />
                  {metadata.extension?.replace(/^\./, "").toUpperCase() ||
                    (metadata.kind === "video" ? "视频" : "图片")}
                </span>
                {state === "completed" && (
                  <span className="media-done">
                    <Check size={12} />
                    已保存
                  </span>
                )}
                {total > 0 && (
                  <span className="media-size">{bytes(total)}</span>
                )}
                {state === "downloading" && (
                  <div className="telegram-frame-progress">
                    <MediaProgress
                      state={state}
                      received={received}
                      total={total}
                    />
                  </div>
                )}
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
              {state !== "completed" && (
                <div className="telegram-media-info">
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
              )}
            </div>
          );
        })}
      </div>
      <footer className="tweet-footer telegram-message-footer library-media-card-footer">
        <span>
          <Film size={13} />
          {videos} 视频 <i>·</i>
          <Image size={13} />
          {items.length - videos} 图片
          <span className="footer-divider" />
          {saved}/{items.length} 已保存
        </span>
        <div className="card-actions">
          {items.length > pageSize && (
            <div className="pager">
              <small>
                {first + 1}–{Math.min(first + pageSize, items.length)} /{" "}
                {items.length}
              </small>
              <button
                type="button"
                className="icon-btn"
                aria-label="上一组媒体"
                disabled={first === 0}
                onClick={() => setOffset(first - pageSize)}
              >
                <ChevronLeft size={14} />
              </button>
              <button
                type="button"
                className="icon-btn"
                aria-label="下一组媒体"
                disabled={first + pageSize >= items.length}
                onClick={() => setOffset(first + pageSize)}
              >
                <ChevronRight size={14} />
              </button>
            </div>
          )}
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
        </div>
      </footer>
    </LibraryCard>
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
  const [mode, setMode] = useState<ScanSource>("channel");
  const [link, setLink] = useState("");
  const [channel, setChannel] = useState("");
  const [channels, setChannels] = useState<Channel[] | null>(null);
  const [loadingChannels, setLoadingChannels] = useState(false);
  const [channelsError, setChannelsError] = useState("");
  const channelRequest = useRef(0);
  const prefetched = useRef<number | undefined>(undefined);
  const [limit, setLimit] = useState("50");
  const [rangeMode, setRangeMode] = useState<"recent" | "dates">("recent");
  const [dates, setDates] = useState(() => recentDates(7));
  const [mediaMode, setMediaMode] = useState<MediaKind | "all">("all");
  const [scanId, setScanId] = useState("");
  const [scan, setScan] = useState<Scan | null>(null);
  const [backfill, setBackfill] = useState<Backfill | null>(null);
  const [incrementalScan, setIncrementalScan] = useState(false);
  const [selection, setSelection] = useState<Record<string, boolean>>({});
  const [library, setLibrary] = useState("");
  const [jobs, setJobs] = useState<Job[] | null>(null);
  const [confirmCancelAll, setConfirmCancelAll] = useState(false);
  const [deletePartial, setDeletePartial] = useState(false);
  const [taskSelection, setTaskSelection] = useState<Set<string>>(new Set());
  const [confirmDelete, setConfirmDelete] = useState(false);
  const [dismissed, setDismissed] = useState<Set<string>>(new Set());
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
  const items = useMemo(
    () =>
      authorized
        ? scan?.items.filter(
            (item) =>
              !dismissed.has(`${targetId}|${item.media_key}`) &&
              (!incrementalScan ||
                mediaMode === "all" ||
                item.kind === mediaMode),
          )
        : undefined,
    [authorized, scan, dismissed, targetId, incrementalScan, mediaMode],
  );
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
  const chosenJobs = messages.flatMap(([key, media]) =>
    taskSelection.has(key)
      ? media.flatMap(({ job }) => (job ? [job.id] : []))
      : [],
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
  const source = (
    mode === "link" ? link : mode === "channel" ? channel : ""
  ).trim();
  const hasSource = mode === "saved" || Boolean(source);
  const dateRange = mode !== "link" && rangeMode === "dates";
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

  const loadChannels = useCallback(async () => {
    const request = ++channelRequest.current;
    setLoadingChannels(true);
    setChannelsError("");
    try {
      const result = await call<Channel[]>("tg_channels");
      if (request !== channelRequest.current) return;
      setChannels(result);
      setChannel((current) =>
        result.some((item) => item.id === current) ? current : "",
      );
    } catch {
      if (request === channelRequest.current)
        setChannelsError("无法载入频道，请重试。");
    } finally {
      if (request === channelRequest.current) setLoadingChannels(false);
    }
  }, []);

  useEffect(() => {
    setScanId("");
    setScan(null);
    setIncrementalScan(false);
    setSelection({});
    setTaskSelection(new Set());
    setDismissed(new Set());
    setLink("");
    setChannel("");
    setChannels(null);
    setLoadingChannels(false);
    setChannelsError("");
    setAccount(null);
    channelRequest.current += 1;
    setError("");
    setNotice("");
  }, [accountReset]);

  useEffect(() => {
    let alive = true;
    void call<Account>("tg_status")
      .then((result) => {
        if (alive) {
          setAccount(result);
          setAccountError("");
          if (result?.authorized && prefetched.current !== accountReset) {
            prefetched.current = accountReset;
            void loadChannels();
          }
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
  }, [active, accountReset, loadChannels]);

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
    setTaskSelection(new Set());
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
    setIncrementalScan(false);
    setSelection({});
    setDismissed(new Set());
  }

  useEffect(() => {
    if (!active || !authorized) return;
    let disposed = false;
    let timer: number;
    async function poll() {
      const status = await call<Backfill | null>("tg_backfill_status").catch(() => null);
      if (disposed) return;
      setBackfill(status);
      timer = window.setTimeout(poll, status?.state === "scanning" ? 1000 : 3000);
    }
    void poll();
    return () => {
      disposed = true;
      window.clearTimeout(timer);
    };
  }, [active, authorized]);

  function startBackfill(resume = false) {
    if (!targetId || busy || checking || backfill?.state === "scanning") return;
    void run("tg_backfill", async () => {
      const result = resume
        ? await call<{ id: string } | null>("tg_backfill_resume")
        : await call<{ id: string }>("tg_scan", {
            mode,
            source,
            limit: 50,
            media_mode: mediaMode,
            backfill: true,
            library_id: targetId,
            ...(dateRange ? { start_date: dates.start, end_date: dates.end } : {}),
          });
      if (result) {
        clearScan();
        setBackfill({ id: result.id, state: "scanning", library_id: targetId });
      }
    });
  }

  function startScan(incremental = false) {
    if (!hasSource || checking || busy || invalidDates) return;
    void run("tg_scan", async () => {
      const result = await call<{ id: string }>("tg_scan", {
        mode,
        source,
        limit: Number(limit) || 50,
        media_mode: mediaMode,
        ...(incremental ? { incremental: true } : {}),
        ...(dateRange ? { start_date: dates.start, end_date: dates.end } : {}),
      });
      clearScan();
      setIncrementalScan(incremental);
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

  function dismissJobs(removed: RemovedJob[]) {
    setDismissed(
      (previous) =>
        new Set([
          ...previous,
          ...removed.map((entry) => `${entry.library_id}|${entry.media_key}`),
        ]),
    );
  }

  function control(action: JobAction | "delete", ids: string[]) {
    if (!ids.length) return;
    const removesJobs = action === "cancel" || action === "delete";
    void run("tg_control", async () => {
      try {
        for (let start = 0; start < ids.length; start += 500) {
          const result = await call<boolean | RemovedJob[]>("tg_control", {
            action,
            ids: ids.slice(start, start + 500),
            ...(removesJobs ? { report: true } : {}),
          });
          if (Array.isArray(result)) dismissJobs(result);
        }
      } finally {
        if (removesJobs) setTaskSelection(new Set());
        await refreshJobs();
      }
    });
  }

  function controlAll(action: "resume" | "cancel", deleteFiles = false) {
    void run("tg_bulk_control", async () => {
      const result = await call<boolean | RemovedJob[]>("tg_bulk_control", {
        action,
        ...(action === "cancel"
          ? { delete_partial: deleteFiles, report: true }
          : {}),
      });
      if (Array.isArray(result)) {
        dismissJobs(result);
        setTaskSelection(new Set());
      }
      const changed = Array.isArray(result) ? result.length > 0 : result;
      setNotice(
        changed
          ? action === "resume"
            ? "已继续所有已暂停的任务。"
            : "已取消未完成的下载任务。"
          : "当前没有可执行该操作的任务。",
      );
      await refreshJobs();
    });
  }

  const locked = Boolean(busy);
  const scanLocked = locked || checking || backfill?.state === "scanning";
  const canResumeAll = jobs?.some(({ state }) => state === "paused");
  const canCancelAll = jobs?.some(({ state }) =>
    (jobStates[state].controls as readonly JobAction[]).includes("cancel"),
  );

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
          <p>登录 Telegram 后可扫描频道和「我的收藏」。</p>
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
            <Button
              variant={mode === "saved" ? "default" : "outline"}
              type="button"
              aria-pressed={mode === "saved"}
              disabled={scanLocked}
              onClick={() => {
                setMode("saved");
                clearScan();
              }}
            >
              我的收藏
            </Button>
          </div>
          <form
            className="telegram-panel"
            onSubmit={(event) => {
              event.preventDefault();
              startScan();
            }}
          >
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
            ) : mode === "channel" ? (
              <>
                <div className="telegram-actions telegram-fields">
                  <Button
                    type="button"
                    variant="outline"
                    disabled={scanLocked || loadingChannels}
                    onClick={() => {
                      channelPrivacy.reveal();
                      if (channelsError || !channels || !channelPrivacy.hidden)
                        void loadChannels();
                    }}
                  >
                    {loadingChannels
                      ? "正在载入频道…"
                      : channelsError
                        ? "重试载入频道"
                        : channelPrivacy.hidden && channels
                          ? "显示频道"
                          : channels
                            ? "刷新频道"
                            : "载入频道"}
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
                {channelsError && (
                  <p className="telegram-error" role="alert">
                    {channelsError}
                  </p>
                )}
              </>
            ) : null}
            <div className="telegram-actions telegram-fields">
              <label>
                媒体类型
                <select
                  value={mediaMode}
                  disabled={scanLocked}
                  onChange={(event) => {
                    setMediaMode(event.target.value as typeof mediaMode);
                    if (!incrementalScan) clearScan();
                  }}
                >
                  <option value="all">视频和图片</option>
                  <option value="video">仅视频</option>
                  <option value="image">仅图片</option>
                </select>
              </label>
              {mode !== "link" && (
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
              {mode !== "link" && (
                <Button type="button" variant="outline"
                  disabled={!hasSource || !target?.online || scanLocked || invalidDates}
                  onClick={() => startBackfill()}>
                  {dateRange ? "补齐日期范围内的媒体" : "补齐全部历史媒体"}
                </Button>
              )}
              {(backfill?.state === "interrupted" || backfill?.state === "failed") && (
                <Button type="button" variant="outline" disabled={scanLocked}
                  onClick={() => startBackfill(true)}>继续上次历史补档</Button>
              )}
              {backfill?.state === "scanning" && backfill.id && (
                <Button type="button" variant="outline" disabled={locked}
                  onClick={() => void run("tg_scan_cancel", async () => {
                    await call("tg_scan_cancel", { id: backfill.id });
                  })}>停止历史补档</Button>
              )}
              {mode !== "link" && !dateRange && (
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
                disabled={!hasSource || scanLocked || invalidDates}
              >
                <Search size={16} />
                扫描媒体
              </Button>
              {mode !== "link" && (
                <Button
                  type="button"
                  variant="outline"
                  disabled={!hasSource || scanLocked || invalidDates}
                  onClick={() => startScan(true)}
                >
                  扫描新增媒体
                </Button>
              )}
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
          {backfill && (
            <p role="status">
              {backfill.state === "scanning" ? "正在补齐历史媒体"
                : backfill.state === "ready" ? "历史扫描完成"
                : backfill.state === "cancelled" ? "历史补档已停止"
                : backfill.error || "历史补档已中断，可从断点继续"}
              {backfill.found !== undefined && ` · 找到 ${backfill.found} 项媒体，${backfill.added ?? 0} 项已提交`}
            </p>
          )}
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
          <Button
            type="button"
            variant="outline"
            disabled={locked || !authorized || !canResumeAll}
            onClick={() => controlAll("resume")}
          >
            <Play size={16} />
            一键继续
          </Button>
          <Button
            type="button"
            variant="outline"
            disabled={locked || !canCancelAll}
            onClick={() => {
              setDeletePartial(false);
              setConfirmCancelAll(true);
            }}
          >
            取消所有任务
          </Button>
          <Button
            type="button"
            variant="outline"
            disabled={locked || !chosenJobs.length}
            onClick={() => setConfirmDelete(true)}
          >
            删除所选任务
          </Button>
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
        <div className="telegram-messages task-list">
          {messages.map(([key, media]) => (
            <TelegramMessageCard
              key={key}
              messageKey={key}
              items={media}
              scanId={scanId}
              indices={indices}
              selected={selected}
              changeSelection={changeSelection}
              taskChecked={taskSelection.has(key)}
              changeTaskChecked={(checked) =>
                setTaskSelection((previous) => {
                  const updated = new Set(previous);
                  if (checked) updated.add(key);
                  else updated.delete(key);
                  return updated;
                })
              }
              locked={locked}
              statusesCurrent={statusesCurrent}
              pending={pending}
              authorized={authorized}
              control={control}
            />
          ))}
        </div>
      </section>
      <Modal
        open={confirmDelete}
        onClose={() => setConfirmDelete(false)}
        title="删除所选任务"
      >
        <p>
          将从下载列表移除所选的 {chosenJobs.length}{" "}
          项任务，并停止其中正在下载的任务。已保存的媒体和断点文件会保留。
        </p>
        <div className="modal-footer">
          <Button
            type="button"
            variant="outline"
            onClick={() => setConfirmDelete(false)}
          >
            返回
          </Button>
          <Button
            type="button"
            disabled={locked || !chosenJobs.length}
            onClick={() => {
              setConfirmDelete(false);
              control("delete", chosenJobs);
            }}
          >
            确认删除
          </Button>
        </div>
      </Modal>
      <Modal
        open={confirmCancelAll}
        onClose={() => setConfirmCancelAll(false)}
        title="取消所有电报下载任务"
      >
        <p>
          将取消所有媒体库中未完成的电报下载，并将这些任务移出列表。已保存的媒体会保留。
        </p>
        <label className="telegram-actions">
          <input
            type="checkbox"
            checked={deletePartial}
            onChange={(event) => setDeletePartial(event.target.checked)}
          />
          同时删除这些任务的本地未完成文件
        </label>
        <div className="modal-footer">
          <Button
            type="button"
            variant="outline"
            onClick={() => setConfirmCancelAll(false)}
          >
            返回
          </Button>
          <Button
            type="button"
            disabled={locked}
            onClick={() => {
              setConfirmCancelAll(false);
              controlAll("cancel", deletePartial);
            }}
          >
            确认取消
          </Button>
        </div>
      </Modal>
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
