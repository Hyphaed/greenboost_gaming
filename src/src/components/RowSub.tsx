// Copyright 2026 Ferran Duarri , GPL v2
// A setting row shows its one-line benefit; the long explanation opens on request (and always while searching).
import { useState } from "react";

export function RowSub({ benefit, sub, forceOpen }: { benefit?: string; sub?: React.ReactNode; forceOpen?: boolean }) {
  const [open, setOpen] = useState(false);
  const showSub = !!sub && (!benefit || open || !!forceOpen);
  return (
    <>
      {benefit && (
        <div className="gs-row-benefit">
          {benefit}
          {sub && !forceOpen && (
            <button type="button" className="gs-more" aria-expanded={open} onClick={() => setOpen(o => !o)}>
              {open ? "Hide details" : "Details"}
            </button>
          )}
        </div>
      )}
      {showSub && <div className="gs-row-sub">{sub}</div>}
    </>
  );
}
