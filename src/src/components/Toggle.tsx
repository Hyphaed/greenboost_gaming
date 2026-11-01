// One on/off switch for the whole app. A real <button role="switch">, so it
// takes focus, toggles with Space or Enter, and is announced as on or off.
// Styling lives in index.css (.toggle); the earlier copies were inline-styled
// divs that a keyboard could not operate.
export function Toggle({ on, onChange, label, disabled }: {
  on: boolean;
  onChange: (next: boolean) => void;
  /** Accessible name; use the row's title. */
  label?: string;
  disabled?: boolean;
}) {
  return (
    <button
      type="button"
      role="switch"
      aria-checked={on}
      aria-label={label}
      disabled={disabled}
      className={`toggle${on ? " on" : ""}`}
      onClick={e => { e.stopPropagation(); onChange(!on); }}
    >
      <span className="toggle-track" aria-hidden="true" />
      <span className="toggle-thumb" aria-hidden="true" />
    </button>
  );
}

/** The switch look for rows whose whole row is already clickable (the click
 *  bubbles to the row's handler). Adds what the old decorative div lacked:
 *  a switch role, an on/off state, a tab stop, and Space/Enter. */
export function SwitchLook({ on, label, disabled, onClick }: {
  on: boolean;
  label?: string;
  disabled?: boolean;
  /** Only for switches that are not inside a clickable row. */
  onClick?: () => void;
}) {
  return (
    <span
      role="switch"
      aria-checked={on}
      aria-label={label}
      aria-disabled={disabled || undefined}
      tabIndex={disabled ? -1 : 0}
      className={`toggle${on ? " on" : ""}`}
      style={disabled ? { opacity: 0.5, cursor: "not-allowed" } : undefined}
      onClick={onClick}
      onKeyDown={e => {
        if (disabled) return;
        if (e.key === " " || e.key === "Enter") { e.preventDefault(); e.currentTarget.click(); }
      }}
    >
      <span className="toggle-track" aria-hidden="true" />
      <span className="toggle-thumb" aria-hidden="true" />
    </span>
  );
}
