import { useState } from "react";
import { Icon } from "../icons";

export function CollapsibleSection({
  title, subtitle, defaultOpen = true, forceOpen, children,
}: {
  title: string;
  /** One-line summary of current state, shown next to the title so a
   * collapsed section still conveys something (e.g. "2 differ from
   * recommended"). Optional , omit for sections with nothing to summarize. */
  subtitle?: React.ReactNode;
  defaultOpen?: boolean;
  /** Override the user's open/closed choice without destroying it. Set while
   * a search is active: a collapsed section that contains a match has to
   * show it, but the user's own collapse state must come back untouched
   * when they clear the search. */
  forceOpen?: boolean;
  children: React.ReactNode;
}) {
  const [open, setOpen] = useState(defaultOpen);
  const shown = forceOpen ?? open;
  return (
    <div className="gs-section">
      <button
        className="gs-section-header"
        aria-expanded={shown}
        onClick={() => setOpen(o => !o)}
      >
        <span style={{
          display: "inline-flex",
          transform: shown ? "rotate(0deg)" : "rotate(-90deg)",
          transition: "transform 0.15s",
        }}><Icon.ChevronDown /></span>
        <span>{title}</span>
        {subtitle && (
          <span style={{
            marginLeft: "auto", fontSize: 11, fontWeight: 400,
            color: "#8a9ab0", textTransform: "none", letterSpacing: 0,
          }}>{subtitle}</span>
        )}
      </button>
      {shown && <div className="gs-section-body">{children}</div>}
    </div>
  );
}
