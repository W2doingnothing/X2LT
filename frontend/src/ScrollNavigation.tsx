import { useEffect, useState } from "react";
import { ChevronUp, ChevronDown } from "lucide-react";

export function ScrollNavigation() {
  const [bounds, setBounds] = useState({
    visible: false,
    top: true,
    bottom: true,
  });
  useEffect(() => {
    let frame = 0;
    const measure = () => {
      frame = 0;
      const max = Math.max(
        0,
        document.documentElement.scrollHeight - innerHeight,
      );
      const next = {
        visible: max > 2,
        top: scrollY <= 2,
        bottom: scrollY >= max - 2,
      };
      setBounds((old) =>
        old.visible === next.visible &&
        old.top === next.top &&
        old.bottom === next.bottom
          ? old
          : next,
      );
    };
    const schedule = () => {
      if (!frame) frame = requestAnimationFrame(measure);
    };
    const observer = new ResizeObserver(schedule);
    observer.observe(document.body);
    observer.observe(document.documentElement);
    window.addEventListener("scroll", schedule, { passive: true });
    window.addEventListener("resize", schedule);
    measure();
    return () => {
      observer.disconnect();
      window.removeEventListener("scroll", schedule);
      window.removeEventListener("resize", schedule);
      cancelAnimationFrame(frame);
    };
  }, []);
  const jump = (bottom: boolean) =>
    window.scrollTo({
      top: bottom ? document.documentElement.scrollHeight : 0,
      behavior: matchMedia("(prefers-reduced-motion: reduce)").matches
        ? "instant"
        : "smooth",
    });
  if (!bounds.visible) return null;
  return (
    <nav className="scroll-navigation" aria-label="页面滚动">
      <button
        type="button"
        title="回到顶部"
        aria-label="回到顶部"
        disabled={bounds.top}
        onClick={() => jump(false)}
      >
        <ChevronUp size={19} />
      </button>
      <button
        type="button"
        title="前往底部"
        aria-label="前往底部"
        disabled={bounds.bottom}
        onClick={() => jump(true)}
      >
        <ChevronDown size={19} />
      </button>
    </nav>
  );
}
