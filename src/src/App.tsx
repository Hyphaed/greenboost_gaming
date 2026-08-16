// Copyright 2026 Ferran Duarri , GPL v2
// GreenBoost is an independent open-source project and is not affiliated with,
// endorsed by, or sponsored by NVIDIA Corporation.
// NVIDIA, CUDA, GeForce, and RTX are trademarks of NVIDIA Corporation.

import { useState, useEffect } from "react";
import type { ViewType } from "./types";
import { loadGlobalSettings } from "./store/globalSettings";
import { checkForUpdates } from "./store/updates";
import { Sidebar } from "./components/Sidebar";
import { StatusView } from "./views/Status";
import { GamesView } from "./views/Games";
import { DisplaysView } from "./views/Displays";
import { GpuProfileView } from "./views/Profile";
import { AboutView } from "./views/About";
import { LiveView } from "./views/Live";

const VIEW_LABELS: Record<ViewType, string> = {
  status:   "Status",
  games:    "Games",
  displays: "Displays",
  profile:  "Profile",
  live:     "Live Stats",
  about:    "About",
};

export default function App() {
  const [view, setView] = useState<ViewType>("status");

  // Warm the shared stores once for the whole session. Views used to each
  // fetch their own copy on mount, which meant navigating re-fetched
  // everything and left views disagreeing about the same settings.
  useEffect(() => {
    loadGlobalSettings();
    checkForUpdates(false);
  }, []);

  const renderContent = () => {
    switch (view) {
      case "status":   return <StatusView />;
      case "games":    return <GamesView />;
      case "displays": return <DisplaysView />;
      case "profile":  return <GpuProfileView />;
      case "live":     return <LiveView onNavigate={setView} />;
      case "about":    return <AboutView />;
    }
  };

  return (
    <div className="app-shell">
      <Sidebar view={view} setView={setView} />

      <div className="main-content">
        <div className="page-header">
          <h1 className="page-title">{VIEW_LABELS[view]}</h1>
        </div>

        <div style={{ flex: 1, display: "flex", flexDirection: "column", overflow: "hidden", minHeight: 0 }}>
          {renderContent()}
        </div>
      </div>
    </div>
  );
}
