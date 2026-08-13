import { useState, useEffect, useCallback, useRef } from "react";
import { invoke } from "@tauri-apps/api/core";
import type { DisplayInfo } from "../types";
import { Icon } from "../icons";

type DisplayLayout = Record<string, { x: number; y: number; w: number; h: number }>;
type ArrangementMode = "join" | "clone";
type DisplayTab      = "settings" | "night-light";

interface NightLightState {
  enabled:        boolean;
  temperature:    number;
  schedule_auto:  boolean;
  manual_from:    number;
  manual_to:      number;
  available:      boolean;
}

const SNAP_PX        = 25;
const DIAGRAM_HEIGHT = 220;

function parseRes(s: string): { w: number; h: number } {
  const m = s.match(/^(\d+)\s*[x×]\s*(\d+)/i);
  return m ? { w: parseInt(m[1]), h: parseInt(m[2]) } : { w: 1920, h: 1080 };
}

function defaultLayout(displays: DisplayInfo[]): DisplayLayout {
  const out: DisplayLayout = {};
  let cursor = 0;
  for (const d of displays) {
    const { w, h } = parseRes(d.current_mode || "1920x1080");
    out[d.name] = { x: cursor, y: 0, w, h };
    cursor += w;
  }
  return out;
}

function MonitorArrangementCanvas({
  displays, layout, onLayoutChange,
}: {
  displays: DisplayInfo[];
  layout: DisplayLayout;
  onLayoutChange: (next: DisplayLayout) => void;
}) {
  const rects = displays.map(d => ({ d, r: layout[d.name] })).filter(x => x.r);
  if (rects.length === 0) return null;

  const PAD = 20;
  const minX = Math.min(...rects.map(({ r }) => r!.x));
  const minY = Math.min(...rects.map(({ r }) => r!.y));
  const maxX = Math.max(...rects.map(({ r }) => r!.x + r!.w));
  const maxY = Math.max(...rects.map(({ r }) => r!.y + r!.h));
  const bbW  = maxX - minX || 1;
  const bbH  = maxY - minY || 1;

  const scale  = (DIAGRAM_HEIGHT - 2 * PAD) / bbH;
  const svgW   = bbW * scale + 2 * PAD;
  const svgH   = DIAGRAM_HEIGHT;

  const toSvgX = (lx: number) => PAD + (lx - minX) * scale;
  const toSvgY = (ly: number) => PAD + (ly - minY) * scale;
  const fromSvgX = (sx: number) => (sx - PAD) / scale + minX;
  const fromSvgY = (sy: number) => (sy - PAD) / scale + minY;

  const dragRef = useRef<{ name: string; anchorX: number; anchorY: number;
                            origX: number; origY: number } | null>(null);
  const svgRef  = useRef<SVGSVGElement | null>(null);

  const startDrag = (name: string, e: React.MouseEvent) => {
    const svg = svgRef.current; if (!svg) return;
    const pt  = svg.createSVGPoint(); pt.x = e.clientX; pt.y = e.clientY;
    const ctm = svg.getScreenCTM(); if (!ctm) return;
    const local = pt.matrixTransform(ctm.inverse());
    const cur = layout[name];
    if (!cur) return;
    dragRef.current = {
      name,
      anchorX: fromSvgX(local.x), anchorY: fromSvgY(local.y),
      origX:   cur.x,             origY:   cur.y,
    };
  };

  const onMove = (e: React.MouseEvent) => {
    const d = dragRef.current; const svg = svgRef.current;
    if (!d || !svg) return;
    const pt = svg.createSVGPoint(); pt.x = e.clientX; pt.y = e.clientY;
    const ctm = svg.getScreenCTM(); if (!ctm) return;
    const local = pt.matrixTransform(ctm.inverse());
    const newX  = d.origX + (fromSvgX(local.x) - d.anchorX);
    const newY  = d.origY + (fromSvgY(local.y) - d.anchorY);
    onLayoutChange({
      ...layout,
      [d.name]: { ...layout[d.name], x: Math.round(newX), y: Math.round(newY) },
    });
  };

  const endDrag = () => {
    const d = dragRef.current;
    if (!d) return;
    dragRef.current = null;

    const me = layout[d.name];
    if (!me) return;
    const others = displays.filter(o => o.name !== d.name)
                           .map(o => layout[o.name])
                           .filter(Boolean) as { x: number; y: number; w: number; h: number }[];

    let dx = 0, dy = 0;
    let bestX = SNAP_PX, bestY = SNAP_PX;
    for (const o of others) {
      for (const [target, ref] of [
        [o.x + o.w, me.x],
        [o.x,       me.x + me.w],
        [o.x,       me.x],
        [o.x + o.w, me.x + me.w],
      ] as [number, number][]) {
        const diff = target - ref;
        if (Math.abs(diff) < bestX) { bestX = Math.abs(diff); dx = diff; }
      }
      for (const [target, ref] of [
        [o.y + o.h, me.y],
        [o.y,       me.y + me.h],
        [o.y,       me.y],
        [o.y + o.h, me.y + me.h],
      ] as [number, number][]) {
        const diff = target - ref;
        if (Math.abs(diff) < bestY) { bestY = Math.abs(diff); dy = diff; }
      }
    }
    if (dx !== 0 || dy !== 0) {
      onLayoutChange({
        ...layout,
        [d.name]: { ...me, x: me.x + dx, y: me.y + dy },
      });
    }
  };

  return (
    <svg ref={svgRef}
         viewBox={`0 0 ${svgW} ${svgH}`}
         width="100%" height={svgH}
         onMouseMove={onMove}
         onMouseUp={endDrag}
         onMouseLeave={endDrag}
         style={{ background: "#1a1a1a", borderRadius: 6, display: "block",
                  cursor: dragRef.current ? "grabbing" : "default" }}>
      {rects.map(({ d, r }, i) => (
        <g key={d.name}
           onMouseDown={e => startDrag(d.name, e)}
           style={{ cursor: "grab" }}>
          <rect
            x={toSvgX(r!.x)} y={toSvgY(r!.y)}
            width={r!.w * scale} height={r!.h * scale}
            rx={4} ry={4}
            fill={d.primary ? "rgba(118,185,0,0.18)" : "#232323"}
            stroke={d.primary ? "#76b900" : "#2a2a2a"}
            strokeWidth={1.5}
          />
          <text
            x={toSvgX(r!.x) + (r!.w * scale) / 2}
            y={toSvgY(r!.y) + (r!.h * scale) / 2 + 4}
            textAnchor="middle"
            fill="#e6e6e6" fontSize={12} fontWeight={600}
            style={{ pointerEvents: "none" }}
          >
            {i + 1}  {d.name}
          </text>
          <text
            x={toSvgX(r!.x) + (r!.w * scale) / 2}
            y={toSvgY(r!.y) + (r!.h * scale) / 2 + 20}
            textAnchor="middle"
            fill="#7a8a9a" fontSize={10}
            style={{ pointerEvents: "none" }}
          >
            {r!.w}×{r!.h}
          </text>
        </g>
      ))}
    </svg>
  );
}

