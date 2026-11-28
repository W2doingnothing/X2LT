import { useEffect, useState } from "react";
import { Button } from "./components/ui/button";
import { call, desktop } from "./api";
import type { Library, Payload, Settings, TelegramScanRule } from "./types";

type Channel = { id: string; title: string };
type ScanResult = { state: string; error?: string; added?: number };
const noRules: TelegramScanRule[] = [];

function ruleKey(rule: TelegramScanRule) {
  return `${rule.mode}:${rule.source}:${rule.library_id}`;
}

function TelegramRules({ settings, libraries, save, pending }: {
  settings: Settings;
  libraries: Library[];
  save: (patch: Payload) => Promise<boolean>;
  pending: boolean;
}) {
  const rules = settings.telegram_scan_rules ?? noRules;
  const [draft, setDraft] = useState<TelegramScanRule | null>(null);
  const [editing, setEditing] = useState<string | null>(null);
  const [channels, setChannels] = useState<Channel[] | null>(null);
  const [channelError, setChannelError] = useState("");
  const [loading, setLoading] = useState(false);
  const [results, setResults] = useState<Record<string, ScanResult>>({});
  const [ruleError, setRuleError] = useState("");

  useEffect(() => {
    if (!desktop || !rules.length) return;
    let closed = false;
    let timer: number;
    async function poll() {
      const latest = await call<Record<string, ScanResult>>("tg_automation_status").catch(() => null);
      if (closed) return;
      if (latest) setResults(latest);
      timer = window.setTimeout(poll, 5000);
    }
    void poll();
    return () => {
      closed = true;
      window.clearTimeout(timer);
    };
  }, [rules.length]);

  async function loadChannels() {
    setLoading(true);
    setChannelError("");
    try {
      const account = await call<{ authorized: boolean }>("tg_status");
      if (!account.authorized) throw new Error("请先在电报账号中登录");
      setChannels(await call<Channel[]>("tg_channels"));
    } catch (error) {
      setChannelError(error instanceof Error ? error.message : "无法载入频道");
    } finally {
      setLoading(false);
    }
  }

  function changeSource(mode: TelegramScanRule["mode"]) {
    setDraft((rule) => rule && { ...rule, mode, source: "", title: "" });
    if (mode === "channel" && channels === null && !loading) void loadChannels();
  }

  async function persist(rule: TelegramScanRule) {
    const next = rules.filter((item) => ruleKey(item) !== editing);
    if (next.some((item) => ruleKey(item) === ruleKey(rule))) {
      setRuleError("该来源在此媒体库已有自动扫描规则。");
      return;
    }
    if (await save({ telegram_scan_rules: [...next, rule] })) setDraft(null);
  }

  return (
    <section className="settings-card">
      <h2>Telegram 自动扫描</h2>
      {rules.map((rule) => {
        const key = ruleKey(rule);
        const result = results[key];
        return (
          <div className="telegram-actions" key={key}>
            <span>{rule.mode === "saved" ? "我的收藏" : rule.title || rule.source} · {libraries.find((item) => item.id === rule.library_id)?.name ?? "媒体库已移除"}</span>
            <span>{[rule.on_start && "启动时", rule.in_window && `时段内每 ${rule.interval} 分钟`].filter(Boolean).join(" · ") || "已停用"}</span>
            {result && <span role="status">{result.state === "scanning" ? "正在扫描"
              : result.state === "ready" ? `上次提交 ${result.added ?? 0} 项`
              : result.error || "扫描暂未完成"}</span>}
            <Button type="button" variant="outline" disabled={pending} onClick={() => {
              setEditing(key);
              setDraft(rule);
              setRuleError("");
              if (rule.mode === "channel" && channels === null && !loading) void loadChannels();
            }}>编辑</Button>
            <Button type="button" variant="outline" disabled={pending} onClick={async () => {
              if (await save({ telegram_scan_rules: rules.filter((item) => ruleKey(item) !== key) }) && editing === key) setDraft(null);
            }}>移除</Button>
          </div>
        );
      })}
      {draft ? (
        <form className="account-form" onSubmit={(event) => {
          event.preventDefault();
          void persist(draft);
        }}>
          <div className="account-fields">
            <label>扫描来源
              <select aria-label="扫描来源" value={draft.mode} onChange={(event) => changeSource(event.currentTarget.value as TelegramScanRule["mode"])}>
                <option value="saved">我的收藏</option>
                <option value="channel">频道</option>
              </select>
            </label>
            {draft.mode === "channel" && <label>频道
              <select aria-label="频道" required value={draft.source} onChange={(event) => {
                const channel = channels?.find((item) => item.id === event.currentTarget.value);
                setDraft({ ...draft, source: channel?.id ?? "", title: channel?.title ?? "" });
              }}>
                <option value="">{loading ? "正在载入频道…" : "请选择频道"}</option>
                {draft.source && !channels?.some((item) => item.id === draft.source)
                  && <option value={draft.source}>{draft.title || draft.source}</option>}
                {channels?.map((channel) => <option key={channel.id} value={channel.id}>{channel.title}</option>)}
              </select>
            </label>}
            <label>目标媒体库
              <select aria-label="目标媒体库" required value={draft.library_id} onChange={(event) => setDraft({ ...draft, library_id: event.currentTarget.value })}>
                <option value="">请选择媒体库</option>
                {libraries.map((library) => <option key={library.id} value={library.id}>{library.name}</option>)}
              </select>
            </label>
            <label>媒体类型
              <select aria-label="媒体类型" value={draft.media_mode} onChange={(event) => setDraft({ ...draft, media_mode: event.currentTarget.value as TelegramScanRule["media_mode"] })}>
                <option value="all">图片和视频</option><option value="video">仅视频</option><option value="image">仅图片</option>
              </select>
            </label>
          </div>
          {draft.mode === "channel" && channelError && <div className="telegram-actions">
            <span role="alert">{channelError}</span>
            <Button type="button" variant="outline" disabled={loading} onClick={() => void loadChannels()}>重新载入频道</Button>
          </div>}
          <label className="privacy-setting">
            <input type="checkbox" checked={draft.on_start} onChange={(event) => setDraft({ ...draft, on_start: event.currentTarget.checked })} />
            软件启动时扫描一次
          </label>
          <label className="privacy-setting">
            <input type="checkbox" checked={draft.in_window} onChange={(event) => setDraft({ ...draft, in_window: event.currentTarget.checked })} />
            在下载时段内定时扫描
          </label>
          {draft.in_window && <label>间隔（分钟）
            <input type="number" min="5" max="720" required value={draft.interval}
              onChange={(event) => setDraft({ ...draft, interval: Number(event.currentTarget.value) })} />
          </label>}
          {draft.in_window && !settings.schedule_enabled && <p>开启定时下载后，时段内扫描才会触发。</p>}
          {ruleError && <p role="alert">{ruleError}</p>}
          <div className="telegram-actions">
            <Button type="submit" disabled={pending}>保存规则</Button>
            <Button type="button" variant="outline" onClick={() => setDraft(null)}>取消</Button>
          </div>
        </form>
      ) : (
        <Button type="button" variant="outline" disabled={pending || !libraries.length || rules.length >= 64} onClick={() => {
          setEditing(null);
          setRuleError("");
          setDraft({ mode: "saved", source: "", title: "", library_id: (libraries.find((item) => item.default) ?? libraries[0]).id,
            media_mode: "all", on_start: false, in_window: true, interval: 60 });
        }}>添加扫描规则</Button>
      )}
    </section>
  );
}

