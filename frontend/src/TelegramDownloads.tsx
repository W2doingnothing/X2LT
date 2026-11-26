import { useEffect, useState, type FormEvent } from "react";
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
import { PrivateText, usePrivateReveal } from "./Privacy";
import type { Library } from "./types";
import "./telegram-downloads.css";

type Account = {
  configured: boolean;
  authorized: boolean;
  has_proxy?: boolean;
  code_sent?: boolean;
  password_required?: boolean;
};
type MediaKind = "video" | "image";
type Media = {
  id: string;
  title: string;
  kind: MediaKind;
  size: number;
  peer_title: string;
};
type Scan = {
  state: "scanning" | "ready" | "failed" | "cancelled";
  items: Media[];
  error?: string;
};
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
type Job = {
  id: string;
  title: string;
  kind: MediaKind;
  state: keyof typeof jobStates;
  received: number;
  total: number;
  speed: number;
  error: string;
};

function TelegramWorkspace({
  active,
  libraries,
  defaultLibrary,
}: {
  active: boolean;
  libraries: Library[];
  defaultLibrary: string | null;
}) {
  const [account, setAccount] = useState<Account | null>(null);
  const [phone, setPhone] = useState("");
  const [code, setCode] = useState("");
  const [password, setPassword] = useState("");
  const [proxy, setProxy] = useState("");
  const [mode, setMode] = useState<"link" | "channel">("link");
  const [link, setLink] = useState("");
  const [channel, setChannel] = useState("");
  const [channels, setChannels] = useState<Channel[] | null>(null);
  const [limit, setLimit] = useState("50");
  const [mediaMode, setMediaMode] = useState<MediaKind | "all">("all");
  const [scanId, setScanId] = useState("");
  const [scan, setScan] = useState<Scan | null>(null);
  const [selected, setSelected] = useState<string[]>([]);
  const [library, setLibrary] = useState("");
  const [jobs, setJobs] = useState<Job[] | null>(null);
  const [jobsError, setJobsError] = useState("");
  const [queueRevision, setQueueRevision] = useState(0);
  const [busy, setBusy] = useState("");
  const [error, setError] = useState("");
  const [notice, setNotice] = useState("");
  const channelPrivacy = usePrivateReveal();
  const checking = scan?.state === "scanning";
  const codeSent = account?.code_sent;
  const onlineLibraries = libraries.filter((item) => item.online);
  const target =
    onlineLibraries.find((item) => item.id === library) ??
    onlineLibraries.find((item) => item.id === defaultLibrary) ??
    onlineLibraries[0];
  const source = (mode === "link" ? link : channel).trim();

  async function run(action: string, operation: () => Promise<void>) {
    setBusy(action);
    setError("");
    setNotice("");
    try {
      await operation();
    } catch (failure) {
      setError(failure instanceof Error ? failure.message : "操作失败，请重试");
      if (
        ["tg_sign_in", "tg_channels", "tg_enqueue", "tg_control"].includes(
          action,
        )
      ) {
        const latest = await call<Account>("tg_status").catch(() => null);
        if (latest) setAccount(latest);
      }
    } finally {
      setBusy("");
    }
  }

  useEffect(() => {
    if (!active) {
      setCode("");
      setPassword("");
      return;
    }
    let alive = true;
    void call<Account>("tg_status")
      .then((result) => {
        if (alive) setAccount(result);
      })
      .catch(() => {
        if (alive) setError("无法读取 Telegram 账号状态，请重试。");
      });
    return () => {
      alive = false;
    };
  }, [active]);

  useEffect(() => {
    if (!active || !scanId || !checking) return;
    let alive = true;
    let timer: number;
    async function poll() {
      try {
        const result = await call<Scan>("tg_scan_status", { id: scanId });
        const latest =
          result.state === "failed"
            ? await call<Account>("tg_status").catch(() => null)
            : null;
        if (!alive) return;
        setScan(result);
        if (latest) setAccount(latest);
        if (result.state === "ready")
          setSelected(result.items.map((item) => item.id));
        if (result.state === "scanning") timer = window.setTimeout(poll, 600);
      } catch {
        if (alive)
          setScan({
            state: "failed",
            items: [],
            error: "无法获取扫描结果，请重新扫描。",
          });
      }
    }
    void poll();
    return () => {
      alive = false;
      window.clearTimeout(timer);
    };
  }, [active, scanId, checking]);

  useEffect(() => {
    if (!active) return;
    let alive = true;
    let timer: number;
    async function poll() {
      try {
        const result = await call<Job[]>("tg_jobs");
        if (!alive) return;
        setJobs(result);
        setJobsError("");
      } catch {
        if (alive) setJobsError("无法获取下载任务，正在重试。");
      }
      if (alive) timer = window.setTimeout(poll, 1000);
    }
    void poll();
    return () => {
      alive = false;
      window.clearTimeout(timer);
    };
  }, [active, queueRevision]);

  function clearAccountInputs() {
    setPhone("");
    setCode("");
    setPassword("");
  }

  function signIn(event: FormEvent) {
    event.preventDefault();
    if (busy) return;
    if (!account?.password_required && !codeSent) {
      void run("tg_send_code", async () => {
        const result = await call<Account>("tg_send_code", {
          phone: phone.trim(),
        });
        setAccount({ ...account, ...result, code_sent: !result.authorized });
        if (result.authorized) clearAccountInputs();
      });
      return;
    }
    const payload = account?.password_required
      ? { password }
      : { code: code.trim() };
    setCode("");
    setPassword("");
    void run("tg_sign_in", async () => {
      const result = await call<Account>("tg_sign_in", payload);
      setAccount({ ...account, ...result });
      if (result.authorized) clearAccountInputs();
    });
  }

  function clearScan() {
    setScanId("");
    setScan(null);
    setSelected([]);
  }

  function startScan(event: FormEvent) {
    event.preventDefault();
    if (!source || checking || busy) return;
    void run("tg_scan", async () => {
      clearScan();
      const result = await call<{ id: string }>("tg_scan", {
        mode,
        source,
        limit: Number(limit) || 50,
        media_mode: mediaMode,
      });
      setScanId(result.id);
      setScan({ state: "scanning", items: [] });
    });
  }

  function enqueue() {
    if (!target || !selected.length) return;
    void run("tg_enqueue", async () => {
      const count = await call<number>("tg_enqueue", {
        scan_id: scanId,
        ids: selected,
        library_id: target.id,
      });
      setNotice(
        count ? `已加入下载队列，共 ${count} 项。` : "没有新的下载任务。",
      );
      setSelected([]);
      setQueueRevision((value) => value + 1);
    });
  }

  function control(action: JobAction, id: string) {
    void run("tg_control", async () => {
      await call<boolean>("tg_control", { action, ids: [id] });
      setQueueRevision((value) => value + 1);
    });
  }

  const locked = Boolean(busy);
  const scanLocked = locked || checking;
  const accountLabel = account?.authorized
    ? "已登录"
    : account?.configured
      ? "等待登录"
      : "未导入 API 凭证";
  let loginLabel = "发送验证码";
  let loginField = (
    <label>
      手机号
      <input
        required
        maxLength={30}
        type="tel"
        autoComplete="off"
        placeholder="+86…"
        value={phone}
        onChange={(event) => setPhone(event.target.value)}
      />
    </label>
  );
  if (account?.password_required) {
    loginLabel = "验证并登录";
    loginField = (
      <label>
        两步验证密码
        <input
          required
          maxLength={512}
          type="password"
          autoComplete="off"
          value={password}
          onChange={(event) => setPassword(event.target.value)}
        />
      </label>
    );
  } else if (codeSent) {
    loginLabel = "验证并登录";
    loginField = (
      <label>
        验证码
        <input
          required
          maxLength={20}
          type="password"
          inputMode="numeric"
          autoComplete="one-time-code"
          value={code}
          onChange={(event) => setCode(event.target.value)}
        />
      </label>
    );
  }

  if (!active) return null;
  return (
    <div className="telegram-downloads">
      <h1>电报下载</h1>
      {error && (
        <p className="telegram-error" role="alert">
          {error}
        </p>
      )}
      {notice && <p role="status">{notice}</p>}
      <section
        className="settings-card telegram-panel"
        aria-labelledby="telegram-account"
      >
        <div className="telegram-heading">
          <h2 id="telegram-account">
            <ShieldCheck size={18} />
            Telegram 账号
          </h2>
          {account && <span className="telegram-status">{accountLabel}</span>}
        </div>
        {!account ? (
          error ? (
            <Button
              size="sm"
              variant="outline"
              disabled={locked}
              onClick={() =>
                void run("tg_status", async () =>
                  setAccount(await call<Account>("tg_status")),
                )
              }
            >
              检查账号状态
            </Button>
          ) : (
            <p>正在读取账号状态…</p>
          )
        ) : account.authorized ? (
          <div className="telegram-actions">
            <Button
              size="sm"
              variant="outline"
              disabled={scanLocked}
              onClick={() =>
                void run("tg_logout", async () => {
                  setAccount({
                    ...account,
                    ...(await call<Account>("tg_logout")),
                  });
                  clearAccountInputs();
                  clearScan();
                  setChannel("");
                  setLink("");
                  setChannels(null);
                  setQueueRevision((value) => value + 1);
                })
              }
            >
              退出登录
            </Button>
          </div>
        ) : (
          <>
            {!account.configured && (
              <p>选择包含 API ID 和 API Hash 的 TXT 文件。</p>
            )}
            <div className="telegram-actions">
              <Button
                size="sm"
                variant="outline"
                disabled={locked}
                onClick={() =>
                  void run("tg_import", async () => {
                    const result = await call<Account>("tg_import");
                    setAccount(result);
                    if (result.configured) clearAccountInputs();
                  })
                }
              >
                {account.configured ? "重新导入 API 凭证" : "导入 API 凭证"}
              </Button>
            </div>
            {account.configured && (
              <form
                className="telegram-actions telegram-fields"
                onSubmit={signIn}
              >
                {loginField}
                <Button type="submit" disabled={locked}>
                  {loginLabel}
                </Button>
                {(codeSent || account.password_required) && (
                  <Button
                    type="button"
                    variant="outline"
                    disabled={locked}
                    onClick={() => {
                      clearAccountInputs();
                      setAccount({
                        ...account,
                        code_sent: false,
                        password_required: false,
                      });
                    }}
                  >
                    更换手机号
                  </Button>
                )}
              </form>
            )}
            {codeSent && !account.password_required && (
              <p>验证码可能通过 Telegram 或短信送达。</p>
            )}
          </>
        )}
        {account && (
          <details className="telegram-options">
            <summary>代理设置{account.has_proxy ? " · 已设置" : ""}</summary>
            <form
              className="telegram-actions telegram-fields"
              onSubmit={(event) => {
                event.preventDefault();
                const url = proxy.trim();
                setProxy("");
                void run("tg_proxy", async () => {
                  setAccount(await call<Account>("tg_proxy", { url }));
                  setNotice(
                    url ? "Telegram 代理已保存。" : "已恢复默认网络设置。",
                  );
                });
              }}
            >
              <label>
                代理地址
                <input
                  type="password"
                  maxLength={2048}
                  autoComplete="off"
                  placeholder="socks5://127.0.0.1:1080"
                  value={proxy}
                  onChange={(event) => setProxy(event.target.value)}
                />
              </label>
              <Button type="submit" variant="outline" disabled={scanLocked}>
                保存代理
              </Button>
            </form>
            <p>留空保存时使用 X 的代理设置，未设置则直连。</p>
          </details>
        )}
      </section>

      {account?.authorized && (
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
              <Button type="submit" disabled={!source || scanLocked}>
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
          {checking && <p role="status">正在扫描媒体…</p>}
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

      {account?.authorized &&
        scan?.state === "ready" &&
        scan.items.length > 0 && (
          <section
            className="settings-card telegram-panel"
            aria-labelledby="telegram-preview"
          >
            <div className="telegram-heading">
              <h2 id="telegram-preview">媒体预览</h2>
              <Button
                size="sm"
                variant="outline"
                disabled={locked}
                onClick={() =>
                  setSelected(
                    selected.length === scan.items.length
                      ? []
                      : scan.items.map((item) => item.id),
                  )
                }
              >
                {selected.length === scan.items.length ? "取消全选" : "全选"}
              </Button>
            </div>
            <ul className="telegram-list telegram-preview">
              {scan.items.map((item, index) => (
                <li key={item.id} className="telegram-preview-row">
                  <input
                    type="checkbox"
                    aria-label={`选择第 ${index + 1} 项`}
                    checked={selected.includes(item.id)}
                    disabled={locked}
                    onChange={(event) =>
                      setSelected((previous) =>
                        event.target.checked
                          ? [...previous, item.id]
                          : previous.filter((id) => id !== item.id),
                      )
                    }
                  />
                  {item.kind === "video" ? (
                    <Film size={18} aria-hidden="true" />
                  ) : (
                    <Image size={18} aria-hidden="true" />
                  )}
                  <div>
                    <PrivateText
                      text={
                        item.title || (item.kind === "video" ? "视频" : "图片")
                      }
                      compact
                    />
                    {item.peer_title && (
                      <PrivateText text={item.peer_title} compact />
                    )}
                  </div>
                  <span className="telegram-status">{bytes(item.size)}</span>
                </li>
              ))}
            </ul>
            <div className="telegram-actions telegram-fields">
              <label>
                目标媒体库
                <select
                  disabled={locked || !onlineLibraries.length}
                  value={target?.id || ""}
                  onChange={(event) => setLibrary(event.target.value)}
                >
                  {!onlineLibraries.length && (
                    <option value="">没有在线媒体库</option>
                  )}
                  {onlineLibraries.map((item) => (
                    <option key={item.id} value={item.id}>
                      {item.name}
                    </option>
                  ))}
                </select>
              </label>
              <Button
                type="button"
                disabled={locked || !target || !selected.length}
                onClick={enqueue}
              >
                <Download size={16} />
                下载所选 {selected.length} 项
              </Button>
            </div>
          </section>
        )}

      <section
        className="settings-card telegram-panel"
        aria-labelledby="telegram-queue"
      >
        <div className="telegram-heading">
          <h2 id="telegram-queue">
            <Download size={18} />
            下载队列
          </h2>
        </div>
        {jobsError && (
          <p className="telegram-error" role="alert">
            {jobsError}
          </p>
        )}
        {!jobsError &&
          (!jobs ? (
            <p>正在读取下载队列…</p>
          ) : (
            !jobs.length && <p>暂无 Telegram 下载任务。</p>
          ))}
        {!!jobs?.length && (
          <ul className="telegram-list">
            {jobs.map((job) => {
              const state = jobStates[job.state];
              return (
                <li key={job.id} className="telegram-job">
                  <div className="telegram-heading">
                    <PrivateText
                      text={
                        job.title || (job.kind === "video" ? "视频" : "图片")
                      }
                      compact
                    />
                    <span className="telegram-status">
                      {state?.label || job.state}
                    </span>
                  </div>
                  {(job.total > 0 || job.state === "downloading") && (
                    <progress
                      aria-label="下载进度"
                      max={job.total > 0 ? job.total : undefined}
                      value={
                        job.total > 0
                          ? Math.min(job.received, job.total)
                          : undefined
                      }
                    />
                  )}
                  <div className="telegram-heading">
                    <span className="telegram-status">
                      {bytes(job.received)} /{" "}
                      {job.total > 0 ? bytes(job.total) : "—"}
                      {job.state === "downloading" && job.speed > 0
                        ? ` · ${bytes(job.speed)}/s`
                        : ""}
                    </span>
                    <div className="telegram-actions">
                      {state?.controls.map((action) => (
                        <Button
                          key={action}
                          size="sm"
                          variant="outline"
                          type="button"
                          disabled={
                            locked ||
                            (!account?.authorized &&
                              (action === "resume" || action === "retry"))
                          }
                          onClick={() => control(action, job.id)}
                        >
                          {jobActionLabels[action]}
                        </Button>
                      ))}
                    </div>
                  </div>
                  {job.error && (
                    <div className="telegram-error">
                      <PrivateText text={job.error} compact />
                    </div>
                  )}
                </li>
              );
            })}
          </ul>
        )}
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