function HourStepper({ value, onChange }: { value: number; onChange: (v: number) => void }) {
  const fmt = (v: number) => {
    const h = Math.floor(v) % 24;
    const m = Math.round((v % 1) * 60);
    return `${String(h).padStart(2, "0")}:${String(m).padStart(2, "0")}`;
  };
  const step = (delta: number) => {
    const next = Math.round(((value + delta) + 24) % 24 * 2) / 2;
    onChange(next);
  };
  const btnStyle: React.CSSProperties = {
    display: "flex", alignItems: "center", justifyContent: "center",
    width: 22, height: 22, borderRadius: 4,
    background: "#1e1e1e", border: "1px solid #2e2e2e",
    color: "#9a9a9a", cursor: "pointer", fontSize: 13, lineHeight: 1,
    userSelect: "none",
  };
  return (
    <div style={{ display: "flex", alignItems: "center", gap: 4 }}>
      <button style={btnStyle} onClick={() => step(-0.5)} title="−30 min">−</button>
      <div style={{
        minWidth: 52, textAlign: "center", fontSize: 14, fontVariantNumeric: "tabular-nums",
        color: "#ffffff", background: "#181818", border: "1px solid #2e2e2e",
        borderRadius: 4, padding: "3px 8px", letterSpacing: "0.04em",
      }}>
        {fmt(value)}
      </div>
      <button style={btnStyle} onClick={() => step(0.5)} title="+30 min">+</button>
    </div>
  );
}

