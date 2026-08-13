import { Icon } from "../icons";
import type { ViewType } from "../types";

const NAV: { id: ViewType; icon: () => React.ReactElement; label: string }[] = [
  { id: "status",   icon: Icon.Activity, label: "Status" },
  { id: "games",    icon: Icon.Gamepad,  label: "Games" },
  { id: "displays", icon: Icon.Monitor,  label: "Displays" },
  { id: "profile",  icon: Icon.Cpu,      label: "Profile" },
  { id: "live",     icon: Icon.Zap,      label: "Live" },
  { id: "about",    icon: Icon.Info,     label: "About" },
];

export function Sidebar({ view, setView }: { view: ViewType; setView: (v: ViewType) => void }) {
  return (
    <nav className="sidebar">
      <div className="sidebar-logo">
        <div className="sidebar-logo-icon">GB</div>
        <div className="sidebar-logo-dot" />
      </div>
      <div className="sidebar-nav">
        {NAV.map(item => (
          <button
            key={item.id}
            className={`nav-item${view === item.id ? " active" : ""}`}
            onClick={() => setView(item.id)}
            title={item.label}
          >
            <span className="nav-icon"><item.icon /></span>
            <span>{item.label}</span>
          </button>
        ))}
      </div>
    </nav>
  );
}
