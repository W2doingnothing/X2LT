/* Blocking, CSP-safe appearance bootstrap. No account or media data is cached. */
(() => {
  const root = document.documentElement;
  const params = new URLSearchParams(location.search);
  const nativeMode =
    params.get("desktop") === "1" &&
    ["light", "dark"].includes(params.get("appearance"))
      ? params.get("appearance")
      : null;
  const system =
    nativeMode ||
    (matchMedia("(prefers-color-scheme: dark)").matches ? "dark" : "light");
  root.style.setProperty("--bg", system === "dark" ? "#07120e" : "#effaf5");
  root.style.setProperty("--text", system === "dark" ? "#eaf2ef" : "#1a201d");
  root.style.setProperty("background-color", "var(--bg)");
  root.style.setProperty("color", "var(--text)");
  root.dataset.theme = system;
  root.style.setProperty("color-scheme", system);
  // Native settings are authoritative, even on a new private WebView origin.
  if (nativeMode) return;
  try {
    const saved = JSON.parse(localStorage.getItem("x2lt-theme") || "null");
    const validHex = (value) =>
      typeof value === "string" && /^#[0-9a-f]{6}$/i.test(value);
    if (
      saved?.version !== 1 ||
      !validHex(saved.accent) ||
      !["light", "dark"].includes(saved.theme)
    )
      return;
    root.dataset.theme = saved.theme;
    root.style.setProperty("color-scheme", saved.theme);
    const allowed = [
      "bg",
      "bg-subtle",
      "surface",
      "surface-2",
      "border",
      "border-strong",
      "text",
      "text-muted",
      "text-subtle",
      "accent",
      "accent-hover",
      "accent-active",
      "accent-text",
      "accent-subtle",
      "on-accent",
      "ring",
      "success",
      "success-text",
      "success-subtle",
      "warning",
      "warning-text",
      "warning-subtle",
      "danger",
      "danger-text",
      "danger-subtle",
      "info",
      "info-text",
      "info-subtle",
      "media-bg",
      "media-text",
    ];
    for (const name of allowed)
      if (validHex(saved.tokens?.[name]))
        root.style.setProperty(`--${name}`, saved.tokens[name]);
  } catch {
    /* Invalid or inaccessible storage falls back to the system theme. */
  }
})();
