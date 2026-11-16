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
  collapsible = false,
}: {
  text: string;
  onOpen?: () => void;
  compact?: boolean;
  collapsible?: boolean;
}) {
  const { hidden, reveal } = usePrivateReveal();
  const [expanded, setExpanded] = useState(false);
  if (hidden)
    return (
      <button
        className="privacy-text-toggle"
        onClick={(e) => {
          e.stopPropagation();
          reveal();
        }}
      >
        内容已折叠 · 点击展开
      </button>
    );
  if (collapsible)
    return (
      <div className="tweet-body">
        <p className={expanded ? "expanded" : "clamped"}>{text}</p>
        {text && (
          <button
            className="body-toggle"
            aria-expanded={expanded}
            onClick={() => setExpanded((value) => !value)}
          >
            {expanded ? "收起正文" : "展开正文"}
          </button>
        )}
      </div>
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
      <label className="privacy-setting">
        <input
          type="checkbox"
          checked={enabled}
          onChange={(e) => void save({ privacy_mode: e.target.checked })}
        />
        开启隐私模式
      </label>
    </section>
  );
}
