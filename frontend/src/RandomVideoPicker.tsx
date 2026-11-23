import { useState } from "react";
import { Play } from "lucide-react";
import { call } from "./api";
import { Button } from "./components/ui/button";
import { Modal } from "./components/ui/dialog";
import { eligibleRandomVideoIds, initialRandomVideoIds } from "./randomVideoLibraries";
import type { Library } from "./types";
import "./random-video-picker.css";

export function RandomVideoPicker({
  libraries,
  defaultLibrary,
  remembered,
  onClose,
  onOpened,
}: {
  libraries: Library[];
  defaultLibrary: string | null;
  remembered?: string[];
  onClose: () => void;
  onOpened: (ids: string[]) => void;
}) {
  const [selection, setSelection] = useState<string[] | null>(null);
  const [launching, setLaunching] = useState(false);
  const [error, setError] = useState("");
  const selected = selection
    ? eligibleRandomVideoIds(libraries, selection)
    : initialRandomVideoIds(libraries, defaultLibrary, remembered);

  async function launch() {
    setLaunching(true);
    setError("");
    try {
      const result = await call<{ opened: boolean }>("random_feed_launch", { library_ids: selected });
      if (result.opened) onOpened(selected);
      onClose();
    } catch (e) {
      setError(e instanceof Error ? e.message : "无法打开随机视频播放器");
      setLaunching(false);
    }
  }

  return (
    <Modal open title="随机刷视频" onClose={() => { if (!launching) onClose(); }}>
      <form onSubmit={(e) => {
        e.preventDefault();
        if (!launching && selected.length) void launch();
      }}>
        {libraries.length > 0 ? (
          <fieldset className="random-video-libraries" disabled={launching}>
            <legend>选择一个或多个媒体库</legend>
            {libraries.map((library) => (
              <label key={library.id}>
                <input
                  type="checkbox"
                  checked={selected.includes(library.id)}
                  disabled={!library.online}
                  onChange={(e) => {
                    setError("");
                    setSelection(e.target.checked
                      ? [...selected, library.id]
                      : selected.filter((id) => id !== library.id));
                  }}
                />
                <span>{library.name}</span>
                {!library.online && <small>{library.capacity_pending ? "正在检查" : "离线"}</small>}
              </label>
            ))}
          </fieldset>
        ) : (
          <p role="status">请先添加媒体库。</p>
        )}
        {error && <p className="random-video-launch-error" role="alert">{error}</p>}
        <div className="modal-footer">
          <Button type="button" variant="outline" disabled={launching} onClick={onClose}>取消</Button>
          <Button type="submit" disabled={launching || !selected.length}>
            <Play size={16} aria-hidden="true" />
            {launching ? "正在打开…" : "开始播放"}
          </Button>
        </div>
      </form>
    </Modal>
  );
}
