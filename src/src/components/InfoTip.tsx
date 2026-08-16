import { useState, useRef, useEffect, useCallback } from "react";
import { createPortal } from "react-dom";
import { Icon } from "../icons";

/// Small "(i)" info icon that opens a plain-language explanation popup on
/// click. Meant to sit next to any setting/library row's title, so a
/// non-engineer can find out what a control actually does without needing
/// the surrounding paragraph text to spell it out inline.
///
/// Rendered into a portal at document.body with fixed positioning, rather
/// than absolutely inside the row. It used to be absolute, which meant every
/// ancestor with `overflow` clipped it , `.content-scroll` and
/// `.gs-section-body` both do , so a tip near the bottom of a section was cut
/// off mid-sentence. That was survivable when tips were one line; once the
/// GreenBoost what/why/verify prose moved into them it hid most of the text.
///
/// Placement rules: prefer below the icon, flip above when there isn't room,
/// clamp to the viewport horizontally, and cap the height so a long tip
/// scrolls inside itself instead of running off screen.

const EDGE = 8;      // keep this far from the viewport edge
const GAP = 6;       // gap between icon and tip
const MAX_W = 420;
const MAX_H = 460;
const MIN_H = 140;   // below this, flipping is better than scrolling

type Placement = {
  left: number; width: number; maxHeight: number;
  top?: number; bottom?: number;
};

export function InfoTip({ children }: { children: React.ReactNode }) {
  const [open, setOpen] = useState(false);
  const [pos, setPos] = useState<Placement | null>(null);
  const btnRef = useRef<HTMLButtonElement>(null);
  const tipRef = useRef<HTMLDivElement>(null);

  const place = useCallback(() => {
    const b = btnRef.current?.getBoundingClientRect();
    if (!b) return;
    const vw = window.innerWidth;
    const vh = window.innerHeight;

    const roomBelow = vh - b.bottom - GAP - EDGE;
    const roomAbove = b.top - GAP - EDGE;
    // Only flip up when below is genuinely cramped AND above is roomier,
    // so the tip doesn't jump sides on tiny layout shifts.
    const below = roomBelow >= MIN_H || roomBelow >= roomAbove;

    const width = Math.min(MAX_W, vw - EDGE * 2);
    let left = b.left;
    if (left + width > vw - EDGE) left = vw - EDGE - width;
    if (left < EDGE) left = EDGE;

    const maxHeight = Math.min(MAX_H, Math.max(MIN_H, below ? roomBelow : roomAbove));

    setPos(below
      ? { left, width, maxHeight, top: b.bottom + GAP }
      // Anchor by `bottom` so the tip grows upward from the icon and never
      // needs its own height measured first.
      : { left, width, maxHeight, bottom: vh - b.top + GAP });
  }, []);

  useEffect(() => {
    if (!open) { setPos(null); return; }
    place();

    const onDocPointer = (e: MouseEvent) => {
      const t = e.target as Node;
      if (btnRef.current?.contains(t)) return;
      if (tipRef.current?.contains(t)) return;
      setOpen(false);
    };
    const onKey = (e: KeyboardEvent) => { if (e.key === "Escape") setOpen(false); };
    // Capture phase: the tip is outside the scrolling element in the DOM, so
    // it can't follow it automatically , recompute instead of drifting.
    const onScroll = () => place();

    document.addEventListener("mousedown", onDocPointer);
    document.addEventListener("keydown", onKey);
    window.addEventListener("scroll", onScroll, true);
    window.addEventListener("resize", onScroll);
    return () => {
      document.removeEventListener("mousedown", onDocPointer);
      document.removeEventListener("keydown", onKey);
      window.removeEventListener("scroll", onScroll, true);
      window.removeEventListener("resize", onScroll);
    };
  }, [open, place]);

  return (
    <>
      <button
        ref={btnRef}
        type="button"
        onClick={e => { e.stopPropagation(); setOpen(o => !o); }}
        aria-label="What does this do?"
        aria-expanded={open}
        title="What does this do?"
        style={{
          display: "inline-flex", alignItems: "center", justifyContent: "center",
          width: 16, height: 16, marginLeft: 6, padding: 0,
          background: "transparent", border: "none", cursor: "pointer",
          color: open ? "#76b900" : "#6b7280",
          verticalAlign: "middle",
        }}
      >
        <span style={{ display: "inline-flex", width: 14, height: 14 }}>
          <Icon.Info />
        </span>
      </button>

      {open && pos && createPortal(
        <div
          ref={tipRef}
          role="tooltip"
          onClick={e => e.stopPropagation()}
          style={{
            position: "fixed",
            left: pos.left,
            ...(pos.top !== undefined ? { top: pos.top } : { bottom: pos.bottom }),
            width: pos.width,
            maxHeight: pos.maxHeight,
            overflowY: "auto",
            overscrollBehavior: "contain",
            zIndex: 1000,
            background: "#232323", border: "1px solid #3a3a3a", borderRadius: 6,
            boxShadow: "0 8px 24px rgba(0,0,0,0.55)", padding: "12px 14px",
            fontSize: 12, fontWeight: 400, color: "#d0d0d0", lineHeight: 1.55,
            textTransform: "none", letterSpacing: 0, textAlign: "left",
            whiteSpace: "normal",
          }}
        >
          {children}
        </div>,
        document.body,
      )}
    </>
  );
}
