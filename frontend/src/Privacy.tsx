import {
  createContext,
  useContext,
  useRef,
  useState,
  type ReactNode,
} from "react";
import "./privacy.css";
const PrivacyContext = createContext({ enabled: false, epoch: 0 });
export function PrivacyProvider({
  enabled,
  scopeKey,
  children,
}: {
  enabled: boolean;
  scopeKey: string;
  children: ReactNode;
}) {
  const boundary = useRef({ enabled, scopeKey, epoch: 0 });
  if (
    boundary.current.enabled !== enabled ||
    boundary.current.scopeKey !== scopeKey
  )
    boundary.current = { enabled, scopeKey, epoch: boundary.current.epoch + 1 };
  return (
    <PrivacyContext.Provider value={{ enabled, epoch: boundary.current.epoch }}>
      {children}
    </PrivacyContext.Provider>
  );
}
export function usePrivateReveal(resource = "") {
  const { enabled, epoch } = useContext(PrivacyContext);
  const [revealed, setRevealed] = useState<{
    epoch: number;
    resource: string;
  } | null>(null);
  return {
    hidden:
      enabled && (revealed?.epoch !== epoch || revealed?.resource !== resource),
    reveal: () => setRevealed({ epoch, resource }),
  };
}
export function ProtectedImage({
  src,
  alt,
  className = "",
  onError,
}: {
  src: string;
  alt: string;
  className?: string;
  onError?: () => void;
}) {
  const { hidden, reveal } = usePrivateReveal(src);
  return (
    <span
      className="protected-image"
      role={hidden ? "button" : undefined}
      tabIndex={hidden ? 0 : undefined}
      aria-label={hidden ? "显示封面" : undefined}
      onClick={(e) => {
        if (hidden) {
          e.preventDefault();
          e.stopPropagation();
          reveal();
        }
      }}
      onKeyDown={(e) => {
        if (hidden && (e.key === "Enter" || e.key === " ")) {
          e.preventDefault();
          e.stopPropagation();
          reveal();
        }
      }}
    >
      <img
        src={src}
        alt={hidden ? "已模糊的封面" : alt}
        className={className + (hidden ? " privacy-blurred" : "")}
        onError={onError}
      />
      {hidden && <span className="privacy-hint">点击显示</span>}
    </span>
  );
}
export function PrivateText({
  text,
  onOpen,
  compact = false,
}: {
  text: string;
  onOpen?: () => void;
  compact?: boolean;
}) {
  const { hidden, reveal } = usePrivateReveal();
  if (hidden)
    return (
      <button
        className="privacy-text-toggle"
        onClick={(e) => {
          e.stopPropagation();
          reveal();
        }}
      >
        推文内容已折叠 · 点击展开
      </button>
    );
  return onOpen ? (
    <button onClick={onOpen}>{text}</button>
  ) : (
    <p className={compact ? "" : "tweet-text"}>{text}</p>
  );
}
export function PrivacyPanel({
  enabled,
  save,
}: {
  enabled: boolean;
  save: (p: Record<string, unknown>) => Promise<boolean>;
}) {
  return (
    <section className="settings-card">
      <h2>隐私模式</h2>
      <p>
        模糊媒体预览和封面图片，折叠推文标题与正文。点击后仅显示当前内容，离开栏目后重新遮蔽。
      </p>
      <label className="privacy-setting">
        <input
          type="checkbox"
          checked={enabled}
          onChange={(e) => void save({ privacy_mode: e.target.checked })}
        />
        开启隐私模式
      </label>
      <p>开关会保存；临时展开状态不会长期保存。打开本地文件会调用系统应用。</p>
    </section>
  );
}
