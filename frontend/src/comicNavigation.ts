export function comicPages(page: number, count: number, spread: boolean): number[] {
  if (!count) return [];
  const current = Math.max(0, Math.min(page, count - 1));
  if (!spread || !current) return [current];
  const first = current % 2 === 0 ? current - 1 : current;
  return first + 1 < count ? [first, first + 1] : [first];
}

export function turnComicPage(page: number, count: number, spread: boolean, direction: number): number {
  const first = comicPages(page, count, spread)[0];
  if (first === undefined || !direction) return page;
  const step = spread && !(direction > 0 ? first === 0 : first === 1) ? 2 : 1;
  const next = first + Math.sign(direction) * step;
  return next >= 0 && next < count ? next : page;
}
