import { useState, useRef, useEffect } from "react";
import { Icon } from "../icons";

/// Small "(i)" info icon that opens a plain-language explanation popup on
/// click. Meant to sit next to any setting/library row's title, so a
/// non-engineer can find out what a control actually does without needing
/// the surrounding paragraph text to spell it out inline.
export function InfoTip({ children }: { children: React.ReactNode }) {
  const [open, setOpen] = useState(false);
  const ref = useRef<HTMLDivElement>(null);

  useEffect(() => {
    if (!open) return;
    const onDocClick = (e: MouseEvent) => {
      if (ref.current && !ref.current.contains(e.target as Node)) setOpen(false);
    };
    document.addEventListener("mousedown", onDocClick);
    return () => document.removeEventListener("mousedown", onDocClick);
  }, [open]);

  return (
    <div ref={ref} style={{ position: "relative", display: "inline-flex", verticalAlign: "middle" }}>
      <button
        type="button"
        onClick={e => { e.stopPropagation(); setOpen(o => !o); }}
        aria-label="What does this do?"
        title="What does this do?"
        style={{
          display: "inline-flex", alignItems: "center", justifyContent: "center",
          width: 16, height: 16, marginLeft: 6, padding: 0,
          background: "transparent", border: "none", cursor: "pointer",
          color: open ? "#76b900" : "#6b7280",
        }}
      >
        <span style={{ display: "inline-flex", width: 14, height: 14 }}>
          <Icon.Info />
        </span>
      </button>
      {open && (
        <div
          role="tooltip"
          onClick={e => e.stopPropagation()}
          style={{
            position: "absolute", top: "calc(100% + 6px)", left: 0,
            minWidth: 240, maxWidth: 320, zIndex: 60,
            background: "#232323", border: "1px solid #2a2a2a", borderRadius: 6,
            boxShadow: "0 6px 18px rgba(0,0,0,0.4)", padding: "10px 12px",
            fontSize: 12, fontWeight: 400, color: "#d0d0d0", lineHeight: 1.5,
          }}
        >
          {children}
        </div>
      )}
    </div>
  );
}