export function AutomationSettings({ settings, libraries, save, pending }: {
  settings: Settings;
  libraries: Library[];
  save: (patch: Payload) => Promise<boolean>;
  pending: boolean;
}) {
  const [start, setStart] = useState(settings.schedule_start ?? "00:00");
  const [end, setEnd] = useState(settings.schedule_end ?? "05:00");
  const [enabled, setEnabled] = useState(settings.schedule_enabled ?? false);
  useEffect(() => {
    setStart(settings.schedule_start ?? "00:00");
    setEnd(settings.schedule_end ?? "05:00");
    setEnabled(settings.schedule_enabled ?? false);
  }, [settings.schedule_start, settings.schedule_end, settings.schedule_enabled]);

  return (
    <>
      <section className="settings-card">
        <h2>定时下载</h2>
        <form className="account-form" onSubmit={(event) => {
          event.preventDefault();
          void save({ schedule_enabled: enabled, schedule_start: start, schedule_end: end });
        }}>
          <label className="privacy-setting">
            <input type="checkbox" checked={enabled} onChange={(event) => setEnabled(event.currentTarget.checked)} />
            按每日时段继续和暂停下载
          </label>
          <div className="account-fields">
            <label>开始时间<input type="time" required value={start} onChange={(event) => setStart(event.currentTarget.value)} /></label>
            <label>暂停时间<input type="time" required value={end} onChange={(event) => setEnd(event.currentTarget.value)} /></label>
          </div>
          {start === end && <p role="alert">开始和暂停时间须不同。</p>}
          {start > end && <p>下载时段跨到次日。</p>}
          <Button type="submit" disabled={pending || start === end}>保存下载时段</Button>
        </form>
      </section>
      <section className="settings-card">
        <h2>后台运行</h2>
        {([
          ["close_to_tray", "关闭窗口时收到系统托盘"],
          ["start_with_windows", "登录 Windows 后自动启动"],
          ["startup_to_tray", "启动时收进系统托盘"],
        ] as const).map(([key, label]) => (
          <label key={key} className="privacy-setting">
            <input type="checkbox" checked={Boolean(settings[key])} disabled={pending}
              onChange={(event) => void save({ [key]: event.currentTarget.checked })} />
            {label}
          </label>
        ))}
      </section>
      <TelegramRules settings={settings} libraries={libraries} save={save} pending={pending} />
    </>
  );
}
