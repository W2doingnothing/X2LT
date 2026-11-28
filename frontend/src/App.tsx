import { useMasonry } from "./Masonry";
import { DownloadDialog, type DownloadMode } from "./DownloadDialog";
import { TelegramDownloads } from "./TelegramDownloads";
import { TelegramAccountSettings } from "./TelegramAccountSettings";
import { AutomationSettings } from "./AutomationSettings";
import { MediaStack, useStackPreviewReady } from "./MediaStack";
import { LocalImportDialog, LocalLibrary } from "./LocalLibrary";
import { LibraryCard } from "./LibraryCard";
import { AutoFlattenSettings, LibraryMigration } from "./LibraryStorage";
import { RandomVideoPicker } from "./RandomVideoPicker";
import {
  ContentMenu,
  ContentContextMenu,
  RemovalDialog,
  FiltersPanel,
  type ContextTarget,
} from "./Removal";
import { ContentIdentity } from "./ContentIdentity";
import { contentPresentation } from "./contentPresentation";
import { useState, useEffect, useRef, useCallback } from "react";
import {
  ArrowDownToLine,
  LayoutDashboard,
  Layers,
  FolderOpen,
  Settings as SettingsIcon,
  Sun,
  Moon,
  Plus,
  Search,
  ChevronLeft,
  ChevronRight,
  ArrowUpRight,
  Play,
  Pause,
  Check,
  CheckCheck,
  X,
  EyeOff,
  Undo2,
  Image,
  Film,
  RefreshCw,
  HardDrive,
  ShieldCheck,
  Clock,
  Download,
  CheckCircle2,
  Info,
  ExternalLink,
} from "lucide-react";
import type {
  Snapshot,
  Media,
  Tweet,
  Filter,
  Payload,
  Event,
  Operation,
  Author,
  AuthorPreference,
  Library,
  Source,
} from "./types";
import { call, desktop, demoProgress } from "./api";
import { applyTheme } from "./theme";
import { appIconUrl, applyAppIcon, resolveAppIcon } from "./app-icons";
import { reconcileProgress, type Progress } from "./progress";
import {
  PrivacyProvider,
  PrivacyPanel,
  PrivateText,
  usePrivateReveal,
} from "./Privacy";
import { art } from "./art";
import {
  ThemePanel,
  LibraryPanel,
  AccountPanel,
  bytes,
  type Action,
} from "./Preferences";
import { Button } from "./components/ui/button";
import { Modal } from "./components/ui/dialog";
import { ScrollNavigation } from "./ScrollNavigation";
import { Authors } from "./Authors";
import { ResourceBrowser, type ResourceKind } from "./ResourceBrowser";
import { PlaybackProvider, usePlayback } from "./Playback";
import { CloudUpload } from "./CloudUpload";
import { useBackNavigation } from "./BackNavigation";
import { ContinuousFooter } from "./ContinuousPages";
import {
  Collections,
  Tags,
  TagFilters,
  Avatar,
  OperationStatus,
  LibraryCover,
  enqueuePreview,
} from "./Collections";
type Page =
  "overview" | "downloads" | "telegram" | "archive" | "organize" | "settings";
const taskPageSize = 50;
const emptyTasks: Tweet[] = [];
type TaskFeed = {
  scope: string;
  tasks: Tweet[];
  firstIds: string;
  next: number;
  total: number;
  loading: boolean;
  error: string;
};
const titles: Record<Page, string> = {
  overview: "总览",
  downloads: "推特下载",
  telegram: "电报下载",
  archive: "媒体库",
  organize: "媒体工具",
  settings: "偏好设置",
};
const labels: Record<string, string> = {
  pending: "等待下载",
  downloading: "下载中",
  paused: "已暂停",
  failed: "失败可重试",
  completed: "已保存",
};
function groups(media: Media[]) {
  const map = new Map<string, Media[]>();
  media.forEach((m) => {
    const key = m.media_type + ":" + m.media_id;
    map.set(key, [...(map.get(key) || []), m]);
  });
  return [...map.values()].sort(
    (a, b) => Number(a[0].media_id) - Number(b[0].media_id),
  );
}
const representative = (rows: Media[]) =>
  rows.find((m) => m.state === "downloading") ||
  rows.find((m) => m.state !== "completed") ||
  rows[0];
const done = (t: Tweet) => t.media.every((m) => m.state === "completed");
const previewCache = new Map<string, string>();
function Thumbnail({
  media,
  tweet,
  index,
  progress,
  onClick,
  detail = false,
  compact = false,
  onSwipe,
}: {
  media: Media;
  tweet: Tweet;
  index: number;
  progress?: Progress;
  onClick: () => void;
  detail?: boolean;
  compact?: boolean;
  onSwipe?: (delta: number) => void;
}) {
  const { hidden, reveal } = usePrivateReveal(String(media.id));
  const stackReady = useStackPreviewReady();
  const [image, setImage] = useState<string | null>(null),
    [visible, setVisible] = useState(false);
  const [dimensions, setDimensions] = useState<[number, number] | null>(null);
  useEffect(() => setDimensions(null), [media.id]);
  const [previewWidth, previewHeight] = dimensions || [
    media.width,
    media.height,
  ];
  const element = useRef<HTMLButtonElement>(null);
  const startX = useRef(0),
    swiped = useRef(false);
  useEffect(() => {
    if (!desktop || !element.current) return;
    if (typeof IntersectionObserver === "undefined") {
      setVisible(true);
      return;
    }
    const observer = new IntersectionObserver(
      (entries) => setVisible(entries[0].isIntersecting),
      { rootMargin: "100px" },
    );
    observer.observe(element.current);
    return () => observer.disconnect();
  }, [media.id]);
  useEffect(() => {
    let mounted = true;
    const key = media.id + ":" + media.state;
    if (!desktop || !visible) return;
    const cached = previewCache.get(key);
    if (cached) {
      setImage(cached);
      return;
    }
    void enqueuePreview(async () => {
      if (!mounted) return;
      const image = await call<string | null>("preview", { job_id: media.id });
      if (image) {
        previewCache.set(key, image);
        if (previewCache.size > 200)
          previewCache.delete(previewCache.keys().next().value!);
      }
      if (mounted) {
        setImage(image);
        if (!image) stackReady?.();
      }
    }).catch(() => {
      if (mounted) stackReady?.();
    });
    return () => {
      mounted = false;
    };
  }, [media.id, media.state, stackReady, visible]);
  const percent =
    media.state === "completed"
      ? 100
      : progress?.total
        ? Math.min(100, (100 * progress.done) / progress.total)
        : desktop
          ? 0
          : demoProgress(media);
  const state =
    media.state === "downloading" && progress && !progress.total
      ? "下载中"
      : labels[media.state];
  const determinate = !!progress?.total || (!desktop && percent > 0);
  const mediaAction = hidden ? "显示" :
    desktop && media.media_type === "video" && media.state === "completed" ? "播放" : "预览";
  return (
    <button
      ref={element}
      style={
        !detail && !stackReady
          ? {
              aspectRatio:
                previewWidth > 0 && previewHeight > 0
                  ? `${previewWidth} / ${previewHeight}`
                  : "16 / 9",
              width: `min(100%, ${(compact ? 280 : 420) * (previewWidth > 0 && previewHeight > 0 ? previewWidth / previewHeight : 16 / 9)}px)`,
            }
          : undefined
      }
      className={
        "media-tile " +
        (media.state !== "completed" && !detail ? "unfinished " : "") +
        (detail ? "detail-tile" : "")
      }
      onPointerDown={(e) => {
        startX.current = e.clientX;
        swiped.current = false;
      }}
      onPointerUp={(e) => {
        const delta = e.clientX - startX.current;
        if (Math.abs(delta) > 30 && onSwipe) {
          swiped.current = true;
          onSwipe(delta > 0 ? -1 : 1);
        }
      }}
      onClick={() => {
        if (hidden) {
          reveal();
          return;
        }
        if (!swiped.current) onClick();
      }}
      aria-label={`${mediaAction}第 ${index + 1} 个媒体`}
    >
      {image || !desktop ? (
        <img
          draggable={false}
          onLoad={(e) => {
            stackReady?.(e.currentTarget);
            if (
              !stackReady &&
              desktop &&
              image &&
              e.currentTarget.naturalWidth &&
              e.currentTarget.naturalHeight
            )
              setDimensions([
                e.currentTarget.naturalWidth,
                e.currentTarget.naturalHeight,
              ]);
          }}
          onError={stackReady ? () => {
            setImage(null);
            stackReady();
          } : undefined}
          src={image || art(tweet.demoColor || "gold", index)}
          alt={hidden ? "已模糊的媒体" : `${tweet.author}的媒体 ${index + 1}`}
          className={hidden ? "privacy-blurred" : ""}
        />
      ) : (
        <div className="no-preview">
          <Image size={26} />
          <span>暂无预览</span>
        </div>
      )}
      {hidden && (
        <>
          <span className="privacy-shield" />
          <span className="privacy-hint">点击显示</span>
        </>
      )}
      <span className="media-type">
        {media.media_type === "video" ? (
          <Film size={12} />
        ) : (
          <Image size={12} />
        )}{" "}
        {media.extension.toUpperCase()}
      </span>
      {media.state === "completed" ? (
        <span className="media-done">
          <Check size={12} /> 已保存
        </span>
      ) : (
        !hidden &&
        !detail && (
          <div className="media-cover">
            <div
              className="media-progress-ring"
              role="progressbar"
              aria-label={state}
              aria-valuemin={0}
              aria-valuemax={100}
              aria-valuenow={determinate ? Math.floor(percent) : undefined}
            >
              <svg viewBox="0 0 64 64">
                <circle cx="32" cy="32" r="27" />
                <circle
                  className={
                    media.state === "downloading" && !determinate
                      ? "indeterminate"
                      : ""
                  }
                  cx="32"
                  cy="32"
                  r="27"
                  strokeDasharray={`${determinate ? percent * 1.696 : media.state === "downloading" ? 55 : 0} 170`}
                />
              </svg>
              <span>
                {determinate ? (
                  `${Math.floor(percent)}%`
                ) : media.state === "downloading" ? (
                  <Download size={18} />
                ) : (
                  <Clock size={18} />
                )}
              </span>
            </div>
            <small>{state}</small>
          </div>
        )
      )}
      <span className="media-size">
        {media.bytes ? bytes(media.bytes) : ""}
      </span>
    </button>
  );
}
export default function App() {
  return <PlaybackProvider><Workspace /></PlaybackProvider>;
}

