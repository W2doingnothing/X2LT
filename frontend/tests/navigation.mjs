export async function openDownloads(page) {
  const nav = page.locator("nav");
  const group = nav.getByRole("button", { name: "下载任务", exact: true });
  await group.waitFor();
  if ((await group.getAttribute("aria-expanded")) === "false") await group.click();
  await nav.getByRole("button", { name: "推特下载", exact: true }).click();
}
