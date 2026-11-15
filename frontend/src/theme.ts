const rgb = (hex: string) =>
  [1, 3, 5].map((i) => parseInt(hex.slice(i, i + 2), 16));
const hex = (c: number[]) =>
  "#" + c.map((v) => Math.round(v).toString(16).padStart(2, "0")).join("");
function luminance(color: string) {
  const c = rgb(color).map((v) => {
    v /= 255;
    return v <= 0.04045 ? v / 12.92 : ((v + 0.055) / 1.055) ** 2.4;
  });
  return c[0] * 0.2126 + c[1] * 0.7152 + c[2] * 0.0722;
}
export function contrast(a: string, b: string) {
  const x = luminance(a),
    y = luminance(b);
  return (Math.max(x, y) + 0.05) / (Math.min(x, y) + 0.05);
}
function mix(a: string, b: string, ratio: number) {
  return hex(rgb(a).map((v, i) => v * (1 - ratio) + rgb(b)[i] * ratio));
}
export function themeColors(color: string, dark: boolean) {
  if (!/^#[0-9a-f]{6}$/i.test(color))
    throw Error("请输入六位十六进制颜色码，例如 #397863");
  color = color.toLowerCase();
  const bg = dark ? "#141b18" : "#f6f8f7";
  let text = color;
  for (let i = 0; i < 30 && contrast(text, bg) < 4.5; i++)
    text = mix(text, dark ? "#ffffff" : "#000000", 0.12);
  return {
    fill: color,
    on:
      contrast(color, "#ffffff") >= contrast(color, "#000000")
        ? "#ffffff"
        : "#000000",
    text,
    soft: mix(bg, color, dark ? 0.16 : 0.09),
  };
}
export function applyTheme(color: string, dark: boolean) {
  const c = themeColors(color, dark);
  const root = document.documentElement;
  root.dataset.theme = dark ? "dark" : "light";
  root.style.setProperty("--accent", c.text);
  root.style.setProperty("--accent-fill", c.fill);
  root.style.setProperty("--accent-on", c.on);
  root.style.setProperty("--accent-soft", c.soft);
}