function NightLightPanel() {
  const [state, setState] = useState<NightLightState | null>(null);
  const [msg, setMsg] = useState<string | null>(null);

  const reload = useCallback(() => {
    invoke<NightLightState>("get_night_light")
      .then(setState)
      .catch(e => setMsg(`Load failed: ${e}`));
  }, []);

  useEffect(() => { reload(); }, [reload]);

  const update = async (patch: Partial<NightLightState>) => {
    if (!state) return;
    const next = { ...state, ...patch };
    setState(next);
    try {
      const ok: string = await invoke("apply_night_light", { state: next });
      setMsg(ok);
    } catch (e: any) {
      setMsg(`Save failed: ${e?.message ?? e}`);
      reload();
    }
  };

  if (!state) {
    return (
      <div className="content-scroll">
        <p style={{ color: "#9a9a9a", padding: 24 }}>Reading Night Light…</p>
      </div>
    );
  }

  if (!state.available) {
    return (
      <div className="content-scroll">
        <div className="gs-card">
          <div className="gs-card-header">Night Light</div>
          <div className="gs-row">
            <div className="gs-row-label">
              <div className="gs-row-title">gsettings not available</div>
              <div className="gs-row-sub">
                We drive GNOME's <code>org.gnome.settings-daemon.plugins.
                color</code> schema for Night Light.  On non-GNOME
                sessions (KDE, Sway, etc.) use your DE's native night
                colour / blue-light filter instead.
              </div>
            </div>
            <div className="gs-row-control" />
          </div>
        </div>
      </div>
    );
  }

  const tToggle = (on: boolean, onClick: () => void) => (
    <div onClick={onClick} role="switch" aria-checked={on} tabIndex={0}
         style={{
           display: "inline-flex", alignItems: "center",
           width: 44, height: 22, padding: 2,
           background: on ? "#76b900" : "#3a3a3a",
           borderRadius: 999, cursor: "pointer",
           transition: "background 120ms ease",
         }}>
      <div style={{
        width: 18, height: 18, borderRadius: 999, background: "#ffffff",
        transform: `translateX(${on ? 22 : 0}px)`,
        transition: "transform 120ms ease",
      }} />
    </div>
  );

  return (
    <div className="content-scroll">
      <div className="gs-card">
        <div className="gs-card-header">Night Light</div>

        <div className="gs-row">
          <div className="gs-row-label">
            <div className="gs-row-title">Enable Night Light</div>
            <div className="gs-row-sub">
              Shifts your screen towards warmer colours after sunset to
              reduce blue-light exposure.
            </div>
          </div>
          <div className="gs-row-control">
            {tToggle(state.enabled, () => update({ enabled: !state.enabled }))}
          </div>
        </div>

        <div className="gs-row">
          <div className="gs-row-label">
            <div className="gs-row-title">Colour temperature</div>
            <div className="gs-row-sub">
              1700 K = strongest warm tint, 6500 K = neutral.  4000 K is
              the GNOME default.
            </div>
          </div>
          <div className="gs-row-control" style={{ minWidth: 280 }}>
            <input
              type="range" min={1700} max={6500} step={100}
              value={state.temperature}
              onChange={e => update({ temperature: Number(e.target.value) })}
              style={{ width: 200 }}
              disabled={!state.enabled}
            />
            <span style={{ marginLeft: 10, color: "#ffffff" }}>
              {state.temperature} K
            </span>
          </div>
        </div>

        <div className="gs-row">
          <div className="gs-row-label">
            <div className="gs-row-title">Automatic schedule</div>
            <div className="gs-row-sub">
              Use sunset / sunrise from your location.  Disable to set
              the active hours manually.
            </div>
          </div>
          <div className="gs-row-control">
            {tToggle(state.schedule_auto,
                     () => update({ schedule_auto: !state.schedule_auto }))}
          </div>
        </div>

        {!state.schedule_auto && (
          <div className="gs-row">
            <div className="gs-row-label">
              <div className="gs-row-title">Manual schedule</div>
              <div className="gs-row-sub">
                E.g. 22:00 → 06:00 for 10 pm to 6 am.
              </div>
            </div>
            <div className="gs-row-control" style={{ display: "flex", alignItems: "center", gap: 10 }}>
              <HourStepper
                value={state.manual_from}
                onChange={v => update({ manual_from: v })}
              />
              <span style={{ color: "#8a9ab0", fontSize: 14 }}>→</span>
              <HourStepper
                value={state.manual_to}
                onChange={v => update({ manual_to: v })}
              />
            </div>
          </div>
        )}
      </div>
      {msg && <p style={{ fontSize: 12, color: "#76b900",
                          margin: "10px 24px 0" }}>{msg}</p>}
    </div>
  );
}

