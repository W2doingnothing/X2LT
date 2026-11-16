import { useEffect, useRef, type ReactNode } from "react";
import { ChevronLeft, ChevronRight } from "lucide-react";
import { cycleIndex, wheelStep, type WheelState } from "./stackMotion";
import "./content-cards.css";
export function MediaStack({
  count,
  index,
  onChange,
  render,
}: {
  count: number;
  index: number;
  onChange: (index: number) => void;
  render: (index: number, swipe: (delta: number) => void) => ReactNode;
}) {
  const front = useRef<HTMLDivElement>(null);
  const state = useRef<WheelState>({ sum: 0, seen: 0, fired: -Infinity });
  const current = cycleIndex(index, 0, count);
  const latest = useRef({ current, count, onChange });
  latest.current = { current, count, onChange };
  const shift = (delta: number) => {
    const v = latest.current;
    v.onChange(cycleIndex(v.current, delta, v.count));
  };
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
          {render(current, shift)}
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
