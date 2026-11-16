import { useEffect, useRef } from "react";
export function useBackNavigation(back: () => void) {
  const latest = useRef(back);
  latest.current = back;
  useEffect(() => {
    function key(event: KeyboardEvent) {
      if (
        event.key !== "Backspace" ||
        event.defaultPrevented ||
        event.isComposing ||
        event.ctrlKey ||
        event.altKey ||
        event.metaKey ||
        event.shiftKey
      )
        return;
      const element = event.target as HTMLElement | null;
      if (
        element?.closest(
          'input,textarea,select,[contenteditable]:not([contenteditable="false"]),[role="textbox"]',
        )
      )
        return;
      event.preventDefault();
      // Radix portals are stacked in DOM order. Never navigate behind an open layer.
      const dialogs = document.querySelectorAll(
        '[role="dialog"][data-state="open"]',
      );
      const top = dialogs.item(dialogs.length - 1);
      if (top) {
        (
          top.querySelector(
            '[aria-label="关闭弹窗"]',
          ) as HTMLButtonElement | null
        )?.click();
        return;
      }
      latest.current();
    }
    window.addEventListener("keydown", key);
    return () => window.removeEventListener("keydown", key);
  }, []);
}
