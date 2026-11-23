import type { Library } from "./types";

type LibraryAvailability = Pick<Library, "id" | "online">;

export function eligibleRandomVideoIds(
  libraries: LibraryAvailability[],
  selected: string[],
): string[] {
  const online = new Set(libraries.filter((library) => library.online).map((library) => library.id));
  return Array.from(new Set(selected.filter((id) => online.has(id))));
}

export function initialRandomVideoIds(
  libraries: LibraryAvailability[],
  defaultLibrary: string | null,
  saved: string[] = [],
): string[] {
  const available = libraries.filter((library) => library.online).map((library) => library.id);
  const remembered = eligibleRandomVideoIds(libraries, saved);
  if (remembered.length) return remembered;
  return defaultLibrary && available.includes(defaultLibrary) ? [defaultLibrary] : available;
}
