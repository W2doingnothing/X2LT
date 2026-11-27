import {
  createContext,
  isValidElement,
  useCallback,
  useContext,
  useEffect,
  useRef,
  useState,
  type ReactNode,
} from "react";
import { ChevronLeft, ChevronRight } from "lucide-react";
import { usePrivacyMode } from "./Privacy";
import { cycleIndex, wheelStep, type WheelState } from "./stackMotion";
import "./content-cards.css";

const StackPreviewContext = createContext<
  ((image?: HTMLImageElement) => void) | undefined
>(undefined);

export function useStackPreviewReady() {
  return useContext(StackPreviewContext);
}

function StackLayer({
  index,
  visible,
  switching,
  privateMode,
  notify,
  onReady,
  children,
}: {
  index: number;
  visible: boolean;
  switching: boolean;
  privateMode: boolean;
  notify: boolean;
  onReady: (index: number) => void;
  children: ReactNode;
}) {
  const [ready, setReady] = useState(false);
  const settle = useCallback((image?: HTMLImageElement) => {
    if (image?.decode)
      void image.decode().then(
        () => setReady(true),
        () => setReady(true),
      );
    else setReady(true);
  }, []);
  useEffect(() => {
    if (ready && notify) onReady(index);
  }, [index, notify, onReady, ready]);

  return (
    <div
      className={
        "stack-layer" +
        (visible ? "" : " stack-layer-staging") +
        (visible && switching && privateMode ? " stack-layer-masked" : "")
      }
      inert={!visible || switching}
      aria-hidden={!visible || switching || undefined}
    >
      <StackPreviewContext.Provider value={settle}>
        {children}
      </StackPreviewContext.Provider>
    </div>
  );
}

export function MediaStack({
  count,
  index,
  onChange,
  render,
  preloadAdjacent = false,
}: {
  count: number;
  index: number;
  onChange: (index: number) => void;
  render: (index: number, swipe: (delta: number) => void) => ReactNode;
  preloadAdjacent?: boolean;
}) {
  const front = useRef<HTMLDivElement>(null);
  const state = useRef<WheelState>({ sum: 0, seen: 0, fired: -Infinity });
  const privateMode = usePrivacyMode();
  const [request, setRequest] = useState<{
    from: number;
    count: number;
    index: number;
  }>();
  const [readyIndex, setReadyIndex] = useState<number>();
  const current = cycleIndex(index, 0, count);
  const pending =
    request?.from === current && request.count === count
      ? request.index
      : undefined;
  const adjacent =
    count > 1 && preloadAdjacent && !privateMode && readyIndex === current
      ? cycleIndex(current, 1, count)
      : undefined;
  const staging = pending ?? adjacent;
  const latest = useRef({ current, count, onChange, pending });
  latest.current = { current, count, onChange, pending };
  const shift = (delta: number) => {
    const v = latest.current;
    const next = cycleIndex(v.pending ?? v.current, delta, v.count);
    setRequest(
      next === v.current
        ? undefined
        : { from: v.current, count: v.count, index: next },
    );
  };
  const previewReady = useCallback((index: number) => {
    const v = latest.current;
    if (index === v.current) setReadyIndex(index);
    if (index === v.pending) {
      setRequest(undefined);
      setReadyIndex(index);
      v.onChange(index);
    }
  }, []);
  useEffect(() => {
    state.current = { sum: 0, seen: 0, fired: -Infinity };
    const element = front.current;
    if (!element || count < 2) return;
    const wheel = (event: WheelEvent) => {
      if (event.ctrlKey || event.metaKey || !event.cancelable) return;
      const delta =
        Math.abs(event.deltaX) > Math.abs(event.deltaY)
          ? event.deltaX
          : event.deltaY;
      if (!delta) return;
      event.preventDefault();
      event.stopPropagation();
      const result = wheelStep(
        delta,
        event.deltaMode,
        performance.now(),
        state.current,
      );
      state.current = result.state;
      if (result.delta) shift(result.delta);
    };
    element.addEventListener("wheel", wheel, { passive: false });
    return () => element.removeEventListener("wheel", wheel);
  }, [count]);
  if (!count) return null;
  return (
    <div
      className={"media-stack " + (count > 1 ? "multiple" : "single")}
      role="group"
      aria-label={`媒体预览 共 ${count} 项`}
      tabIndex={count > 1 ? 0 : undefined}
      onKeyDown={(e) => {
        if (
          count < 2 ||
          e.ctrlKey ||
          e.metaKey ||
          e.altKey ||
          !["ArrowLeft", "ArrowRight"].includes(e.key)
        )
          return;
        e.preventDefault();
        e.stopPropagation();
        shift(e.key === "ArrowLeft" ? -1 : 1);
      }}
    >
      <div className="stack-deck">
        {count > 2 && (
          <span className="stack-back stack-back-far" aria-hidden="true" />
        )}
        {count > 1 && (
          <span className="stack-back stack-back-near" aria-hidden="true" />
        )}
        <div className="stack-front" ref={front}>
          {count < 2
            ? render(current, shift)
            : (staging === undefined ? [current] : [current, staging]).map(
                (index) => {
                  const child = render(index, shift);
                  return (
                    <StackLayer
                      key={isValidElement(child) ? (child.key ?? index) : index}
                      index={index}
                      visible={index === current}
                      switching={index === current && pending !== undefined}
                      privateMode={privateMode}
                      notify={index === current || index === pending}
                      onReady={previewReady}
                    >
                      {child}
                    </StackLayer>
                  );
                },
              )}
        </div>
      </div>
      {count > 1 && (
        <div className="stack-controls">
          <button
            aria-label="上一个预览"
            title="上一个预览"
            onClick={() => shift(-1)}
          >
            <ChevronLeft size={16} />
          </button>
          <span aria-live="polite">
            {current + 1} / {count}
          </span>
          <button
            aria-label="下一个预览"
            title="下一个预览"
            onClick={() => shift(1)}
          >
            <ChevronRight size={16} />
          </button>
        </div>
      )}
    </div>
  );
}