function Workspace() {
  const play = usePlayback();
  const [data, setData] = useState<Snapshot | null>(null),
    [page, setPage] = useState<Page>("overview"),
    [filter, setFilter] = useState<Filter>("all"),
    [query, setQuery] = useState(""),
    [offset, setOffset] = useState(0),
    [selected, setSelected] = useState<string[]>([]),
    [offsets, setOffsets] = useState<Record<string, number>>({}),
    [progress, setProgress] = useState<Record<number, Progress>>({}),
    [detail, setDetail] = useState<Tweet | null>(null),
    [detailIndex, setDetailIndex] = useState(0),
    [adding, setAdding] = useState<DownloadMode>(null),
    [library, setLibrary] = useState(""),
    [settingsTab, setSettingsTab] = useState("appearance"),
    [toast, setToast] = useState<{ text: string; undo?: () => void } | null>(
      null,
    ),
    [loadError, setLoadError] = useState(""),
    [busy, setBusy] = useState(false),
    [transition, setTransition] = useState(false);
  const progressRun = useRef<string | null>(null);
  const dataRef = useRef(data);
  dataRef.current = data;
  const [scope, setScope] = useState<string | null>(null);
  const [localImport, setLocalImport] = useState<Library | null>(null);
  const [localScan, setLocalScan] = useState<Library | null>(null);
  const [randomVideoPicker, setRandomVideoPicker] = useState(false);
  const [telegramAccountReset, setTelegramAccountReset] = useState(0);
  const [lastRandomVideoLibraries, setLastRandomVideoLibraries] = useState<string[]>();
  const [localRevision, setLocalRevision] = useState(0);
  const [localCount, setLocalCount] = useState<{ key: string; total: number } | null>(null);
  const [mixedPage, setMixedPage] = useState<{ key: string; tasks: Tweet[] }>();
  const [removeIds, setRemoveIds] = useState<string[] | null>(null);
  const [contextTarget, setContextTarget] = useState<ContextTarget | null>(
    null,
  );
  type CollectionRoute = {
    mode: "tweets" | "authors" | "sources" | "local";
    author: Author | null;
    source?: Source | null;
    group?: "active" | "archive";
  };
  const [collectionRoute, setCollectionRoute] = useState<CollectionRoute>({
    mode: "tweets",
    author: null,
  });
  const libraryRoutes = useRef<Record<string, CollectionRoute>>({});
  const [resourceKind, setResourceKind] = useState<ResourceKind | null>(null);
  const overviewRootScroll = useRef(0);
  const resourceScroll = useRef<Partial<Record<ResourceKind, number>>>({});
  function chooseResourceKind(kind: ResourceKind | null) {
    if (resourceKind === null) overviewRootScroll.current = window.scrollY;
    else resourceScroll.current[resourceKind] = window.scrollY;
    setResourceKind(kind);
    const y =
      kind === null
        ? overviewRootScroll.current
        : resourceScroll.current[kind] || 0;
    requestAnimationFrame(() =>
      requestAnimationFrame(() => window.scrollTo(0, y)),
    );
  }
  const [toolOpen, setToolOpen] = useState(false);
  const [authorBusy, setAuthorBusy] = useState(false);
  const isCollection = (page === "overview" || page === "archive") && !!scope;
  const authorView =
    isCollection &&
    collectionRoute.mode === "authors" &&
    !collectionRoute.author;
  const sourceView =
    isCollection &&
    collectionRoute.mode === "sources" &&
    !collectionRoute.source;
  const localView = isCollection && collectionRoute.mode === "local";
  const mixedCollection = desktop && !!data?.library_cards
    && isCollection && collectionRoute.mode === "tweets";
  const [tagFilters, setTagFilters] = useState<string[]>([]);
  const [untagged, setUntagged] = useState(false);
  const localQueryKey = JSON.stringify([scope, query, tagFilters, untagged]);
  const mixedPageKey = JSON.stringify([localQueryKey, offset]);
  const mixedTasks = mixedPage?.key === mixedPageKey ? mixedPage.tasks : emptyTasks;
  const infiniteScroll = data?.settings.infinite_scroll === true;
  const infiniteTasks = infiniteScroll && isCollection && !authorView && !sourceView && !localView && !mixedCollection;
  const feedScope = JSON.stringify([
    page, scope, filter, query, tagFilters, untagged,
    collectionRoute.mode, collectionRoute.group, collectionRoute.author?.key, collectionRoute.source?.key,
  ]);
  const [feed, setFeed] = useState<TaskFeed | null>(null);
  const activeFeed = infiniteTasks && feed?.scope === feedScope ? feed : null;
  const taskRows = infiniteTasks ? activeFeed?.tasks || emptyTasks : data?.tasks || emptyTasks;
  const feedRef = useRef<TaskFeed | null>(null);
  const appendRequest = useRef(0);
  const liveFeedScope = useRef("");
  liveFeedScope.current = infiniteTasks ? feedScope : "";
  const updateFeed = useCallback((value: TaskFeed | null) => {
    feedRef.current = value;
    setFeed(value);
  }, []);
  const contentList = useRef<HTMLDivElement>(null);
  useMasonry(
    contentList,
    isCollection && !authorView && !sourceView && !localView && !mixedCollection,
    taskRows.map((t) => t.id).join(",") + ":" + page + ":" + scope,
  );
  const routeKey = (p: Page, s: string | null, route: CollectionRoute) =>
    p +
    ":" +
    (s || "") +
    ((p === "overview" || p === "archive") && s
      ? ":" +
        route.mode +
        ":" +
        (route.group || "active") +
        ":" +
        (route.author?.key || route.source?.key || "")
      : "");
  const [operation, setOperation] = useState<Operation | null>(null);
  const [organizeProgress, setOrganizeProgress] = useState<{
    done: number;
    total: number;
    text: string;
  } | null>(null);
  type View = {
    filter: Filter;
    query: string;
    offset: number;
    tags: string[];
    untagged: boolean;
    selected: string[];
    offsets: Record<string, number>;
    detail: Tweet | null;
    detailIndex: number;
    scroll: number;
    library: string;
  };
  const views = useRef<Record<string, View>>({});
  const sectionScope = useRef<Partial<Record<Page, string | null>>>({});
  const pendingScroll = useRef<number | null>(null);
  const [downloadsOpen, setDownloadsOpen] = useState(false);
  const [librariesOpen, setLibrariesOpen] = useState(false);
  const started = data !== null;
  const summaryOnly = page !== "downloads" && (!isCollection || localView || mixedCollection);
  const request = useRef(0);
  const detailId = useRef<string | null>(null);
  detailId.current = detail?.id || null;
  const notify = useCallback(
    (text: string, undo?: () => void) => setToast({ text, undo }),
    [],
  );
  const fetchSnapshot = useCallback((nextOffset: number) =>
    call<Snapshot>("snapshot", {
      filter: page === "archive" ? "completed" : filter,
      query,
      offset: nextOffset,
      library_id: scope || undefined,
      tags: tagFilters,
      untagged,
      collection: isCollection,
      view: authorView ? "authors" : sourceView ? "sources" : "tweets",
      source_key: isCollection ? collectionRoute.source?.key : undefined,
      author_key: isCollection ? collectionRoute.author?.key : undefined,
      author_group: collectionRoute.group || "active",
      summary_only: summaryOnly || undefined,
    }), [
      page, filter, query, scope, tagFilters, untagged, isCollection, authorView,
      sourceView, collectionRoute, summaryOnly,
    ]);
  const refresh = useCallback(async () => {
    const revision = ++request.current;
    try {
      if (desktop && !dataRef.current) {
        try {
          const shell = await call<Snapshot>("bootstrap");
          if (revision !== request.current) return;
          if (shell?.settings && Array.isArray(shell.libraries)) {
            applyTheme(shell.settings.accent, shell.settings.theme === "dark");
            setData(shell);
            dataRef.current = shell;
            setLibrary(shell.default_library || "");
            setOperation(shell.operation || null);
            setBusy(shell.busy);
            setLoadError("");
          }
        } catch {
          // Older hosts and fixtures may only provide snapshot.
        }
        if (revision !== request.current) return;
      }
      const requestedOffset = infiniteTasks || summaryOnly || mixedCollection ? 0 : offset;
      const result = await fetchSnapshot(requestedOffset);
      if (revision !== request.current) return;
      applyTheme(result.settings.accent, result.settings.theme === "dark");
      setData(result);
      let available = result.tasks;
      if (infiniteTasks && liveFeedScope.current === feedScope) {
        const old = feedRef.current;
        const firstIds = result.tasks.map((task) => task.id).join(",");
        if (old?.scope === feedScope && old.total === result.total && old.firstIds === firstIds) {
          available = [...result.tasks, ...old.tasks.slice(result.tasks.length)];
          updateFeed({ ...old, tasks: available });
        } else {
          appendRequest.current++;
          updateFeed({
            scope: feedScope, tasks: available, firstIds,
            next: result.tasks.length || result.total, total: result.total, loading: false, error: "",
          });
        }
      }
      if (!mixedCollection) {
        setSelected((prev) =>
          prev.filter((id) => available.some((t) => t.id === id)),
        );
      }
      if (!mixedCollection && !infiniteTasks && !summaryOnly && result.offset !== offset) {
        pendingScroll.current = 0;
        setSelected([]);
        setOffset(result.offset);
      }
      const snapshotRun = result.operation?.run_id || null;
      if (progressRun.current !== snapshotRun) {
        setProgress({});
        progressRun.current = snapshotRun;
      }
      setOperation(result.operation || null);
      setBusy(result.busy);
      setLoadError("");
      if (pendingScroll.current !== null) {
        const y = pendingScroll.current;
        pendingScroll.current = null;
        requestAnimationFrame(() =>
          requestAnimationFrame(() => {
            if (revision === request.current) window.scrollTo(0, y);
          }),
        );
      }
      setLibrary((prev) =>
        result.libraries.some((l) => l.id === prev)
          ? prev
          : result.default_library || "",
      );
      if (!mixedCollection && detailId.current) {
        const updated = available.find((t) => t.id === detailId.current);
        if (updated) setDetail(updated);
      }
    } catch (e) {
      if (revision === request.current) setLoadError((e as Error).message);
    }
  }, [offset, fetchSnapshot, infiniteTasks, feedScope, summaryOnly, updateFeed, mixedCollection]);
  const latestRefresh = useRef(refresh);
  latestRefresh.current = refresh;
  useEffect(() => {
    void refresh();
  }, [refresh]);
  useEffect(() => {
    if (!infiniteTasks || feedRef.current?.scope !== feedScope) {
      appendRequest.current++;
      if (feedRef.current) updateFeed(null);
    }
  }, [infiniteTasks, feedScope, updateFeed]);
  const appendTasks = useCallback(async () => {
    const current = feedRef.current;
    if (!infiniteTasks || !current || current.scope !== feedScope ||
        current.loading || current.next >= current.total) return;
    const next = current.next;
    const revision = ++appendRequest.current;
    updateFeed({ ...current, loading: true, error: "" });
    try {
      const result = await fetchSnapshot(next);
      const latest = feedRef.current;
      if (revision !== appendRequest.current || liveFeedScope.current !== feedScope || latest?.scope !== feedScope ||
          !latest.loading || latest.next !== next) return;
      const seen = new Set(latest.tasks.map((task) => task.id));
      const additions = [];
      for (const task of result.tasks) {
        if (seen.has(task.id)) continue;
        seen.add(task.id);
        additions.push(task);
      }
      updateFeed({
        ...latest, tasks: [...latest.tasks, ...additions],
        next: result.offset === next && result.tasks.length
          ? result.offset + result.tasks.length : result.total,
        total: result.total, loading: false,
      });
    } catch (error) {
      const latest = feedRef.current;
      if (revision === appendRequest.current && liveFeedScope.current === feedScope && latest?.scope === feedScope &&
          latest.loading && latest.next === next)
        updateFeed({ ...latest, loading: false, error: (error as Error).message });
    }
  }, [infiniteTasks, feedScope, fetchSnapshot, updateFeed]);
  useEffect(() => {
    if (!started) return;
    let alive = true;
    let timer: ReturnType<typeof setTimeout>;
    async function poll() {
      try {
        const result = await call<{
          events: Event[];
          busy: boolean;
          operation?: Operation | null;
        }>("poll");
        if (!alive) return;
        setBusy(result.busy);
        if (result.operation !== undefined) setOperation(result.operation);
        const runId = result.operation?.run_id || null;
        const priorRun = progressRun.current;
        const events = result.events.filter(
          (e) => !e.run_id || e.run_id === runId,
        );
        if (runId !== priorRun || events.some((e) => e.type === "progress")) {
          setProgress((prev) =>
            reconcileProgress(prev, result.events, runId, priorRun, Date.now()),
          );
        }
        progressRun.current = runId;
        events
          .filter(
            (e) =>
              e.type === "message" &&
              e.text &&
              (e.scope === "organize"
                ? page === "organize"
                : page === "downloads"),
          )
          .forEach((e) => notify(e.text!));
        for (const event of events)
          if (event.scope === "organize" && event.type === "operation_progress")
            setOrganizeProgress({
              done: event.done || 0,
              total: event.total || 0,
              text: event.text || "整理",
            });
        if (
          events.some((e) => ["changed", "finished", "resync"].includes(e.type))
        ) {
          if (mixedCollection) setLocalRevision((value) => value + 1);
          void refresh();
        }
      } catch (e) {
        if (alive) setLoadError((e as Error).message);
      } finally {
        if (alive) timer = setTimeout(poll, 600);
      }
    }
    void poll();
    return () => {
      alive = false;
      clearTimeout(timer);
    };
  }, [refresh, notify, started, page, mixedCollection]);
  useEffect(() => {
    if (data) applyTheme(data.settings.accent, data.settings.theme === "dark");
  }, [data?.settings.accent, data?.settings.theme]);
  const appIcon = resolveAppIcon(
    data?.settings.app_icon ?? new URLSearchParams(location.search).get("app-icon"),
  );
  useEffect(() => applyAppIcon(appIcon), [appIcon]);
  useEffect(() => {
    if (!toast) return;
    const timer = setTimeout(() => setToast(null), 5500);
    return () => clearTimeout(timer);
  }, [toast]);
  const action: Action = async (name, payload = {}, message) => {
    try {
      setTransition(true);
      const oldTags =
        name === "tags_set"
          ? (mixedCollection ? mixedTasks : dataRef.current?.tasks)
            ?.find((t) => t.id === payload.id)?.tags || []
          : [];
      const result = await call(name, payload);
      if (name === "start") {
        setProgress({});
        progressRun.current = null;
      }
      if (name === "tags_set")
        setDetail((prev) =>
          prev && prev.id === payload.id
            ? { ...prev, tags: payload.tags as string[] }
            : prev,
        );
      if (name === "library_default") setLibrary(String(payload.id));
      if (name === "tag_rename")
        setTagFilters((prev) =>
          prev.map((t) => (t === payload.old ? String(payload.new) : t)),
        );
      if (name === "tag_delete")
        setTagFilters((prev) => prev.filter((t) => t !== payload.name));
      if (mixedCollection || name === "tag_rename" || name === "tag_delete")
        setLocalRevision((value) => value + 1);
      if (message) notify(message);
      await latestRefresh.current();
      if (
        name === "tags_set" &&
        oldTags.length > (payload.tags as string[]).length
      )
        notify("标签已移除", () => {
          void action(
            "tags_set",
            { id: payload.id, tags: oldTags },
            "已恢复标签",
          );
        });
      return true;
    } catch (e) {
      notify((e as Error).message);
      return false;
    } finally {
      setTransition(false);
    }
  };
  const save = async (p: Payload) => action("settings", p, "设置已保存");
  function rememberView() {
    views.current[routeKey(page, scope, collectionRoute)] = {
      filter,
      query,
      offset,
      tags: tagFilters,
      untagged,
      selected: [...selected],
      offsets: { ...offsets },
      detail,
      detailIndex,
      scroll: window.scrollY,
      library,
    };
    sectionScope.current[page] = scope;
    if (isCollection && scope)
      libraryRoutes.current[page + ":" + scope] = collectionRoute;
  }
  function restoreView(
    p: Page,
    nextScope: string | null,
    targetRoute?: CollectionRoute,
  ) {
    request.current++;
    appendRequest.current++;
    if (feedRef.current?.loading) updateFeed({ ...feedRef.current, loading: false });
    const route = targetRoute ||
      (nextScope ? libraryRoutes.current[p + ":" + nextScope] : null) || {
        mode: "tweets",
        author: null,
      };
    const view = views.current[routeKey(p, nextScope, route)];
    setCollectionRoute(route);
    setPage(p);
    setScope(nextScope);
    sectionScope.current[p] = nextScope;
    setFilter(view?.filter || (nextScope ? "completed" : "all"));
    setQuery(view?.query || "");
    setOffset(view?.offset || 0);
    setTagFilters(view?.tags || []);
    setUntagged(view?.untagged || false);
    setSelected(view?.selected || []);
    setOffsets(view?.offsets || {});
    setDetail(view?.detail || null);
    setDetailIndex(view?.detailIndex || 0);
    setAdding(null);
    if (nextScope) setLibrary(nextScope);
    else if (view?.library) setLibrary(view.library);
    pendingScroll.current = view?.scroll || 0;
  }
  function enterLibrary(id: string, parent: "archive" | "overview" = page === "archive" ? "archive" : "overview") {
    rememberView();
    restoreView(parent, id);
  }
  function changeCollection(
    mode: CollectionRoute["mode"],
    author: Author | null = null,
    group: "active" | "archive" = mode === "authors"
      ? collectionRoute.group || "active"
      : "active",
    source: Source | null = null,
  ) {
    rememberView();
    restoreView(page, scope, { mode, author, group, source });
    const enteringOther =
      group === "archive" && collectionRoute.group !== "archive" && !author;
    if (author || source || enteringOther) {
      const previous =
        views.current[routeKey(page, scope, { mode, author, group, source })];
      setTagFilters([...tagFilters]);
      setUntagged(untagged);
      if (enteringOther) {
        setQuery(query);
        setOffset(0);
        pendingScroll.current = 0;
      }
      if (
        !previous ||
        previous.untagged !== untagged ||
        JSON.stringify([...previous.tags].sort()) !==
          JSON.stringify([...tagFilters].sort())
      ) {
        setOffset(0);
        pendingScroll.current = 0;
      }
    }
  }
  async function authorPreferences(
    keys: string[],
    changes: { starred?: boolean; archived?: boolean },
  ) {
    if (!scope || authorBusy) return false;
    const libraryId = scope;
    setAuthorBusy(true);
    try {
      const before = await call<AuthorPreference[]>("author_preferences", {
        library_id: libraryId,
        keys,
        ...changes,
      });
      await latestRefresh.current();
      notify("作者设置已更新", () => {
        void (async () => {
          try {
            await call("author_preferences", {
              library_id: libraryId,
              keys,
              restore: before,
            });
            await latestRefresh.current();
            notify("已撤销");
          } catch (e) {
            notify((e as Error).message);
          }
        })();
      });
      return true;
    } catch (e) {
      notify((e as Error).message);
      return false;
    } finally {
      setAuthorBusy(false);
    }
  }
  function navigate(p: Page, root = false) {
    rememberView();
    const remembered = sectionScope.current[p] || null;
    const target =
      root || ((p === "overview" || p === "archive") && p === page && scope)
        ? null
        : remembered;
    restoreView(
      p,
      target && dataRef.current?.libraries.some((l) => l.id === target)
        ? target
        : null,
    );
    if (p === "overview" && root) {
      setResourceKind(null);
      pendingScroll.current = overviewRootScroll.current;
    }
    if (p === "downloads" || p === "telegram") setDownloadsOpen(true);
  }
  async function ignore(ids: string[], value = true) {
    if (await action("ignore", { ids, ignored: value })) {
      setSelected([]);
      notify(
        value
          ? "已忽略；已开始的媒体允许完成，其余跳过"
          : "已取消忽略，请手动继续下载",
        () => {
          void action("ignore", { ids, ignored: !value }, "已撤销忽略设置");
        },
      );
    }
  }
  function paginate(next: number) {
    pendingScroll.current = 0;
    setSelected([]);
    setOffset(next);
  }
  function openDetail(t: Tweet, index = 0) {
    setDetail(t);
    setDetailIndex(index);
  }
  function openCardMedia(t: Tweet, media: Media) {
    if (desktop && media.media_type === "video" && media.state === "completed") {
      play({ source: "x", id: media.id });
      return;
    }
    openDetail(t, t.media.findIndex((row) => row.id === media.id));
  }
  async function start(mode: string, extra: Payload = {}) {
    await action(
      "start",
      { mode, library_id: library, ...extra },
      mode === "verify" ? "正在检查本地文件" : "任务已开始",
    );
  }
  useBackNavigation(() => {
    if (scope && (page === "overview" || page === "archive")) {
      if (localView) changeCollection("tweets");
      else if (collectionRoute.source) changeCollection("sources");
      else if (collectionRoute.author)
        changeCollection("authors", null, collectionRoute.group);
      else if (collectionRoute.group === "archive")
        changeCollection("authors", null, "active");
      else {
        rememberView();
        restoreView(page, null);
      }
    } else if (page === "overview" && resourceKind) chooseResourceKind(null);
    else if (page === "organize" && toolOpen) setToolOpen(false);
    else if (page !== "overview") navigate("overview", true);
  });
  if (!data)
    return (
      <div className="boot-screen">
        <img src={appIconUrl(appIcon)} width={56} height={56} alt="" />
        <h2>StashRat</h2>
        <p>{loadError || "正在连接本地服务…"}</p>
        {loadError && <Button onClick={refresh}>重试</Button>}
      </div>
    );
  const currentLibrary =
    data.libraries.find((l) => l.id === (scope || library)) ||
    data.libraries.find((l) => l.default);
  const selectableTasks = mixedCollection ? mixedTasks.slice(0, taskPageSize)
    : infiniteTasks ? taskRows.slice(0, taskPageSize) : taskRows;
  const showLocalContent = desktop && isCollection
    && !authorView && !sourceView && !collectionRoute.author && !collectionRoute.source;
  const visibleLocalCount = showLocalContent && localCount?.key === localQueryKey ? localCount.total : 0;
  const collectionTotal = mixedCollection ? visibleLocalCount : (localView ? 0 : data.total) + visibleLocalCount;
  function mixedTasksChanged(tasks: Tweet[]) {
    setMixedPage({ key: mixedPageKey, tasks });
    const available = new Set(tasks.map((task) => task.id));
    setSelected((previous) => {
      const next = previous.filter((id) => available.has(id));
      return next.length === previous.length ? previous : next;
    });
    if (detailId.current) {
      const updated = tasks.find((task) => task.id === detailId.current);
      if (updated) setDetail(updated);
    }
  }
  function localChanged() {
    setLocalRevision((value) => value + 1);
    void latestRefresh.current();
  }
  const selectedMedia = detail?.media[detailIndex];
  const speed = Object.values(progress).reduce(
    (n, p) => n + (Date.now() - p.received < 2000 ? p.speed : 0),
    0,
  );
  const pickLibrary = (
    <select
      aria-label="目标媒体库"
      className="library-select"
      value={library}
      onChange={(e) => setLibrary(e.target.value)}
    >
      {!data.libraries.length && <option value="">请先添加媒体库</option>}
      {data.libraries.map((l) => (
        <option value={l.id} key={l.id}>
          {l.emoji || "📁"} {l.name}
          {l.online ? "" : "（离线）"}
        </option>
      ))}
    </select>
  );
  function card(t: Tweet) {
    const media = groups(t.media);
    const shift = (delta: number) =>
      setOffsets((prev) => ({
        ...prev,
        [t.id]: Math.max(
          0,
          Math.min(media.length - 3, (prev[t.id] || 0) + delta),
        ),
      }));
    const off = Math.min(offsets[t.id] || 0, Math.max(0, media.length - 3));
    return (
      <LibraryCard
        source="x"
        key={t.id}
        className={"tweet-card " + (selected.includes(t.id) ? "selected" : "")}
        onContextMenu={(e) => {
          if ((e.target as HTMLElement).closest("input,textarea,a")) return;
          e.preventDefault();
          setContextTarget({
            ids: selected.includes(t.id) ? selected : [t.id],
            x: e.clientX,
            y: e.clientY,
          });
        }}
      >
        <Tags
          tweet={t}
          options={data!.tag_options || []}
          action={action}
          onFilter={(tag) => {
            setTagFilters((prev) =>
              prev.includes(tag) ? prev : [...prev, tag],
            );
            setOffset(0);
          }}
        />
        <div className="tweet-head library-media-card-header">
          {(page === "downloads" || isCollection) && (
            <button
              className={
                "checkbox " + (selected.includes(t.id) ? "checked" : "")
              }
              aria-label={`选择内容 ${t.id}`}
              aria-pressed={selected.includes(t.id)}
              onClick={() =>
                selected.includes(t.id)
                  ? setSelected((prev) => prev.filter((id) => id !== t.id))
                  : (infiniteTasks || mixedCollection) && selected.length >= taskPageSize
                    ? notify("每次最多选择 50 条内容")
                    : setSelected((prev) => [...prev, t.id])
              }
            >
              {selected.includes(t.id) && <Check size={12} />}
            </button>
          )}
          <ContentIdentity content={t} action={action} />
          {page === "downloads" && (
            <span className={"status " + (done(t) ? "success" : "muted")}>
              {t.ignored ? (
                <EyeOff size={12} />
              ) : done(t) ? (
                <CheckCircle2 size={12} />
              ) : (
                <span className="dot" />
              )}
              {t.ignored
                ? "已忽略"
                : done(t)
                  ? "已完成"
                  : t.media.some((m) => m.state === "downloading")
                    ? "下载中"
                    : t.media.some((m) => m.state === "completed")
                      ? "部分完成"
                      : "待完成"}
            </span>
          )}
        </div>
        {isCollection ? (
          <MediaStack
            count={media.length}
            index={offsets[t.id] || 0}
            onChange={(index) =>
              setOffsets((prev) => ({ ...prev, [t.id]: index }))
            }
            render={(index, swipe) => {
              const versions = media[index];
              const m = representative(versions);
              return (
                <div key={m.id} className="tile-wrap">
                  <Thumbnail
                    media={m}
                    tweet={t}
                    index={index}
                    progress={progress[m.id]}
                    onSwipe={swipe}
                    onClick={() => openCardMedia(t, m)}
                  />
                  {versions.length > 1 && (
                    <span className="version-count">
                      {versions.length} 个版本
                    </span>
                  )}
                </div>
              );
            }}
          />
        ) : (
          <>
            <div
              className="media-grid"
              onWheel={(e) => {
                if (e.shiftKey || Math.abs(e.deltaX) > 20)
                  shift((e.deltaX || e.deltaY) > 0 ? 1 : -1);
              }}
            >
              {media.slice(off, off + 3).map((versions, i) => {
                const m = representative(versions);
                return (
                  <div
                    className="tile-wrap"
                    key={m.media_id + ":" + m.media_type}
                  >
                    <Thumbnail
                      media={m}
                      tweet={t}
                      index={off + i}
                      compact
                      progress={progress[m.id]}
                      onSwipe={shift}
                      onClick={() => openCardMedia(t, m)}
                    />
                    {versions.length > 1 && (
                      <span className="version-count">
                        {versions.length} 个版本
                      </span>
                    )}
                  </div>
                );
              })}
            </div>
          </>
        )}
        <footer className="tweet-footer library-media-card-footer">
          <span>
            <Film size={13} />
            {media.filter((g) => g[0].media_type === "video").length} 视频
            <i>·</i>
            <Image size={13} />
            {media.filter((g) => g[0].media_type === "image").length} 图片
            <span className="footer-divider" />
            {media.filter((g) => g.some((m) => m.state === "completed")).length}
            /{media.length} 已保存
          </span>
          <div className="card-actions">
            <ContentMenu onRemove={() => setRemoveIds([t.id])} />
            {!isCollection && media.length > 3 && (
              <div className="pager">
                <small>
                  {off + 1}–{Math.min(off + 3, media.length)} / {media.length}
                </small>
                <button
                  className="icon-btn"
                  aria-label="上一组媒体"
                  disabled={!off}
                  onClick={() => setOffsets({ ...offsets, [t.id]: off - 1 })}
                >
                  <ChevronLeft size={14} />
                </button>
                <button
                  className="icon-btn"
                  aria-label="下一组媒体"
                  disabled={off + 3 >= media.length}
                  onClick={() => setOffsets({ ...offsets, [t.id]: off + 1 })}
                >
                  <ChevronRight size={14} />
                </button>
              </div>
            )}
            {page === "downloads" && (
              <button
                className="text-btn"
                onClick={() => ignore([t.id], !t.ignored)}
              >
                {t.ignored ? <Undo2 size={13} /> : <EyeOff size={13} />}{" "}
                {t.ignored ? "取消忽略" : "忽略"}
              </button>
            )}
            <button className="text-btn" onClick={() => openDetail(t)}>
              详情
              <ArrowUpRight size={13} />
            </button>
          </div>
        </footer>
      </LibraryCard>
    );
  }
  const libraryCards = (
    <Collections
      libraries={data.libraries}
      action={action}
      onEnter={enterLibrary}
      onScan={desktop ? setLocalScan : undefined}
      onAdd={() => {
        navigate("settings");
        setSettingsTab("libraries");
      }}
    />
  );
  return (
    <PrivacyProvider
      enabled={!!data.settings.privacy_mode}
      scopeKey={
        routeKey(page, scope, collectionRoute) +
        ":" +
        (resourceKind || "") +
        ":" +
        toolOpen
      }
    >
      <div className="app">
        <aside className="sidebar">
          <a
            className="brand"
            aria-label="StashRat · 总览"
            href="#"
            onClick={(e) => {
              e.preventDefault();
              navigate("overview", true);
            }}
          >
            <img className="brand-mark" src={appIconUrl(appIcon)} alt="" />
            <strong>StashRat</strong>
          </a>
          <div className="workspace-label">工作空间</div>
          <nav>
            <button className={page === "overview" ? "active" : ""} onClick={() => navigate("overview")}>
              <LayoutDashboard size={18} />总览
            </button>
            <div>
              <div className="nav-library-group">
                <button className={page === "archive" ? "active" : ""} onClick={() => navigate("archive", true)}>
                  <FolderOpen size={18} />媒体库
                </button>
                <button type="button" className="nav-library-toggle"
                  aria-label={librariesOpen ? "收起媒体库" : "展开媒体库"}
                  aria-expanded={librariesOpen}
                  aria-controls="sidebar-library-list"
                  onClick={() => setLibrariesOpen((value) => !value)}>
                  <ChevronRight size={14} style={{ transform: librariesOpen ? "rotate(90deg)" : undefined }} />
                </button>
              </div>
              <div id="sidebar-library-list" className="download-children nav-library-list" hidden={!librariesOpen}>
                {librariesOpen && data.libraries.map((entry) => (
                  <button key={entry.id} type="button" className={page === "archive" && scope === entry.id ? "active" : ""}
                    title={entry.name} aria-label={entry.name} onClick={() => enterLibrary(entry.id, "archive")}>
                    <span>{entry.emoji || "📁"}</span><span>{entry.name}</span>
                  </button>
                ))}
              </div>
            </div>
            <div>
              <button className="nav-download-group" aria-label="下载任务" aria-expanded={downloadsOpen}
                aria-controls="sidebar-download-list" onClick={() => setDownloadsOpen((value) => !value)}>
                <ArrowDownToLine size={18} />下载任务
                <ChevronRight size={14} style={{ transform: downloadsOpen ? "rotate(90deg)" : undefined }} />
              </button>
              <div id="sidebar-download-list" className="download-children" hidden={!downloadsOpen}>
                {downloadsOpen && <>
                  <button className={page === "downloads" ? "active" : ""} onClick={() => navigate("downloads")}>推特下载</button>
                  <button className={page === "telegram" ? "active" : ""} onClick={() => navigate("telegram")}>电报下载</button>
                </>}
              </div>
            </div>
            <button className={page === "organize" ? "active" : ""} onClick={() => navigate("organize")}>
              <Layers size={18} />媒体工具
            </button>
          </nav>
          <div className="sidebar-bottom">
            <button
              className={
                page === "settings" ? "side-settings active" : "side-settings"
              }
              onClick={() => navigate("settings")}
            >
              <SettingsIcon size={18} />
              设置
              <ChevronRight size={14} />
            </button>
            <div className="sidebar-foot">
              <span className="user-avatar">XL</span>
              <div>
                <strong>本地工作空间</strong>
                <small>{desktop ? "Windows 桌面版" : "交互原型"}</small>
              </div>
              <button
                className="icon-btn"
                aria-label={
                  data.settings.theme === "dark" ? "切换浅色" : "切换深色"
                }
                onClick={() =>
                  save({
                    theme: data.settings.theme === "dark" ? "light" : "dark",
                  })
                }
              >
                {data.settings.theme === "dark" ? (
                  <Sun size={17} />
                ) : (
                  <Moon size={17} />
                )}
              </button>
            </div>
          </div>
        </aside>
        <div className="main">
          <header className="topbar">
            <div className="breadcrumb">
              工作空间
              <ChevronRight size={13} />
              <span>{scope ? currentLibrary?.name : titles[page]}</span>
            </div>
            <div className="top-right">
              <span className="prototype-pill">
                <span className="dot" />
                {desktop ? "本地连接 · " + data.version : "交互原型 · 模拟数据"}
              </span>
              {!desktop && (
                <button
                  className="icon-btn"
                  aria-label="重置演示"
                  onClick={() => action("demo_reset", {}, "演示设置已重置")}
                >
                  <RefreshCw size={16} />
                </button>
              )}
            </div>
          </header>
          <main>
            {loadError && (
              <div className="pagination" role="alert">
                <span>{loadError}</span>
                <Button size="sm" variant="outline" onClick={refresh}>重试页面</Button>
              </div>
            )}
            {page === "downloads" && (
              <OperationStatus
                operation={operation}
                onPause={() => void action("pause")}
                onOpen={() => navigate("downloads")}
              />
            )}
            {scope && (
              <>
                <div className="library-breadcrumb">
                  <button onClick={() => navigate(page, true)}>
                    {titles[page]}
                  </button>
                  {collectionRoute.author ||
                  collectionRoute.group === "archive" ? (
                    <>
                      <button
                        aria-label={
                          collectionRoute.group === "archive"
                            ? "返回库作者列表"
                            : "返回作者列表"
                        }
                        onClick={() =>
                          changeCollection("authors", null, "active")
                        }
                      >
                        / {currentLibrary?.name}
                      </button>
                      {collectionRoute.group === "archive" &&
                        (collectionRoute.author ? (
                          <button
                            aria-label="返回作者列表"
                            onClick={() =>
                              changeCollection("authors", null, "archive")
                            }
                          >
                            / 归档作者
                          </button>
                        ) : (
                          <span>/ 归档作者</span>
                        ))}
                      {collectionRoute.author && (
                        <span>
                          /{" "}
                          {collectionRoute.author.display_name ||
                            collectionRoute.author.handle}
                        </span>
                      )}
                    </>
                  ) : (
                    <span>/ {currentLibrary?.name}</span>
                  )}
                </div>
                {collectionRoute.source && (
                  <div className="library-breadcrumb">
                    <button onClick={() => changeCollection("sources")}>
                      返回来源列表
                    </button>
                    <span>/ {collectionRoute.source.label}</span>
                  </div>
                )}
                {currentLibrary && (
                  <div className="library-summary">
                    <LibraryCover library={currentLibrary} />
                    <div>
                      <strong>{currentLibrary.name}</strong>
                      <p>
                        {currentLibrary.message_count ?? 0} 条内容 ·{" "}
                        {currentLibrary.media_count ?? 0} 个媒体 ·{" "}
                        {currentLibrary.capacity_pending
                          ? "正在检查容量"
                          : currentLibrary.available_bytes === undefined
                            ? "容量未知"
                            : "可用 " + bytes(currentLibrary.available_bytes)}
                      </p>
                      {!currentLibrary.online && !currentLibrary.capacity_pending && (
                        <p>媒体库离线 · 可浏览已有记录</p>
                      )}
                    </div>
                  </div>
                )}
              </>
            )}
            <div className="page-heading">
              <div>
                <h1>
                  {scope
                    ? collectionRoute.source?.label ||
                      collectionRoute.author?.display_name ||
                      collectionRoute.author?.handle ||
                      (collectionRoute.group === "archive" ? "归档作者" : "") ||
                      currentLibrary?.name
                    : page === "overview" && resourceKind
                      ? "所有资源"
                      : page === "organize" && toolOpen
                        ? "媒体转移"
                        : titles[page]}
                </h1>
              </div>
              {page === "downloads" && (
                <div className="download-entry-actions">
                  <Button variant="outline" onClick={() => setAdding("link")}>
                    链接下载
                  </Button>
                  <Button onClick={() => setAdding("likes")}>
                    <RefreshCw size={16} />
                    同步点赞
                  </Button>
                </div>
              )}
              {desktop && page === "archive" && !scope && (
                <Button variant="outline" onClick={() => setRandomVideoPicker(true)}>
                  <Play size={16} aria-hidden="true" />
                  随机刷视频
                </Button>
              )}
              {showLocalContent && currentLibrary && (
                <div className="local-heading-actions">
                  <Button variant="outline" disabled={!currentLibrary.online} onClick={() => setLocalScan(currentLibrary)}>
                    扫描此库
                  </Button>
                  <Button onClick={() => setLocalImport(currentLibrary)}>导入本地</Button>
                </div>
              )}
            </div>
            {(loadError || data.credential_error) && (
              <div className="error-banner">
                {loadError || data.credential_error}
              </div>
            )}
            {isCollection && (
              <div className="collection-stats">
                <span>
                  收藏内容 <strong>{currentLibrary?.message_count ?? 0}</strong>
                </span>
                <span>
                  已保存媒体 <strong>{currentLibrary?.media_count ?? 0}</strong>
                </span>
                {!!data.collection_author_count && (
                  <span>
                    作者 <strong>{data.collection_author_count}</strong>
                  </span>
                )}
              </div>
            )}
            {page === "downloads" && (
              <div className="stats">
                <div className="stat">
                  <span className="stat-icon">
                    <ArrowDownToLine size={19} />
                  </span>
                  <div>
                    <span>当前下载</span>
                    <strong>
                      {busy ? (speed / 1024 ** 2).toFixed(1) : "0.0"}
                      <small>MB/s</small>
                    </strong>
                  </div>
                  <div className={"spark " + (busy ? "moving" : "")}>
                    {[8, 12, 9, 17, 13, 23, 16, 27, 21, 30, 25, 19].map(
                      (n, i) => (
                        <i key={i} style={{ height: n }} />
                      ),
                    )}
                  </div>
                </div>
                <div className="stat">
                  <span className="stat-icon">
                    <Layers size={19} />
                  </span>
                  <div>
                    <span>待完成内容</span>
                    <strong>
                      {data.counts.pending}
                      <small>条</small>
                    </strong>
                  </div>
                </div>
                <div className="stat">
                  <span className="stat-icon">
                    <CheckCheck size={19} />
                  </span>
                  <div>
                    <span>已收藏内容</span>
                    <strong>
                      {data.counts.completed}
                      <small>条</small>
                    </strong>
                  </div>
                  <span className="stat-note positive">
                    <ShieldCheck size={13} />
                    本地保存
                  </span>
                </div>
              </div>
            )}
            {(page === "downloads" || isCollection) && (
              <>
                <div className="list-toolbar">
                  <div className="tabs">
                    {isCollection ? (
                      <>
                        <button
                          className={
                            collectionRoute.mode === "tweets" ? "active" : ""
                          }
                          onClick={() =>
                            changeCollection("tweets", null, "active")
                          }
                        >
                          内容
                        </button>
                        {desktop && !data.library_cards && (
                          <button
                            className={localView ? "active" : ""}
                            onClick={() => changeCollection("local")}
                          >
                            本地导入
                          </button>
                        )}
                        {!!data.collection_author_count && (
                          <>
                            <button
                              aria-label="按作者聚类"
                              className={
                                collectionRoute.mode === "authors"
                                  ? "active"
                                  : ""
                              }
                              onClick={() =>
                                changeCollection("authors", null, "active")
                              }
                            >
                              作者
                            </button>
                          </>
                        )}
                        <button
                          aria-label="按来源分类"
                          className={
                            collectionRoute.mode === "sources" ? "active" : ""
                          }
                          onClick={() => changeCollection("sources")}
                        >
                          来源
                        </button>
                      </>
                    ) : (
                      (page === "archive"
                        ? [["completed", "已保存"]]
                        : [
                            ["all", "全部"],
                            ["pending", "未完成"],
                            ["completed", "已完成"],
                            ["ignored", "已忽略"],
                          ]
                      ).map(([key, label]) => (
                        <button
                          className={
                            page === "archive" || filter === key ? "active" : ""
                          }
                          key={key}
                          onClick={() => {
                            setFilter(key as Filter);
                            setOffset(0);
                            setSelected([]);
                          }}
                        >
                          {label}
                          <span>{data.counts[key as Filter]}</span>
                        </button>
                      ))
                    )}
                  </div>
                  <div className="toolbar-right">
                    {authorView && (
                      <Button
                        variant="outline"
                        size="sm"
                        onClick={() =>
                          changeCollection(
                            "authors",
                            null,
                            collectionRoute.group === "archive"
                              ? "active"
                              : "archive",
                          )
                        }
                      >
                        {collectionRoute.group === "archive"
                          ? "返回作者"
                          : "打开归档"}
                      </Button>
                    )}
                    <div className="search">
                      <Search size={15} />
                      <input
                        aria-label={
                          authorView
                            ? "搜索作者"
                            : sourceView
                              ? "搜索来源"
                              : "搜索内容"
                        }
                        placeholder={
                          authorView
                            ? "搜索作者名称、账号…"
                            : sourceView
                              ? "搜索来源…"
                              : "搜索内容、作者、文件名…"
                        }
                        value={query}
                        onChange={(e) => {
                          setQuery(e.target.value);
                          setOffset(0);
                        }}
                      />
                      {query && (
                        <button
                          aria-label="清除搜索"
                          onClick={() => setQuery("")}
                        >
                          <X size={13} />
                        </button>
                      )}
                    </div>
                  </div>
                </div>
                <TagFilters
                  options={data.tag_options || []}
                  selected={tagFilters}
                  untagged={untagged}
                  onChange={(tags) => {
                    setTagFilters(tags);
                    setOffset(0);
                  }}
                  onUntagged={(value) => {
                    setUntagged(value);
                    setOffset(0);
                  }}
                  action={action}
                />
                {(page === "downloads" ||
                  (isCollection && !authorView && !sourceView && !localView && !!selectableTasks.length)) && (
                  <div className="selection-toolbar">
                    <button
                      className={
                        "checkbox " +
                        (selectableTasks.length &&
                        selectableTasks.every((t) => selected.includes(t.id))
                          ? "checked"
                          : "")
                      }
                      aria-label={mixedCollection ? "选择当前列表中的 X 内容（最多 50 条）"
                        : infiniteTasks ? "选择已加载内容（最多 50 条）" : "全选当前页"}
                      onClick={() =>
                        setSelected(
                          selectableTasks.every((t) => selected.includes(t.id))
                            ? []
                            : [
                                ...new Set([
                                  ...selected,
                                  ...selectableTasks.map((t) => t.id),
                                ]),
                              ].slice(0, taskPageSize),
                        )
                      }
                    >
                      {!!selectableTasks.length &&
                        selectableTasks.every((t) => selected.includes(t.id)) && (
                          <Check size={12} />
                        )}
                    </button>
                    <span>
                      {selected.length
                        ? `已选 ${selected.length} 条`
                        : `${collectionTotal} 条内容`}
                    </span>
                    {!!selected.length && (
                      <>
                        {page === "downloads" && (
                          <button
                            className="text-btn"
                            onClick={() =>
                              ignore(selected, filter !== "ignored")
                            }
                          >
                            {filter === "ignored" ? (
                              <Undo2 size={13} />
                            ) : (
                              <EyeOff size={13} />
                            )}
                            批量{filter === "ignored" ? "取消忽略" : "忽略"}
                          </button>
                        )}
                        <button
                          className="text-btn"
                          onClick={() => setRemoveIds(selected)}
                        >
                          批量移除
                        </button>
                        <button
                          className="text-btn"
                          onClick={() => setSelected([])}
                        >
                          取消选择
                        </button>
                      </>
                    )}
                    <span className="selection-spacer" />
                    {page === "downloads" && filter !== "ignored" && (
                      <>
                        <div className="target-picker">
                          <FolderOpen size={13} />
                          {pickLibrary}
                        </div>
                        <Button
                          size="sm"
                          variant="outline"
                          disabled={transition}
                          onClick={() =>
                            busy
                              ? action("pause", {}, "正在暂停并保存进度")
                              : start("continue")
                          }
                        >
                          {busy ? <Pause size={14} /> : <Play size={14} />}{" "}
                          {busy ? "暂停下载" : "继续下载"}
                        </Button>
                      </>
                    )}
                  </div>
                )}
                {isCollection && (
                  <div className="collection-result-count">
                    {collectionTotal}{" "}
                    {sourceView
                      ? "个来源"
                      : authorView
                        ? collectionRoute.group === "archive"
                          ? "位作者"
                          : "个条目"
                        : "条内容"}
                  </div>
                )}
                <div
                  className={
                    isCollection && !authorView && !sourceView
                      ? "content-region"
                      : ""
                  }
                >
                  {showLocalContent && currentLibrary && (
                    <LocalLibrary
                      key={currentLibrary.id}
                      libraryId={currentLibrary.id}
                      libraries={data.libraries}
                      query={query}
                      tags={tagFilters}
                      untagged={untagged}
                      revision={localRevision}
                      changed={localChanged}
                      onTotal={(total) => setLocalCount({ key: localQueryKey, total })}
                      notify={notify}
                      standalone={localView}
                      infiniteScroll={infiniteScroll}
                      mixed={mixedCollection ? {
                        offset, onPage: paginate, onTasks: mixedTasksChanged, renderTask: card,
                      } : undefined}
                    />
                  )}
                  {!localView && !mixedCollection && <div
                    ref={contentList}
                    className={
                      "task-list " +
                      (isCollection && !authorView && !sourceView
                        ? "collection-content-list"
                        : "") + (infiniteTasks ? " continuous-cards" : "")
                    }
                  >
                    {sourceView ? (
                      <div className="source-grid">
                        {data.sources?.map((source) => (
                          <button
                            className="source-card"
                            key={source.key}
                            aria-label={"查看来源 " + source.label}
                            onClick={() =>
                              changeCollection(
                                "sources",
                                null,
                                "active",
                                source,
                              )
                            }
                          >
                            <strong>{source.label}</strong>
                            <small>
                              {source.message_count} 条内容 ·{" "}
                              {source.media_count} 个媒体
                            </small>
                          </button>
                        ))}
                      </div>
                    ) : authorView ? (
                      <Authors
                        authors={data.authors || []}
                        onOpen={(author) => changeCollection("authors", author)}
                        archived={collectionRoute.group === "archive"}
                        onArchive={(author) =>
                          void authorPreferences([author.key], {
                            archived: collectionRoute.group !== "archive",
                          })
                        }
                        onProfile={(handle) =>
                          void action("open_profile", { handle })
                        }
                        busy={authorBusy}
                        onStar={(author) =>
                          void authorPreferences([author.key], {
                            starred: !author.starred,
                          })
                        }
                      />
                    ) : (
                      taskRows.map(card)
                    )}
                    {!(sourceView
                      ? data.sources?.length
                      : authorView
                        ? data.authors?.length
                        : taskRows.length) && (!infiniteTasks || activeFeed) && !visibleLocalCount && (
                      <div className="empty">
                        <FolderOpen size={35} />
                        <h3>
                          {sourceView
                            ? "暂无匹配的来源"
                            : authorView
                              ? "暂无匹配的作者"
                              : query
                                ? "没有匹配的内容"
                                : "这里暂时没有内容"}
                        </h3>
                        <p>
                          {isCollection
                            ? "调整搜索或标签，或导入本地媒体。"
                            : "切换分类，或添加一条下载任务。"}
                        </p>
                      </div>
                    )}
                  </div>}
                </div>
                {activeFeed && activeFeed.total > 0 && (
                  <ContinuousFooter loaded={activeFeed.tasks.length} total={activeFeed.total}
                    loading={activeFeed.loading} done={activeFeed.next >= activeFeed.total}
                    error={activeFeed.error} onLoad={appendTasks} onRetry={appendTasks} />
                )}
                {!mixedCollection && !localView && !infiniteTasks && data.total > taskPageSize && (
                  <div className="pagination">
                    <Button
                      variant="outline"
                      size="sm"
                      disabled={!offset}
                      onClick={() => paginate(Math.max(0, offset - taskPageSize))}
                    >
                      上一页
                    </Button>
                    <span>
                      {offset + 1}–{Math.min(offset + taskPageSize, data.total)} /{" "}
                      {data.total}
                    </span>
                    <Button
                      variant="outline"
                      size="sm"
                      disabled={offset + taskPageSize >= data.total}
                      onClick={() => paginate(offset + taskPageSize)}
                    >
                      下一页
                    </Button>
                  </div>
                )}
              </>
            )}
            <div hidden={page !== "overview" || !!scope}>
              <ResourceBrowser
                libraries={data.libraries}
                kind={resourceKind}
                onKind={chooseResourceKind}
                onLibraries={() => navigate("archive", true)}
                options={data.tag_options || []}
                revision={JSON.stringify(
                  data.libraries.map((l) => [l.id, l.media_count, l.path]),
                )}
                active={page === "overview" && !scope}
                infiniteScroll={infiniteScroll}
                overviewLayout={data.settings.overview_layout}
                onSaveLayout={(value) => save({ overview_layout: value })}
                librarySection={libraryCards}
              />
            </div>
            {page === "archive" && !scope && libraryCards}
            {page === "organize" && !toolOpen && (
              <button className="tool-entry" onClick={() => setToolOpen(true)}>
                <Layers size={30} />
                <strong>媒体转移</strong>
                <ChevronRight size={18} />
              </button>
            )}
            {desktop && page === "organize" && !toolOpen && <CloudUpload />}
            {page === "organize" && toolOpen && (
              <>
                <div className="library-breadcrumb">
                  <button onClick={() => setToolOpen(false)}>媒体工具</button>
                  <span>/ 媒体转移</span>
                </div>
                <section className="settings-card">
                  {organizeProgress && (
                    <div className="organize-progress" aria-live="polite">
                      <span>
                        {organizeProgress.text} {organizeProgress.done} /{" "}
                        {organizeProgress.total}
                      </span>
                      <progress
                        aria-label="整理进度"
                        max={Math.max(1, organizeProgress.total)}
                        value={organizeProgress.done}
                      />
                    </div>
                  )}
                  <div className="section-icon">
                    <Layers size={26} />
                  </div>
                  <div className="folder-path">
                    <FolderOpen size={18} />
                    {pickLibrary}
                  </div>
                  {data.library_storage ? (
                    <div className="row-actions">
                      <Button variant="outline" disabled={busy} onClick={() => start("verify")}>
                        完整性检查
                      </Button>
                    </div>
                  ) : <div className="row-actions">
                    <Button
                      disabled={busy || transition}
                      onClick={() =>
                        action(
                          "organize",
                          { library_id: library },
                          "开始整理媒体",
                        )
                      }
                    >
                      <Layers size={15} />
                      集中转移
                    </Button>
                    <Button
                      variant="outline"
                      disabled={busy || transition}
                      onClick={() =>
                        action(
                          "organize",
                          { library_id: library, rollback: true },
                          "开始回退",
                        )
                      }
                    >
                      <Undo2 size={15} />
                      转移回退
                    </Button>
                    <Button
                      variant="outline"
                      disabled={busy}
                      onClick={() => start("verify")}
                    >
                      完整性检查
                    </Button>
                    <Button
                      variant="outline"
                      disabled={busy}
                      onClick={() =>
                        action(
                          "organize",
                          { library_id: library, rollback: true, search: true },
                          "开始查找并回退",
                        )
                      }
                    >
                      查找并回退
                    </Button>
                    <Button
                      variant="outline"
                      disabled={busy}
                      onClick={() =>
                        action(
                          "organize",
                          { library_id: library, custom_target: true },
                          "开始整理；目标需位于已配置媒体库内",
                        )
                      }
                    >
                      选择整理位置
                    </Button>
                  </div>}
                </section>
                {data.library_storage && (
                  <LibraryMigration key={library}
                    library={data.libraries.find((item) => item.id === library)}
                    changed={localChanged}
                  />
                )}
              </>
            )}
            <TelegramDownloads
              active={page === "telegram"}
              libraries={data.libraries}
              defaultLibrary={data.default_library}
              accountReset={telegramAccountReset}
              onOpenAccount={() => {
                setSettingsTab("telegram-account");
                navigate("settings");
              }}
            />
            {page === "settings" && (
              <>
                <div className="settings-tabs tabs">
                  {[
                    ["appearance", "外观"],
                    ["libraries", "媒体库"],
                    ["account", "账号与下载"],
                    ["telegram-account", "电报账号"],
                    ["automation", "自动任务"],
                    ["privacy", "隐私"],
                    ["management", "媒体管理"],
                  ].map(([id, label]) => (
                    <button
                      key={id}
                      className={settingsTab === id ? "active" : ""}
                      onClick={() => setSettingsTab(id)}
                    >
                      {label}
                    </button>
                  ))}
                </div>
                {settingsTab === "management" ? (
                  <FiltersPanel
                    confirm={data.settings.confirm_removal !== false}
                    save={save}
                  />
                ) : settingsTab === "privacy" ? (
                  <PrivacyPanel
                    enabled={!!data.settings.privacy_mode}
                    save={save}
                  />
                ) : settingsTab === "appearance" ? (
                  <>
                    <ThemePanel settings={data.settings} save={save} />
                    <label className="privacy-setting">
                      <input type="checkbox" checked={infiniteScroll} disabled={transition}
                        onChange={(event) => void save({ infinite_scroll: event.currentTarget.checked })} />
                      瀑布流连续加载
                    </label>
                  </>
                ) : settingsTab === "libraries" ? (
                  <>
                    <LibraryPanel libraries={data.libraries} action={action} />
                    {data.library_storage && <AutoFlattenSettings
                      libraries={data.libraries}
                      busy={busy}
                      applying={transition}
                      progress={organizeProgress}
                      apply={(id, enabled) => {
                        setOrganizeProgress(null);
                        return action("library_auto_flatten", { library_id: id, enabled });
                      }}
                    />}
                  </>
                ) : settingsTab === "automation" ? (
                  <AutomationSettings settings={data.settings} save={save} pending={transition} />
                ) : settingsTab === "telegram-account" ? (
                  <TelegramAccountSettings
                    onAccountChanged={() => setTelegramAccountReset((value) => value + 1)}
                  />
                ) : (
                  <AccountPanel
                    settings={data.settings}
                    save={save}
                    action={action}
                    hasCredentials={data.has_credentials}
                  />
                )}
              </>
            )}
            <div className="page-foot">
              <span>StashRat / {data.version}</span>
            </div>
          </main>
        </div>
        <ContentContextMenu
          target={contextTarget}
          close={() => setContextTarget(null)}
          remove={setRemoveIds}
        />
        <RemovalDialog
          ids={removeIds}
          libraryId={scope || library}
          libraryName={currentLibrary?.name || "当前媒体库"}
          confirm={data.settings.confirm_removal !== false}
          close={() => setRemoveIds(null)}
          save={save}
          changed={() => {
            setSelected([]);
            setDetail(null);
            if (mixedCollection) setLocalRevision((value) => value + 1);
            void latestRefresh.current();
            notify("正在移除；结果与重试可在设置的过滤媒体名单查看");
          }}
        />
        <DownloadDialog
          mode={adding}
          close={() => setAdding(null)}
          library={library}
          picker={pickLibrary}
          current={currentLibrary}
          action={action}
          busy={busy}
          transition={transition}
          hasCredentials={data.has_credentials}
          username={data.settings.username}
          configure={() => {
            setAdding(null);
            navigate("settings");
            setSettingsTab("account");
          }}
          downloaded={() => navigate("downloads")}
        />
        <Modal
          drawer
          open={!!detail}
          onClose={() => setDetail(null)}
          title="内容详情"
        >
          {detail && selectedMedia && (
            <>
              <div className="drawer-body">
                <div className="detail-author">
                  <ContentIdentity
                    content={detail}
                    action={action}
                    body={false}
                  />
                </div>
                <Tags
                  tweet={detail}
                  options={data.tag_options || []}
                  action={action}
                  onFilter={(tag) => {
                    setTagFilters((prev) =>
                      prev.includes(tag) ? prev : [...prev, tag],
                    );
                    setOffset(0);
                  }}
                />
                <PrivateText text={detail.text || ""} />
                <Thumbnail
                  detail
                  media={selectedMedia}
                  tweet={detail}
                  index={detailIndex}
                  progress={progress[selectedMedia.id]}
                  onClick={() => {
                    if (desktop && selectedMedia.media_type === "video" && selectedMedia.state === "completed") {
                      play({ source: "x", id: selectedMedia.id });
                      return;
                    }
                    void action(
                      "open_media", { job_id: selectedMedia.id },
                      desktop ? undefined : "原型播放演示：未打开本地文件",
                    );
                  }}
                />
                <div className="detail-navigation">
                  <button
                    className="icon-btn"
                    aria-label="上一个媒体"
                    disabled={!detailIndex}
                    onClick={() => setDetailIndex(detailIndex - 1)}
                  >
                    <ChevronLeft size={16} />
                  </button>
                  <span>
                    {detailIndex + 1} / {detail.media.length}（含画质及库版本）
                  </span>
                  <button
                    className="icon-btn"
                    aria-label="下一个媒体"
                    disabled={detailIndex + 1 >= detail.media.length}
                    onClick={() => setDetailIndex(detailIndex + 1)}
                  >
                    <ChevronRight size={16} />
                  </button>
                </div>
                <select
                  className="detail-version"
                  aria-label="媒体版本"
                  value={detailIndex}
                  onChange={(e) => setDetailIndex(Number(e.target.value))}
                >
                  {detail.media.map((m, i) => (
                    <option key={m.id} value={i}>
                      {m.media_type === "video" ? "视频" : "图片"} {m.media_id}{" "}
                      · {m.quality} · {m.output}
                    </option>
                  ))}
                </select>
                <h3>媒体信息</h3>
                <dl>
                  <div>
                    <dt>状态</dt>
                    <dd>{labels[selectedMedia.state]}</dd>
                  </div>
                  <div>
                    <dt>大小</dt>
                    <dd>{bytes(selectedMedia.bytes)}</dd>
                  </div>
                  <div>
                    <dt>完整性检查</dt>
                    <dd>{selectedMedia.verification || "下载后校验"}</dd>
                  </div>
                  <div>
                    <dt>保存位置</dt>
                    <dd className="path">{selectedMedia.output}</dd>
                  </div>
                </dl>
                {selectedMedia.error && (
                  <div className="error-banner">{selectedMedia.error}</div>
                )}
                <div className="detail-tools">
                  {page === "downloads" && (
                    <>
                      <Button
                        size="sm"
                        variant="outline"
                        onClick={() =>
                          start("enrich", { job_id: selectedMedia.id })
                        }
                        disabled={busy}
                      >
                        补全预览
                      </Button>
                      <Button
                        size="sm"
                        variant="outline"
                        onClick={() =>
                          start("force", { job_id: selectedMedia.id })
                        }
                        disabled={busy || detail.ignored}
                      >
                        强制重新下载
                      </Button>
                    </>
                  )}
                  {contentPresentation(detail).canSource && (
                    <Button
                      size="sm"
                      variant="outline"
                      onClick={() =>
                        action("open_tweet", { job_id: selectedMedia.id })
                      }
                    >
                      <ExternalLink size={12} />
                      查看来源
                    </Button>
                  )}
                </div>
                {page === "downloads" && (
                  <div className="info-note">
                    <Info size={15} />
                    忽略不删除已有文件。取消忽略后，手动继续下载。
                  </div>
                )}
              </div>
              <footer>
                {page === "downloads" && (
                  <Button
                    variant="outline"
                    onClick={() => ignore([detail.id], !detail.ignored)}
                  >
                    {detail.ignored ? (
                      <Undo2 size={15} />
                    ) : (
                      <EyeOff size={15} />
                    )}{" "}
                    {detail.ignored ? "取消忽略" : "忽略内容"}
                  </Button>
                )}
                <Button
                  onClick={() =>
                    action(
                      "open_folder",
                      { job_id: selectedMedia.id },
                      desktop ? undefined : "示例文件夹 · 未访问本地文件",
                    )
                  }
                >
                  <FolderOpen size={15} />
                  打开文件夹
                </Button>
              </footer>
            </>
          )}
        </Modal>
        {localImport && (
          <LocalImportDialog library={localImport} close={() => setLocalImport(null)} changed={localChanged} />
        )}
        {localScan && (
          <LocalImportDialog library={localScan} scanCurrentPath close={() => setLocalScan(null)} changed={localChanged} />
        )}
        {randomVideoPicker && (
          <RandomVideoPicker
            libraries={data.libraries}
            defaultLibrary={data.default_library}
            remembered={lastRandomVideoLibraries ?? data.settings.random_video_libraries}
            onClose={() => setRandomVideoPicker(false)}
            onOpened={setLastRandomVideoLibraries}
          />
        )}
        {!adding && !detail && <ScrollNavigation />}
        {toast && (
          <div className="toast" role="status">
            <CheckCircle2 size={18} />
            <span>{toast.text}</span>
            {toast.undo && <button onClick={toast.undo}>撤销</button>}
            <button aria-label="关闭提示" onClick={() => setToast(null)}>
              <X size={14} />
            </button>
          </div>
        )}
      </div>
    </PrivacyProvider>
  );
}
