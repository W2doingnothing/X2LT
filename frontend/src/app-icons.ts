export const appIcons = [
  { id: "gray-pixel", label: "灰色像素" },
  { id: "black-white-pixel", label: "黑白像素" },
  { id: "black-white-illustration", label: "黑白插画" },
  { id: "gray-illustration", label: "灰色插画" },
] as const;

export type AppIcon = (typeof appIcons)[number]["id"];
export const defaultAppIcon = appIcons[0].id;

export function resolveAppIcon(value?: string | null): AppIcon {
  return appIcons.find(({ id }) => id === value)?.id ?? defaultAppIcon;
}

export function appIconUrl(icon: AppIcon) {
  return `${import.meta.env.BASE_URL}app-icons/${icon}.png`;
}

export function applyAppIcon(icon: AppIcon) {
  document.querySelector<HTMLLinkElement>('link[rel="icon"]')?.setAttribute("href", appIconUrl(icon));
}
