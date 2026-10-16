// A matched header for the viewer's two panes — the source (원본) and the translated
// output (번역본). A thin accent bar marks each: neutral for the source, the brand
// gradient for the translation (the product), so the two columns read as a clear pair.
export function PaneLabel({
  title,
  lang,
  tone,
}: {
  title: string;
  lang: string;
  tone: "source" | "target";
}) {
  const target = tone === "target";
  return (
    <span className="inline-flex items-center gap-2">
      <span
        aria-hidden
        className="h-4 w-[3px] shrink-0 rounded-full"
        style={{ background: target ? "var(--gradient-brand)" : "var(--border-strong)" }}
      />
      <span
        className="font-semibold"
        style={{ fontSize: "var(--fs-label)", color: target ? "var(--text)" : "var(--text-2)" }}
      >
        {title}
      </span>
      <span className="slug">{lang}</span>
    </span>
  );
}
