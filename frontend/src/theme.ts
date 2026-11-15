const rgb = (hex: string) =>
  [1, 3, 5].map((i) => parseInt(hex.slice(i, i + 2), 16));
const clamp = (n: number, lo = 0, hi = 1) => Math.min(hi, Math.max(lo, n));
const linear = (v: number) =>
  (v /= 255) <= 0.04045 ? v / 12.92 : ((v + 0.055) / 1.055) ** 2.4;
const hex = (values: number[]) =>
  "#" +
  values
    .map((v) =>
      Math.round(clamp(v, 0, 255))
        .toString(16)
        .padStart(2, "0"),
    )
    .join("");
function luminance(color: string) {
  const [r, g, b] = rgb(color).map(linear);
  return 0.2126 * r + 0.7152 * g + 0.0722 * b;
}
export function contrast(a: string, b: string) {
  const x = luminance(a),
    y = luminance(b);
  return (Math.max(x, y) + 0.05) / (Math.min(x, y) + 0.05);
}
function toOklch(color: string) {
  const [r, g, b] = rgb(color).map(linear);
  const l = Math.cbrt(0.4122214708 * r + 0.5363325363 * g + 0.0514459929 * b);
  const m = Math.cbrt(0.2119034982 * r + 0.6806995451 * g + 0.1073969566 * b);
  const s = Math.cbrt(0.0883024619 * r + 0.2817188376 * g + 0.6299787005 * b);
  const a = 1.9779984951 * l - 2.428592205 * m + 0.4505937099 * s;
  const bb = 0.0259040371 * l + 0.7827717662 * m - 0.808675766 * s;
  const c = Math.hypot(a, bb);
  // Achromatic input has no stable hue; use neutral red rather than floating point noise.
  return {
    c,
    h: c < 0.000001 ? 0 : ((Math.atan2(bb, a) * 180) / Math.PI + 360) % 360,
  };
}
function oklch(lightness: number, chroma: number, hue: number) {
  const L = clamp(lightness),
    angle = (hue * Math.PI) / 180;
  const channels = (c: number) => {
    const a = c * Math.cos(angle),
      b = c * Math.sin(angle);
    const l = (L + 0.3963377774 * a + 0.2158037573 * b) ** 3;
    const m = (L - 0.1055613458 * a - 0.0638541728 * b) ** 3;
    const s = (L - 0.0894841775 * a - 1.291485548 * b) ** 3;
    return [
      4.0767416621 * l - 3.3077115913 * m + 0.2309699292 * s,
      -1.2684380046 * l + 2.6097574011 * m - 0.3413193965 * s,
      -0.0041960863 * l - 0.7034186147 * m + 1.707614701 * s,
    ];
  };
  const inGamut = (v: number[]) => v.every((x) => x >= -0.0005 && x <= 1.0005);
  let values = channels(chroma);
  if (!inGamut(values)) {
    let low = 0,
      high = chroma;
    for (let i = 0; i < 30; i++) {
      const middle = (low + high) / 2;
      if (inGamut(channels(middle))) low = middle;
      else high = middle;
    }
    values = channels(low);
  }
  return hex(
    values.map(
      (v) =>
        255 *
        (v <= 0.0031308
          ? clamp(v) * 12.92
          : 1.055 * clamp(v) ** (1 / 2.4) - 0.055),
    ),
  );
}

