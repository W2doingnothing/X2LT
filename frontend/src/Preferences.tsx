import { EmojiPicker, validEmoji } from "./EmojiPicker";
import { useState, useEffect, type CSSProperties } from "react";
import {
  Sun,
  Moon,
  Check,
  CheckCircle2,
  FolderOpen,
  Plus,
  RefreshCw,
  HardDrive,
  Star,
  Trash2,
  Pencil,
  Palette,
  SlidersHorizontal,
  KeyRound,
  Unplug,
} from "lucide-react";
import type { Library, Settings, Payload } from "./types";
import { call, desktop } from "./api";
import { applyTheme, themeColors, contrast } from "./theme";
import { appIcons, appIconUrl, resolveAppIcon } from "./app-icons";
import { Button } from "./components/ui/button";
import { Modal } from "./components/ui/dialog";
import { PrivateText } from "./Privacy";
export const GB = 1024 ** 3;
export const bytes = (n: number | undefined) =>
  n === undefined
    ? "—"
    : n >= GB
      ? `${(n / GB).toFixed(1)} GB`
      : n >= 1024 ** 2
        ? `${(n / 1024 ** 2).toFixed(1)} MB`
        : `${Math.floor(n / 1024)} KB`;
export type Action = (
  action: string,
  payload?: Payload,
  message?: string,
) => Promise<boolean>;
export function ThemePanel({
  settings,
  save,
}: {
  settings: Settings;
  save: (p: Payload) => Promise<boolean>;
}) {
  const [draft, setDraft] = useState(settings.accent),
    [error, setError] = useState("");
  useEffect(() => setDraft(settings.accent), [settings.accent]);
  const normalize = (s: string) => {
    s = s.trim();
    if (!s.startsWith("#")) s = "#" + s;
    if (/^#[0-9a-f]{3}$/i.test(s))
      s =
        "#" +
        s
          .slice(1)
          .split("")
          .map((c) => c + c)
          .join("");
    return s.toLowerCase();
  };
  const preview = (s: string) => {
    setDraft(s);
    setError("");
    try {
      applyTheme(normalize(s), settings.theme === "dark");
    } catch {
      /* Incomplete typing leaves the last valid preview. */
    }
  };
  const commit = async (s: string) => {
    const color = normalize(s);
    try {
      themeColors(color, settings.theme === "dark");
      if (await save({ accent: color })) setDraft(color);
    } catch (e) {
      setError((e as Error).message);
    }
  };
  useEffect(
    () => () => applyTheme(settings.accent, settings.theme === "dark"),
    [settings.accent, settings.theme],
  );
  const source = /^#[0-9a-f]{6}$/i.test(normalize(draft))
    ? normalize(draft)
    : settings.accent;
  const appIcon = resolveAppIcon(settings.app_icon);
  return (
    <section className="settings-card">
      <h2>外观</h2>
      <div className="theme-options">
        {(
          [
            ["light", Sun, "浅色"],
            ["dark", Moon, "深色"],
          ] as const
        ).map(([mode, Icon, label]) => {
          const palette = themeColors(source, mode === "dark");
          const t = palette.tokens;
          return (
            <button
              key={mode}
              aria-label={label}
              className={
                "theme-option " + (settings.theme === mode ? "chosen" : "")
              }
              onClick={() => save({ theme: mode })}
            >
              <div
                className={"mini-preview " + mode}
                style={
                  {
                    "--preview-bg": t.bg,
                    "--preview-side": t["bg-subtle"],
                    "--preview-surface": t.surface,
                    "--preview-border": t.border,
                    "--preview-accent": t.accent,
                  } as CSSProperties
                }
              >
                <i />
                <span />
                <b />
                <em />
              </div>
              <span>
                <Icon size={15} />
                {label}
                {settings.theme === mode && <CheckCircle2 size={15} />}
              </span>
            </button>
          );
        })}
      </div>
      <hr />
      <h3>主题色</h3>
      <div className="accent-editor">
        <label
          className="color-picker"
          title="打开主题色盘"
          style={{
            background: source,
            color:
              contrast(source, "#ffffff") > contrast(source, "#000000")
                ? "#ffffff"
                : "#000000",
          }}
        >
          <input
            type="color"
            aria-label="主题色盘"
            value={
              /^#[0-9a-f]{6}$/i.test(normalize(draft))
                ? normalize(draft)
                : settings.accent
            }
            onChange={(e) => {
              preview(e.target.value);
              void commit(e.target.value);
            }}
          />
          <Palette size={17} />
        </label>
        <label className="field hex-field">
          <span>HEX</span>
          <input
            aria-label="十六进制主题色"
            value={draft}
            maxLength={7}
            onChange={(e) => preview(e.target.value)}
            onBlur={() => void commit(draft)}
            onKeyDown={(e) => {
              if (e.key === "Enter") void commit(draft);
            }}
          />
        </label>
        <Button size="sm" onClick={() => commit(draft)}>
          应用颜色
        </Button>
        <Button
          size="sm"
          variant="outline"
          onClick={() => {
            preview("#397863");
            void commit("#397863");
          }}
        >
          恢复默认
        </Button>
      </div>
      {error && (
        <p className="error" role="alert">
          {error}
        </p>
      )}
      <div className="swatches">
        {[
          "#397863",
          "#4f6bd9",
          "#7c5bb4",
          "#b65779",
          "#ba6a36",
          "#64748b",
          "#111827",
        ].map((color) => (
          <button
            key={color}
            aria-label={`选择主题色 ${color}`}
            style={{
              background: color,
              color:
                contrast(color, "#ffffff") >= contrast(color, "#000000")
                  ? "#ffffff"
                  : "#000000",
            }}
            onClick={() => {
              preview(color);
              void commit(color);
            }}
          >
            {settings.accent === color && <Check size={14} />}
          </button>
        ))}
      </div>
      <div className="theme-sample">
        <div>
          <span className="sample-dot" />
          效果预览
        </div>
        <Button size="sm">主要按钮</Button>
        <span className="status success">
          <CheckCircle2 size={12} />
          标签
        </span>
        <div className="sample-progress">
          <span />
        </div>
      </div>
      <hr />
      <h3 id="app-icon-heading">应用图标</h3>
      <div className="app-icon-options" role="group" aria-labelledby="app-icon-heading">
        {appIcons.map(({ id, label }) => (
          <button
            key={id}
            type="button"
            className="app-icon-option"
            aria-pressed={appIcon === id}
            onClick={() => void save({ app_icon: id })}
          >
            <img src={appIconUrl(id)} width={48} height={48} alt="" />
            <span>{label}</span>
          </button>
        ))}
      </div>
    </section>
  );
}
export function LibraryEditor({
  target,
  action,
  close,
}: {
  target: Library | "new";
  action: Action;
  close: () => void;
}) {
  const creating = target === "new";
  const [name, setName] = useState(creating ? "" : target.name),
    [emoji, setEmoji] = useState(creating ? "📁" : target.emoji || "📁"),
    [path, setPath] = useState(creating ? "" : target.path),
    [quota, setQuota] = useState(creating || !target.quota_bytes ? "" : String(target.quota_bytes / GB)),
    [scanMode, setScanMode] = useState<Library["scan_mode"]>(creating ? "media" : target.scan_mode ?? "media"),
    [error, setError] = useState(""),
    [saving, setSaving] = useState(false),
    [picked, setPicked] = useState(false);
  async function choose() {
    try {
      const result = await call<string | null>("choose_folder");
      if (result) {
        setPath(result);
        setPicked(true);
        if (!name)
          setName(result.split(/[\\/]/).filter(Boolean).at(-1) || "新媒体库");
      }
    } catch (e) {
      setError((e as Error).message);
    }
  }
  async function submit(e: React.FormEvent) {
    e.preventDefault();
    setError("");
    if (!name.trim() || !path.trim()) {
      setError("请填写库名称并选择文件夹");
      return;
    }
    const number = quota === "" ? 0 : Number(quota);
    if (!Number.isFinite(number) || number < 0 || number > 1024 ** 2) {
      setError("请输入有效的存储上限");
      return;
    }
    if (!validEmoji(emoji)) {
      setError("请选择一个 emoji 标识");
      return;
    }
    setSaving(true);
    const payload: Payload = {
      emoji: emoji.trim(),
      name: name.trim(),
      quota_bytes: Math.round(number * GB),
      scan_mode: scanMode,
    };
    if (creating || picked || !desktop) payload.path = path;
    if (!creating) payload.id = target.id;
    const ok = await action(
      creating ? "library_add" : "library_update",
      payload,
      "媒体库已保存",
    );
    setSaving(false);
    if (ok) close();
  }
  return (
      <Modal
        open
        onClose={() => { if (!saving) close(); }}
        title={creating ? "添加媒体库" : "编辑媒体库"}
      >
        <form onSubmit={submit} className="library-form">
          <label>
            库名称
            <input
              value={name}
              maxLength={80}
              onChange={(e) => setName(e.target.value)}
              placeholder="例如：日常收藏"
              autoFocus
            />
          </label>
          <EmojiPicker value={emoji} onChange={setEmoji} />
          <label>
            保存位置
            <div className="folder-input">
              <input
                aria-label="媒体库路径"
                value={path}
                readOnly={desktop}
                onChange={(e) => setPath(e.target.value)}
                placeholder={
                  desktop ? "点击右侧按钮选择文件夹" : "例如 F:\\Media"
                }
              />
              <Button
                type="button"
                variant="outline"
                size="sm"
                onClick={choose}
              >
                <FolderOpen size={15} />
                选择文件夹
              </Button>
            </div>
          </label>
          {!desktop && (
            <small className="muted-text">
              原型中路径与容量为模拟数据；正式桌面版打开系统文件夹选择器。
            </small>
          )}
          <label>
            最大存储量
            <div className="quota-input">
              <input
                aria-label="最大存储量"
                type="number"
                min="0"
                max="1048576"
                step="0.1"
                value={quota}
                onChange={(e) => setQuota(e.target.value)}
                placeholder="留空表示不限额"
              />
              <span>GB</span>
            </div>
          </label>
          <small className="muted-text">
            支持小数；0 或留空表示不设置配额上限。
          </small>
          <label>
            扫描模式
            <select
              value={scanMode}
              onChange={(event) => setScanMode(event.target.value === "comic" ? "comic" : "media")}
            >
              <option value="media">图片 / 视频</option>
              <option value="comic">漫画</option>
            </select>
          </label>
          {!creating && (
            <div className="collection-card-actions">
              <Button type="button" variant="outline" size="sm" disabled={saving}
                onClick={() => action("library_cover", { id: target.id, kind: "image" })}>
                导入封面图片
              </Button>
              <Button type="button" variant="outline" size="sm" disabled={saving}
                onClick={() => action("library_cover", { id: target.id, kind: "reset" })}>
                重置封面
              </Button>
            </div>
          )}
          {error && (
            <div className="error" role="alert">
              {error}
            </div>
          )}
          <div className="modal-footer">
            <Button
              type="button"
              variant="outline"
              onClick={close}
              disabled={saving}
            >
              取消
            </Button>
            <Button type="submit" disabled={saving}>
              {saving ? "保存中…" : "保存媒体库"}
            </Button>
          </div>
        </form>
      </Modal>
  );
}
export function LibraryPanel({
  libraries,
  action,
}: {
  libraries: Library[];
  action: Action;
}) {
  const [edit, open] = useState<Library | "new" | null>(null);
  const [remove, setRemove] = useState<Library | null>(null);
  const [excluded, setExcluded] = useState<Library | null>(null);
  return (
    <>
      <div className="library-heading">
        <div>
          <h2>媒体库</h2>
        </div>
        <div className="row-actions">
          <Button
            variant="outline"
            size="sm"
            onClick={() => action("library_refresh", {}, "容量已刷新")}
          >
            <RefreshCw size={14} />
            刷新容量
          </Button>
          <Button size="sm" onClick={() => open("new")}>
            <Plus size={15} />
            添加媒体库
          </Button>
        </div>
      </div>
      <div className="library-grid">
        {libraries.map((lib) => (
          <article
            className={"library-card " + (!lib.online && !lib.capacity_pending ? "offline" : "")}
            key={lib.id}
          >
            <div className="library-card-head">
              <span className="library-icon">
                <FolderOpen size={22} />
              </span>
              <div>
                {desktop && (
                  <button className="text-btn" onClick={() => setExcluded(lib)}>
                    扫描跳过名单
                  </button>
                )}
                <h3>
                  {lib.emoji || "📁"} {lib.name}
                </h3>
                <span className="library-state">
                  {lib.default && (
                    <>
                      <Star size={10} />
                      默认库<span>·</span>
                    </>
                  )}
                  {lib.capacity_pending ? "正在检查容量" : lib.online ? "已连接" : "路径不可用"}
                </span>
              </div>
              <button
                className="icon-btn"
                aria-label={`编辑 ${lib.name}`}
                onClick={() => open(lib)}
              >
                <Pencil size={15} />
              </button>
            </div>
            <div className="library-path" title={lib.path}>
              {lib.path}
            </div>
            {(lib.online || lib.capacity_stale) ? (
              <>
                <div className="capacity-heading">
                  <strong>
                    {bytes(lib.used_bytes)}
                    <small> 已使用</small>
                  </strong>
                  <span>
                    {lib.quota_bytes
                      ? `上限 ${bytes(lib.quota_bytes)}`
                      : "不设配额上限"}
                  </span>
                </div>
                <div className="capacity-track">
                  <span
                    className={(lib.available_bytes || 0) < GB ? "low" : ""}
                    style={{
                      width: `${Math.min(100, (100 * (lib.used_bytes || 0)) / (lib.quota_bytes || (lib.used_bytes || 0) + (lib.disk_free || 0) || 1))}%`,
                    }}
                  />
                </div>
                <dl className="capacity-list">
                  <div>
                    <dt>配额剩余</dt>
                    <dd>
                      {lib.quota_remaining === null
                        ? "不限额"
                        : bytes(lib.quota_remaining)}
                    </dd>
                  </div>
                  <div>
                    <dt>磁盘剩余 / 总容量</dt>
                    <dd>
                      {bytes(lib.disk_free)} / {bytes(lib.disk_total)}
                    </dd>
                  </div>
                  <div className="capacity-available">
                    <dt>{lib.capacity_stale ? "上次可用" : "当前可用"}</dt>
                    <dd>{bytes(lib.available_bytes)}</dd>
                  </div>
                  {!!lib.reserved_bytes && (
                    <div>
                      <dt>下载临时空间预留</dt>
                      <dd>{bytes(lib.reserved_bytes)}</dd>
                    </div>
                  )}
                </dl>
              </>
            ) : lib.capacity_pending ? (
              <div className="offline-note"><p>正在检查容量…</p></div>
            ) : (
              <div className="offline-note">
                <Unplug size={20} />
                <p>
                  文件夹不存在或磁盘未连接。重新定位后可继续使用，下载记录仍然保留。
                </p>
              </div>
            )}
            <footer>
              {lib.online && !lib.default ? (
                <button
                  className="text-btn"
                  onClick={() =>
                    action(
                      "library_default",
                      { id: lib.id },
                      "默认媒体库已更新",
                    )
                  }
                >
                  设为默认库
                </button>
              ) : (
                <span className="text-btn">
                  {lib.default ? "新任务默认保存到这里" : lib.capacity_pending ? "正在检查容量" : "媒体库离线"}
                </span>
              )}
              <div>
                {!desktop && (
                  <button
                    className="text-btn"
                    onClick={() => action("demo_offline", { id: lib.id })}
                  >
                    {lib.online ? "模拟断开" : "模拟连接"}
                  </button>
                )}
                <button
                  className="icon-btn"
                  aria-label={`移除 ${lib.name}`}
                  onClick={() => setRemove(lib)}
                >
                  <Trash2 size={14} />
                </button>
              </div>
            </footer>
          </article>
        ))}
      </div>
      {!libraries.length && (
        <div className="empty">
          <FolderOpen size={32} />
          <h3>添加你的第一个媒体库</h3>
          <Button onClick={() => open("new")}>添加媒体库</Button>
        </div>
      )}
      {edit && <LibraryEditor target={edit} action={action} close={() => open(null)} />}
      {excluded && <LibraryScanExclusions library={excluded} close={() => setExcluded(null)} />}
      <Modal open={!!remove} onClose={() => setRemove(null)} title="移除媒体库">
        <p>将“{remove?.name}”从列表中移除。文件、下载记录与凭证均会保留。</p>
        <div className="modal-footer">
          <Button variant="outline" onClick={() => setRemove(null)}>
            保留
          </Button>
          <Button
            onClick={async () => {
              if (
                remove &&
                (await action(
                  "library_remove",
                  { id: remove.id },
                  "媒体库已移除，文件和记录保留",
                ))
              )
                setRemove(null);
            }}
          >
            移除管理关系
          </Button>
        </div>
      </Modal>
    </>
  );
}

function LibraryScanExclusions({ library, close }: { library: Library; close: () => void }) {
  const [items, setItems] = useState<{ id: number; title: string }[]>();
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  useEffect(() => {
    let mounted = true;
    void call<{ id: number; title: string }[]>("local_scan_exclusions", { library_id: library.id })
      .then((result) => { if (mounted) setItems(result); })
      .catch((reason) => { if (mounted) setError(String(reason)); });
    return () => { mounted = false; };
  }, [library.id]);

  async function restore(id: number) {
    setBusy(true);
    setError("");
    try {
      await call("local_scan_restore", { library_id: library.id, id });
      setItems((current) => current?.filter((item) => item.id !== id));
    } catch (reason) {
      setError(String(reason));
    } finally {
      setBusy(false);
    }
  }

  return (
    <Modal open title={`扫描跳过名单 · ${library.name}`} onClose={close}>
      {!!items?.length && (
        <>
          <p>取消跳过后，该项目会在下一次手动扫描时重新出现。</p>
          <ul className="local-import-list">
            {items.map((item) => (
              <li key={item.id}>
                <PrivateText compact text={item.title} />
                <Button size="sm" variant="outline" disabled={busy} onClick={() => void restore(item.id)}>
                  取消跳过
                </Button>
              </li>
            ))}
          </ul>
        </>
      )}
      {items && !items.length && <p>此库没有跳过项目。</p>}
      {!items && !error && <p role="status">正在读取名单…</p>}
      {error && <p className="error" role="alert">{error}</p>}
    </Modal>
  );
}

export function AccountPanel({
  settings,
  save,
  action,
  hasCredentials,
}: {
  settings: Settings;
  save: (p: Payload) => Promise<boolean>;
  action: Action;
  hasCredentials: boolean;
}) {
  const [draft, setDraft] = useState(settings),
    [sizeUnit, setSizeUnit] = useState(1),
    [auth, setAuth] = useState(""),
    [ct0, setCt0] = useState(""),
    [proxy, setProxy] = useState("");
  useEffect(() => setDraft(settings), [settings]);
  return (
    <section className="settings-card">
      <h2>账号与下载</h2>
      <form
        className="account-form"
        onSubmit={async (e) => {
          e.preventDefault();
          const { username, quality, connections, max_mb, telegram_task_limit, media_mode, request_strategy } = draft;
          await save({ username, quality, connections, max_mb, telegram_task_limit: telegram_task_limit ?? 0,
            media_mode, request_strategy });
        }}
      >
        <label>
          X 用户名
          <input
            value={draft.username}
            onChange={(e) => setDraft({ ...draft, username: e.target.value })}
            placeholder="用户名，不含 @"
          />
        </label>
        <div className="form-columns">
          <label>
            媒体类型
            <select
              value={draft.media_mode}
              onChange={(e) =>
                setDraft({ ...draft, media_mode: e.target.value })
              }
            >
              <option value="all">视频＋图片</option>
              <option value="video">仅视频</option>
              <option value="image">仅图片</option>
            </select>
          </label>
          <label>
            视频画质
            <select
              value={draft.quality}
              onChange={(e) => setDraft({ ...draft, quality: e.target.value })}
            >
              <option value="best">最高画质</option>
              <option value="720p">720p</option>
              <option value="480p">480p</option>
            </select>
          </label>
          <label>
            分段连接数
            <select
              value={draft.connections}
              onChange={(e) =>
                setDraft({ ...draft, connections: Number(e.target.value) })
              }
            >
              {[1, 2, 4].map((n) => (
                <option key={n}>{n}</option>
              ))}
            </select>
          </label>
          <label>
            单个媒体最大体积（0 为不限）
            <input
              type="number"
              min="0"
              max={65536 / sizeUnit}
              step={sizeUnit === 1 ? 1 : "any"}
              value={draft.max_mb / sizeUnit}
              onChange={(e) =>
                setDraft({ ...draft, max_mb: Math.round(Number(e.target.value) * sizeUnit) })
              }
            />
          </label>
          <label>
            体积单位
            <select value={sizeUnit} onChange={(e) => setSizeUnit(Number(e.target.value))}>
              <option value={1}>MB</option>
              <option value={1024}>GB</option>
            </select>
          </label>
          <label>
            Telegram 最大待处理任务数（0 为不限）
            <input type="number" min="0" max="100000" value={draft.telegram_task_limit ?? 0}
              onChange={(e) => setDraft({ ...draft, telegram_task_limit: Number(e.target.value) })} />
          </label>
          <label>
            请求策略
            <select
              value={draft.request_strategy}
              onChange={(e) =>
                setDraft({ ...draft, request_strategy: e.target.value })
              }
            >
              <option value="conservative">保守 · 1 MiB</option>
              <option value="balanced">均衡 · 4 MiB</option>
              <option value="throughput">吞吐 · 8 MiB</option>
            </select>
          </label>
        </div>
        <Button type="submit">保存下载设置</Button>
      </form>
      <hr />
      <h3>
        登录凭证{" "}
        <span className="status success">
          {hasCredentials ? "已保存" : "未配置"}
        </span>
      </h3>
      <div className="account-form">
        <label>
          auth_token
          <input
            type="password"
            value={auth}
            autoComplete="off"
            onChange={(e) => setAuth(e.target.value)}
            placeholder="不回显已保存的凭证"
          />
        </label>
        <label>
          ct0
          <input
            type="password"
            value={ct0}
            autoComplete="off"
            onChange={(e) => setCt0(e.target.value)}
          />
        </label>
        <label>
          代理地址
          <input
            type="password"
            value={proxy}
            autoComplete="off"
            onChange={(e) => setProxy(e.target.value)}
            placeholder="留空保留原配置"
          />
        </label>
        <div className="row-actions">
          <Button
            onClick={async () => {
              const p: Payload = {};
              if (auth) p.auth_token = auth;
              if (ct0) p.ct0 = ct0;
              if (proxy) p.proxy = proxy;
              if (await action("credentials", p, "凭证已加密保存")) {
                setAuth("");
                setCt0("");
                setProxy("");
              }
            }}
          >
            <KeyRound size={15} />
            保存凭证
          </Button>
          <Button
            variant="outline"
            onClick={() =>
              action("import_legacy", {}, "配置已导入并加密，旧文件保留")
            }
          >
            导入已有配置
          </Button>
          <Button
            variant="outline"
            onClick={() =>
              action(
                "credentials",
                { proxy: "" },
                "代理已禁用，后续任务使用直连",
              )
            }
          >
            禁用代理
          </Button>
          <Button
            variant="outline"
            onClick={() =>
              action(
                "credentials",
                { auth_token: "", ct0: "" },
                "登录凭证已清除",
              )
            }
          >
            清除登录
          </Button>
        </div>
      </div>
      <Button
        variant="outline"
        size="sm"
        onClick={() => action("clear_previews", {}, "预览缓存已清理")}
      >
        清空预览缓存
      </Button>
    </section>
  );
}
