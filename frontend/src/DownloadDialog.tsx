import { useEffect, useState, type ReactNode } from "react";
import { Button } from "./components/ui/button";
import { Modal } from "./components/ui/dialog";
import { bytes, type Action } from "./Preferences";
import type { Library } from "./types";

export type DownloadMode = "likes" | "link" | null;
export function DownloadDialog({
  mode,
  close,
  library,
  picker,
  current,
  action,
  busy,
  transition,
  hasCredentials,
  username,
  configure,
  downloaded,
}: {
  mode: DownloadMode;
  close: () => void;
  library: string;
  picker: ReactNode;
  current: Library | undefined;
  action: Action;
  busy: boolean;
  transition: boolean;
  hasCredentials: boolean;
  username: string;
  configure: () => void;
  downloaded: () => void;
}) {
  const [url, setUrl] = useState(""),
    [range, setRange] = useState("latest");
  useEffect(() => {
    setUrl("");
    setRange("latest");
  }, [mode]);
  const ready = !!library && !!current?.online && !busy && !transition;
  const account = hasCredentials && !!username;
  return (
    <Modal
      open={mode !== null}
      onClose={close}
      title={mode === "likes" ? "同步点赞" : "链接下载"}
    >
      <form
        onSubmit={async (e) => {
          e.preventDefault();
          if (!ready || (mode === "likes" ? !account : !url.trim())) return;
          const payload =
            mode === "likes"
              ? { mode: range, library_id: library }
              : { mode: "link", url: url.trim(), library_id: library };
          if (
            await action(
              "start",
              payload,
              mode === "likes" ? "开始同步点赞" : "下载任务已开始",
            )
          ) {
            close();
            downloaded();
          }
        }}
      >
        {mode === "likes" ? (
          <>
            <p>
              {username
                ? `账号 @${username.replace(/^@/, "")}`
                : "请先配置 X 账号"}
            </p>
            {!account && (
              <Button type="button" variant="outline" onClick={configure}>
                设置账号与凭证
              </Button>
            )}
            <label htmlFor="likes-range">同步范围</label>
            <select
              id="likes-range"
              value={range}
              onChange={(e) => setRange(e.target.value)}
            >
              <option value="latest">最近 50 条点赞</option>
              <option value="all">全部点赞</option>
            </select>
          </>
        ) : (
          <>
            <label htmlFor="tweet-url">推文链接</label>
            <input
              id="tweet-url"
              autoFocus
              placeholder="https://x.com/username/status/…"
              value={url}
              onChange={(e) => setUrl(e.target.value)}
            />
          </>
        )}
        <label>目标媒体库</label>
        {picker}
        <div className="info-note">
          <span>
            {current?.path || "请先在设置中添加媒体库"}
            <br />
            {current?.online
              ? `当前可用 ${bytes(current.available_bytes)}`
              : "媒体库离线"}
          </span>
        </div>
        <div className="modal-footer">
          <Button type="button" variant="outline" onClick={close}>
            取消
          </Button>
          <Button
            type="submit"
            disabled={!ready || (mode === "likes" ? !account : !url.trim())}
          >
            {mode === "likes" ? "开始同步" : "开始下载"}
          </Button>
        </div>
      </form>
    </Modal>
  );
}
