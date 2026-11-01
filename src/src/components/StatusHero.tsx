// Copyright 2026 Ferran Duarri , GPL v2
// Answers the one question the Status page exists for: "is everything working, and if not, what do I do?"
// The headline is a plain sentence, every problem names its fix, and the fix is a button.
import type { ReactNode } from "react";

export type StatusCheck = {
  id: string;
  level: "ok" | "warn" | "note";
  title: string;
  hint?: string;
  action?: { label: string; onClick: () => void };
};

export function StatusHero({ checks, subtitle, banner }: { checks: StatusCheck[]; subtitle: string; banner?: ReactNode }) {
  const warns = checks.filter(c => c.level === "warn");
  const notes = checks.filter(c => c.level === "note");
  const oks = checks.filter(c => c.level === "ok");
  const ready = warns.length === 0;
  const headline = ready ? "Ready to play" : warns.length === 1 ? "One thing needs attention" : `${warns.length} things need attention`;
  return (
    <section className={`hero ${ready ? "hero-ok" : "hero-warn"}`} aria-labelledby="hero-headline">
      <div className="hero-top">
        <span className="hero-dot" aria-hidden="true" />
        <div>
          <h2 id="hero-headline" className="hero-headline">{headline}</h2>
          <p className="hero-sub">{subtitle}</p>
        </div>
      </div>
      {banner}
      {warns.length > 0 && (
        <ul className="hero-issues">
          {warns.map(c => (
            <li key={c.id} className="hero-issue">
              <div className="hero-issue-text">
                <strong>{c.title}</strong>
                {c.hint && <span>{c.hint}</span>}
              </div>
              {c.action && <button className="btn-primary" onClick={c.action.onClick}>{c.action.label}</button>}
            </li>
          ))}
        </ul>
      )}
      {(oks.length > 0 || notes.length > 0) && (
        <ul className="hero-chips" aria-label="Checks that passed">
          {oks.map(c => <li key={c.id} className="chip chip-ok"><span aria-hidden="true">✓</span> {c.title}</li>)}
          {notes.map(c => <li key={c.id} className="chip chip-note" title={c.hint}>{c.title}</li>)}
        </ul>
      )}
    </section>
  );
}