export function themeColors(color: string, dark: boolean) {
  if (!/^#[0-9a-f]{6}$/i.test(color))
    throw Error("请输入六位十六进制颜色码，例如 #397863");
  const source = color.toLowerCase(),
    { c: sourceC, h } = toOklch(source);
  const c = Math.max(0.018, sourceC),
    bgL = dark ? 0.17 : 0.975,
    bgC = dark ? 0.018 : 0.012;
  const tokens: Record<string, string> = {};
  const put = (name: string, l: number, cc: number, hue = h) =>
    (tokens[name] = oklch(l, cc, hue));
  put("bg", bgL, bgC);
  put("bg-subtle", bgL - (dark ? 0.015 : 0.0225), bgC * 1.15);
  put("surface", dark ? bgL + 0.03 : 0.995, bgC * 0.75);
  put("surface-2", dark ? bgL + 0.06 : 0.999, bgC * 0.5);
  put(
    "accent-subtle",
    dark ? 0.255 : 0.94,
    Math.min(dark ? 0.05 : 0.032, c * (dark ? 0.3 : 0.16)),
  );
  put("border", bgL + (dark ? 1 : -1) * (0.045 + 0.09 * 0.55), 0.011);
  put("border-strong", bgL + (dark ? 1 : -1) * (0.12 + 0.13 * 0.55), 0.013);
  put("text-subtle", dark ? 0.56 : 0.63, 0.01);
  const backgrounds = [
    "bg",
    "bg-subtle",
    "surface",
    "surface-2",
    "accent-subtle",
  ].map((name) => tokens[name]);
  // Always test the final 8-bit sRGB output, including each actual badge background.
  const readable = (l: number, cc: number, hue = h, surfaces = backgrounds) => {
    let result = oklch(l, cc, hue);
    for (
      let i = 0;
      i < 200 && surfaces.some((bg) => contrast(result, bg) < 4.5);
      i++
    ) {
      l = clamp(l + (dark ? 0.005 : -0.005));
      result = oklch(l, cc, hue);
    }
    return result;
  };
  tokens.text = readable(dark ? 0.955 : 0.235, 0.01);
  tokens["text-muted"] = readable(dark ? 0.72 : 0.47, 0.011);
  tokens["accent-text"] = readable(dark ? 0.78 : 0.52, c);
  const white = oklch(0.99, Math.min(0.008, c * 0.08), h);
  const black = oklch(0.19, Math.min(0.02, c * 0.15), h);
  let accentL = dark ? 0.74 : 0.545;
  for (let i = 0; i < 150; i++) {
    put("accent", accentL, c);
    put("accent-hover", accentL + (dark ? 0.045 : -0.05), c * 1.02);
    put("accent-active", accentL + (dark ? 0.085 : -0.09), c * 1.05);
    const states = [
      tokens.accent,
      tokens["accent-hover"],
      tokens["accent-active"],
    ];
    const score = (on: string) =>
      Math.min(...states.map((bg) => contrast(on, bg)));
    tokens["on-accent"] = score(white) >= score(black) ? white : black;
    if (score(tokens["on-accent"]) >= 4.5) break;
    accentL += dark ? 0.005 : -0.005;
  }
  for (const [name, hue, offset, scale] of [
    ["success", 150, dark ? -0.05 : -0.04, 0.85],
    ["warning", 78, dark ? -0.03 : 0.03, 1],
    ["danger", 25, 0, 1],
    ["info", 255, 0, 0.9],
  ] as const) {
    const cc = (dark ? 0.13 : 0.15) * scale,
      l = (dark ? 0.8 : 0.6) + offset;
    put(name, l, cc, hue);
    put(`${name}-subtle`, dark ? 0.255 : 0.94, dark ? 0.035 : 0.025, hue);
    tokens[`${name}-text`] = readable(l, cc, hue, [
      ...backgrounds,
      tokens[`${name}-subtle`],
    ]);
  }
  // An opaque, calibrated outline stays visible; translucent rings alone do not.
  tokens.ring = tokens["accent-text"];
  const shadow = rgb(
    oklch(dark ? 0.04 : 0.22, Math.min(0.05, c * 0.22), h),
  ).join(",");
  const rgba = (alpha: number) => `rgba(${shadow},${alpha})`;
  tokens.shadow1 = `0 1px 2px ${rgba(dark ? 0.45 : 0.07)}, 0 1px 3px ${rgba(dark ? 0.35 : 0.05)}`;
  tokens.shadow2 = `0 2px 6px ${rgba(dark ? 0.4 : 0.06)}, 0 10px 30px ${rgba(dark ? 0.45 : 0.08)}`;
  tokens.overlay = rgba(dark ? 0.65 : 0.3);
  tokens["media-bg"] = oklch(0.19, 0.008, h);
  tokens["media-text"] = oklch(0.99, 0.006, h);
  return {
    source,
    tokens,
    fill: tokens.accent,
    on: tokens["on-accent"],
    text: tokens["accent-text"],
    soft: tokens["accent-subtle"],
  };
}

export function applyTheme(color: string, dark: boolean) {
  const { tokens, source } = themeColors(color, dark),
    root = document.documentElement;
  root.dataset.theme = dark ? "dark" : "light";
  const aliases: Record<string, string> = {
    card: "surface",
    side: "bg-subtle",
    muted: "text-muted",
    line: "border",
    soft: "bg-subtle",
    shadow: "shadow1",
    "accent-fill": "accent",
    "accent-on": "on-accent",
    "accent-soft": "accent-subtle",
    background: "bg",
    foreground: "text",
    "card-foreground": "text",
    popover: "surface-2",
    "popover-foreground": "text",
    primary: "accent",
    "primary-foreground": "on-accent",
    secondary: "bg-subtle",
    "secondary-foreground": "text",
    "muted-foreground": "text-muted",
    input: "border-strong",
    destructive: "danger-text",
    "destructive-foreground": "surface",
  };
  for (const [name, value] of Object.entries(tokens))
    root.style.setProperty(`--${name}`, value);
  for (const [name, token] of Object.entries(aliases))
    root.style.setProperty(`--${name}`, tokens[token]);
  root.style.setProperty("color-scheme", dark ? "dark" : "light");
  try {
    localStorage.setItem(
      "x2lt-theme",
      JSON.stringify({
        version: 1,
        accent: source,
        theme: dark ? "dark" : "light",
        tokens,
      }),
    );
  } catch {
    /* Storage may be disabled. */
  }
}