export function DisplaysView() {
  const [displays, setDisplays] = useState<DisplayInfo[]>([]);
  const [selected, setSelected] = useState<DisplayInfo | null>(null);
  const [selRes, setSelRes] = useState("");
  const [selRate, setSelRate] = useState(0);
  const [rotation, setRotation] = useState("normal");
  const [scale, setScale]   = useState(100);
  const [arrangement, setArrangement] = useState<ArrangementMode>("join");
  const [layout, setLayout] = useState<DisplayLayout>({});
  const [tab, setTab]       = useState<DisplayTab>("settings");
  const [applyMsg, setApplyMsg] = useState<string | null>(null);
  const [applying, setApplying] = useState(false);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    invoke<DisplayInfo[]>("query_displays").then(d => {
      setDisplays(d);
      if (d.length > 0) selectDisplay(d[0]);
      setLayout(defaultLayout(d));
    }).catch(console.error).finally(() => setLoading(false));
  }, []);

  const selectDisplay = (d: DisplayInfo) => {
    setSelected(d);
    setSelRes(d.current_mode);
    setSelRate(d.current_rate);
    setRotation("normal");
    setApplyMsg(null);
  };

  const availableRates = selected
    ? (selected.modes.find(m => m.resolution === selRes)?.rates ?? [])
    : [];

  const handleApply = async () => {
    if (!selected) return;
    setApplying(true); setApplyMsg(null);
    const messages: string[] = [];
    try {
      messages.push(await invoke("apply_display_mode", {
        name: selected.name, resolution: selRes, rate: selRate
      }));
      if (rotation !== "normal") {
        messages.push(await invoke("apply_display_rotation",
          { name: selected.name, rotation }));
      }
      if (scale !== 100) {
        try {
          messages.push(await invoke("apply_display_scale",
            { name: selected.name, percent: scale }));
        } catch (e: any) {
          messages.push(`scale skipped: ${e?.message ?? e}`);
        }
      }
      if (!selected.primary) {
        try {
          messages.push(await invoke("apply_display_primary",
            { name: selected.name }));
        } catch (e: any) {
          messages.push(`primary skipped: ${e?.message ?? e}`);
        }
      }
      if (displays.length > 1) {
        try {
          if (arrangement === "join") {
            const positions = displays
              .map(d => layout[d.name] ? {
                name: d.name,
                x:    layout[d.name].x,
                y:    layout[d.name].y,
              } : null)
              .filter(Boolean);
            if (positions.length === displays.length) {
              messages.push(await invoke("apply_display_positions",
                { positions }));
            } else {
              messages.push(await invoke("apply_display_arrangement", {
                mode: arrangement,
                displays: displays.map(d => d.name),
              }));
            }
          } else {
            messages.push(await invoke("apply_display_arrangement", {
              mode: arrangement,
              displays: displays.map(d => d.name),
            }));
          }
        } catch (e: any) {
          messages.push(`arrangement skipped: ${e?.message ?? e}`);
        }
      }
      setApplyMsg(messages.join(" · "));
    } catch (e: any) {
      setApplyMsg("Error: " + (e?.message ?? e));
    }
    // Same "success message but nothing on screen updates" gap as the VRR/
    // enabled toggles: mode, rotation, scale, primary, and arrangement all
    // report success without ever touching `selected`/`displays`, so e.g.
    // the resolution dropdown's "(current)" label and the primary badge
    // stay stale until you navigate away and back. Re-fetch rather than
    // patch each field individually , this one call can change five things
    // at once (some of which may have partially failed above), and only
    // the compositor's own state is authoritative for the result.
    try {
      const fresh = await invoke<DisplayInfo[]>("query_displays");
      setDisplays(fresh);
      // Not selectDisplay() , it resets applyMsg to null as a side effect,
      // which would immediately wipe the success/error message set above.
      const stillSelected = fresh.find(d => d.name === selected.name);
      if (stillSelected) {
        setSelected(stillSelected);
        setSelRes(stillSelected.current_mode);
        setSelRate(stillSelected.current_rate);
      }
    } catch { /* best-effort refresh; the apply result message already landed */ }
    setApplying(false);
  };

  const ROTATIONS = [
    { id: "normal",   label: "Landscape" },
    { id: "inverted", label: "Landscape (Flipped)" },
    { id: "left",     label: "Portrait" },
    { id: "right",    label: "Portrait (Flipped)" },
  ];

  if (loading) return (
    <div className="placeholder-view">
      <span className="animate-spin" style={{ display: "inline-block" }}><Icon.Refresh /></span>
      <p>Detecting displays…</p>
    </div>
  );

  if (displays.length === 0) return (
    <div className="placeholder-view">
      <Icon.Monitor />
      <p>No displays detected. Ensure xrandr is installed.</p>
    </div>
  );

  return (
    <>
      <div className="sub-nav">
        <button
          className={`sub-nav-tab${tab === "settings" ? " active" : ""}`}
          onClick={() => setTab("settings")}>Settings</button>
        <button
          className={`sub-nav-tab${tab === "night-light" ? " active" : ""}`}
          onClick={() => setTab("night-light")}>Night Light</button>
      </div>

      {tab === "night-light" && <NightLightPanel />}

      {tab === "settings" && (
    <div className="content-scroll">
      <div style={{ maxWidth: 780, margin: "0 auto" }}>

        {displays.length > 1 && (
          <>
            <p className="section-title">Arrangement</p>
            <div className="section-card">
              <div className="info-row" style={{ alignItems: "center" }}>
                <div style={{ flex: 1 }}>
                  <div className="info-label" style={{ fontWeight: 600 }}>
                    Multi-monitor mode
                  </div>
                  <div style={{ fontSize: 12, color: "#8a9ab0", marginTop: 4 }}>
                    {arrangement === "join"
                      ? "Each monitor shows independent content; arrange them in the diagram."
                      : "All monitors mirror the same image (resolution forced to highest common)."}
                  </div>
                </div>
                <div style={{ display: "flex", gap: 0,
                              border: "1px solid #2a2a2a",
                              borderRadius: 6, overflow: "hidden" }}>
                  <button
                    className={`sub-nav-tab${arrangement === "join" ? " active" : ""}`}
                    style={{ padding: "6px 18px", borderRadius: 0,
                             borderRight: "1px solid #2a2a2a" }}
                    onClick={() => setArrangement("join")}>Join</button>
                  <button
                    className={`sub-nav-tab${arrangement === "clone" ? " active" : ""}`}
                    style={{ padding: "6px 18px", borderRadius: 0 }}
                    onClick={() => setArrangement("clone")}>Clone</button>
                </div>
              </div>

              {arrangement === "join" && (
                <div style={{ marginTop: 14 }}>
                  <MonitorArrangementCanvas
                    displays={displays}
                    layout={layout}
                    onLayoutChange={setLayout}
                  />
                  <p style={{ fontSize: 11, color: "#8a9ab0",
                              marginTop: 8, lineHeight: 1.5 }}>
                    Drag a monitor to reposition it.  Edges snap to
                    neighbours within {SNAP_PX}px.  Click <b>Apply</b>{" "}
                    below to push the layout to xrandr.
                  </p>
                </div>
              )}
            </div>
          </>
        )}

        <p className="section-title" style={{ marginTop: displays.length > 1 ? 24 : 0 }}>
          Display Settings
        </p>
        <div className="display-cards-row">
          {displays.map((d, i) => (
            <div
              key={d.name}
              className={`display-card${selected?.name === d.name ? " selected" : ""}`}
              onClick={() => selectDisplay(d)}
            >
              <div className="display-card-icon">
                <svg width="52" height="42" viewBox="0 0 52 42" fill="none" stroke="currentColor" strokeWidth="1.5">
                  <rect x="3" y="3" width="46" height="30" rx="3"/>
                  <line x1="18" y1="33" x2="34" y2="33"/>
                  <line x1="26" y1="33" x2="26" y2="39"/>
                  <line x1="18" y1="39" x2="34" y2="39"/>
                  <text x="26" y="22" textAnchor="middle" fontSize="12" fill="currentColor" stroke="none" fontWeight="bold">{i + 1}</text>
                </svg>
              </div>
              <div className="display-card-name">{d.name}</div>
              <div className="display-card-sub">{d.connector}</div>
              {d.gsync_compatible && (
                <div className="display-card-badge">G-SYNC Compatible</div>
              )}
              {d.primary && (
                <div className="display-card-badge" style={{ background: "#1a3a1a" }}>Primary</div>
              )}
            </div>
          ))}
        </div>

        {selected && (
          <>
            <p className="section-title" style={{ marginTop: 24 }}>
              Display Properties , {selected.name}
            </p>
            <div className="section-card">
              <div style={{ display: "grid",
                            gridTemplateColumns: "1fr 1fr 1fr",
                            gap: 20 }}>
                <div>
                  <div className="info-label" style={{ marginBottom: 8 }}>Resolution</div>
                  <select
                    className="gb-select"
                    value={selRes}
                    onChange={e => {
                      setSelRes(e.target.value);
                      const mode = selected.modes.find(m => m.resolution === e.target.value);
                      if (mode && mode.rates.length > 0) setSelRate(mode.rates[0]);
                    }}
                  >
                    {selected.modes.map(m => (
                      <option key={m.resolution} value={m.resolution}>
                        {m.resolution}{m.resolution === selected.current_mode ? " (current)" : ""}
                      </option>
                    ))}
                  </select>
                </div>

                <div>
                  <div className="info-label" style={{ marginBottom: 8 }}>Refresh Rate</div>
                  <select
                    className="gb-select"
                    value={selRate}
                    onChange={e => setSelRate(Number(e.target.value))}
                  >
                    {availableRates.map(r => (
                      <option key={r} value={r}>
                        {r.toFixed(0)} Hz{r === selected.current_rate ? " (current)" : ""}
                      </option>
                    ))}
                  </select>
                </div>

                <div>
                  <div className="info-label" style={{ marginBottom: 8 }}>Scale</div>
                  <select
                    className="gb-select"
                    value={scale}
                    onChange={e => setScale(Number(e.target.value))}
                  >
                    {[100, 125, 150, 175, 200].map(s => (
                      <option key={s} value={s}>{s}%</option>
                    ))}
                  </select>
                </div>
              </div>

              <div className="toggle-row" style={{ marginTop: 18 }}>
                <div>
                  <div className="toggle-label">Use as primary display</div>
                  <div className="toggle-desc">
                    Where panels, indicators, and most full-screen
                    applications appear.
                  </div>
                </div>
                <div className={`toggle${selected.primary ? " on" : ""}`}>
                  <div className="toggle-track" />
                  <div className="toggle-thumb" />
                </div>
              </div>

              {(() => {
                const capable = selected.gsync_compatible || selected.vrr;
                return (
                  <div className="toggle-row"
                       title={capable
                         ? undefined
                         : "Adaptive-sync capability not detected on this output. Toggling will still attempt to enable VRR via the driver / compositor."}
                       style={{ marginTop: 14,
                                cursor: "pointer",
                                opacity: capable ? 1 : 0.7 }}
                       onClick={async () => {
                         try {
                           const next = !selected.vrr;
                           const msg: string = await invoke("apply_display_vrr",
                             { name: selected.name, enabled: next });
                           // apply_display_vrr succeeding doesn't update `selected`/
                           // `displays` on its own , the toggle's checked state reads
                           // selected.vrr, so without this the switch visually stays
                           // off after a real, successful enable.
                           setSelected(s => s ? { ...s, vrr: next } : s);
                           setDisplays(ds => ds.map(d => d.name === selected.name ? { ...d, vrr: next } : d));
                           setApplyMsg(msg || `VRR ${next ? "enabled" : "disabled"} for ${selected.name}.`);
                         } catch (e: any) {
                           setApplyMsg("VRR toggle failed: " + (e?.message ?? e));
                         }
                       }}>
                    <div>
                      <div className="toggle-label">
                        Variable Refresh Rate (G-SYNC / FreeSync / Adaptive-Sync)
                      </div>
                      <div className="toggle-desc">
                        {capable
                          ? "Detected on this output. Reduces tearing and stutter for variable frame-rate games."
                          : "Not detected on this output. Toggle anyway to ask the driver / compositor."}
                      </div>
                    </div>
                    <div className={`toggle${selected.vrr ? " on" : ""}`}>
                      <div className="toggle-track" />
                      <div className="toggle-thumb" />
                    </div>
                  </div>
                );
              })()}

              <div className="toggle-row" style={{ marginTop: 14,
                                                    cursor: selected.primary
                                                      ? "not-allowed"
                                                      : "pointer",
                                                    opacity: selected.primary
                                                      ? 0.5 : 1 }}
                   onClick={async () => {
                     if (selected.primary || !selected.enabled) return;
                     try {
                       await invoke("apply_display_enabled",
                         { name: selected.name, enabled: false });
                       // Same class of bug as the VRR toggle: a successful
                       // call doesn't update local state on its own, and
                       // this toggle used to just render hardcoded "on"
                       // regardless of real state.
                       setSelected(s => s ? { ...s, enabled: false } : s);
                       setDisplays(ds => ds.map(d => d.name === selected.name ? { ...d, enabled: false } : d));
                       setApplyMsg(`Disabled ${selected.name}. Use Restore to re-enable.`);
                     } catch (e: any) {
                       setApplyMsg("Enable toggle failed: " + (e?.message ?? e));
                     }
                   }}>
                <div>
                  <div className="toggle-label">Display enabled</div>
                  <div className="toggle-desc">
                    {selected.primary
                      ? "Primary displays can't be turned off from here."
                      : selected.enabled
                        ? "Power off this display.  Re-enable from the bottom of this card."
                        : "Powered off , re-enable from the bottom of this card."}
                  </div>
                </div>
                <div className={`toggle${selected.enabled ? " on" : ""}`}>
                  <div className="toggle-track" />
                  <div className="toggle-thumb" />
                </div>
              </div>

              <div style={{ marginTop: 20 }}>
                <div className="info-label" style={{ marginBottom: 10 }}>Orientation</div>
                <div className="orientation-btns">
                  {ROTATIONS.map(r => (
                    <button
                      key={r.id}
                      className={`orientation-btn${rotation === r.id ? " active" : ""}`}
                      onClick={() => setRotation(r.id)}
                    >
                      {r.label}
                    </button>
                  ))}
                </div>
              </div>

              <div className="info-row" style={{ marginTop: 16 }}>
                <div className="info-label">Connector</div>
                <div className="info-value">{selected.connector}</div>
              </div>
              {selected.width_mm > 0 && (
                <div className="info-row">
                  <div className="info-label">Physical Size</div>
                  <div className="info-value">{selected.width_mm} × {selected.height_mm} mm</div>
                </div>
              )}
              {selected.gsync_compatible && (
                <div className="info-row">
                  <div className="info-label">Adaptive Sync</div>
                  <div className="info-value ok">G-SYNC Compatible / VRR</div>
                </div>
              )}
            </div>

            <div style={{ marginTop: 16, display: "flex", alignItems: "center", gap: 16 }}>
              <button className="btn-optimize" onClick={handleApply} disabled={applying}>
                {applying ? "Applying…" : "Apply"}
              </button>
              <button
                className="btn-component"
                onClick={async () => {
                  setApplying(true); setApplyMsg(null);
                  try {
                    const msg: string = await invoke("restore_all_displays");
                    setApplyMsg(msg);
                    // Bulk operation , re-fetch rather than guess which
                    // displays came back; same "success message but toggle
                    // never updates" bug as the single-display cases above.
                    // Not selectDisplay() , it resets applyMsg to null,
                    // which would wipe the message set right above.
                    const fresh = await invoke<DisplayInfo[]>("query_displays");
                    setDisplays(fresh);
                    const stillSelected = fresh.find(d => d.name === selected?.name) ?? fresh[0];
                    if (stillSelected) {
                      setSelected(stillSelected);
                      setSelRes(stillSelected.current_mode);
                      setSelRate(stillSelected.current_rate);
                    }
                  } catch (e: any) {
                    setApplyMsg("Restore failed: " + (e?.message ?? e));
                  }
                  setApplying(false);
                }}>
                Restore all displays
              </button>
              {applyMsg && <span style={{ fontSize: 12, color: "#76b900" }}>{applyMsg}</span>}
            </div>
          </>
        )}
      </div>
    </div>
      )}
    </>
  );
}
