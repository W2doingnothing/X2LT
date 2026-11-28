import { useEffect, useState } from "react";
import { Button } from "./components/ui/button";
import type { Payload, Settings } from "./types";

export function AutomationSettings({ settings, save, pending }: {
  settings: Settings;
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
    </>
  );
}
