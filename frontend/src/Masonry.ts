import { useLayoutEffect, type RefObject } from "react";

/** Quantized grid rows preserve DOM order, keyboard order and existing card nodes. */
export function useMasonry(
  ref: RefObject<HTMLDivElement | null>,
  enabled: boolean,
  identity: string,
  cardSelector = ".tweet-card",
) {
  useLayoutEffect(() => {
    const root = ref.current;
    if (!root || !enabled) return;
    const cards = [
      ...root.querySelectorAll<HTMLElement>(`:scope > ${cardSelector}`),
    ];
    if (!cards.length) return;
    let frame = 0,
      disposed = false;
    root.classList.add("masonry-ready");
    const layout = (preserve: boolean) => {
      if (disposed) return;
      const anchor =
        preserve && window.scrollY > 0
          ? cards.find((c) => {
              const r = c.getBoundingClientRect();
              return r.bottom > 80 && r.top < window.innerHeight;
            })
          : null;
      const before = anchor?.getBoundingClientRect().top;
      const spans = cards.map((c) =>
        Math.ceil((c.getBoundingClientRect().height + 20) / 8),
      );
      cards.forEach((c, i) => {
        const value = `span ${spans[i]}`;
        if (c.style.gridRowEnd !== value) c.style.gridRowEnd = value;
      });
      if (anchor && before !== undefined) {
        const delta = anchor.getBoundingClientRect().top - before;
        if (Math.abs(delta) > 1) window.scrollBy(0, delta);
      }
    };
    layout(false);
    const observer = new ResizeObserver(() => {
      cancelAnimationFrame(frame);
      frame = requestAnimationFrame(() => layout(true));
    });
    observer.observe(root);
    cards.forEach((c) => observer.observe(c));
    return () => {
      disposed = true;
      observer.disconnect();
      cancelAnimationFrame(frame);
      root.classList.remove("masonry-ready");
      cards.forEach((c) => c.style.removeProperty("grid-row-end"));
    };
  }, [ref, enabled, identity, cardSelector]);
}
