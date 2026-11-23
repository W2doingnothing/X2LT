import React from "react";
import { createRoot } from "react-dom/client";
import "./style.css";
import { applyTheme } from "./theme";
// The cache contains only appearance preferences, never account data.
try {
  const params = new URLSearchParams(location.search);
  const mode = params.get("appearance"),
    accent = params.get("accent");
  const nativeAppearance =
    params.get("desktop") === "1" &&
    (mode === "dark" || mode === "light") &&
    /^#[0-9a-f]{6}$/i.test(accent || "");
  if (nativeAppearance) {
    applyTheme(accent || "#397863", mode === "dark");
  } else {
    const saved = JSON.parse(localStorage.getItem("x2lt-theme") || "null");
    applyTheme(
      saved?.version === 1 ? saved.accent : "#397863",
      saved?.version === 1
        ? saved.theme === "dark"
        : matchMedia("(prefers-color-scheme: dark)").matches,
    );
  }
} catch {
  applyTheme("#397863", matchMedia("(prefers-color-scheme: dark)").matches);
}
// CSS keeps the page hidden until the synchronous cached theme is installed.
document.documentElement.dataset.themeReady = "true";
const params = new URLSearchParams(location.search);
const View = React.lazy(
  params.get("desktop") === "1" && params.get("view") === "random-feed"
    ? () => import("./RandomFeed")
    : () => import("./App"),
);
createRoot(document.getElementById("root")!).render(
  <React.StrictMode>
    <React.Suspense>
      <View />
    </React.Suspense>
  </React.StrictMode>,
);
