import type { ComponentPropsWithoutRef } from "react";
import "./library-card.css";

const sourceLabels = { x: "X", telegram: "Telegram", local: "本地导入" };
type LibraryCardSourceKind = keyof typeof sourceLabels;

type LibraryCardProps = ComponentPropsWithoutRef<"article"> & {
  source: LibraryCardSourceKind;
};

export function LibraryCard({ source, className, ...props }: LibraryCardProps) {
  return (
    <article
      {...props}
      className={`library-media-card ${className ?? ""}`}
      data-library-source={source}
    />
  );
}

export function LibraryCardSource({
  source,
}: {
  source: LibraryCardSourceKind;
}) {
  return (
    <span className="library-media-card-source">{sourceLabels[source]}</span>
  );
}
