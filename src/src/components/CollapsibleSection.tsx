import { useState } from "react";
import { Icon } from "../icons";

export function CollapsibleSection({
  title, subtitle, defaultOpen = true, children,
}: {
  title: string;
  /** One-line summary of current state, shown next to the title so a
   * collapsed section still conveys something (e.g. "2 differ from
   * recommended"). Optional , omit for sections with nothing to summarize. */
  subtitle?: React.ReactNode;
  defaultOpen?: boolean; children: React.ReactNode;
}) {
  const [open, setOpen] = useState(defaultOpen);
  return (
    <div className="gs-section">
      <button
        className="gs-section-header"
        aria-expanded={open}
        onClick={() => setOpen(o => !o)}
      >
        <span style={{
          display: "inline-flex",
          transform: open ? "rotate(0deg)" : "rotate(-90deg)",
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
      {open && <div className="gs-section-body">{children}</div>}
    </div>
  );
}
