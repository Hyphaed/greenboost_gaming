import { useState, useEffect, useRef } from "react";
import { invoke } from "@tauri-apps/api/core";
import type { GpuInfo, InstallStreamProps, Game, GameOverrides } from "../types";
import { Icon } from "../icons";
import { InstallStreamModal } from "../components/InstallStreamModal";

type ProfileTab = "overclock" | "fan-curve" | "profiles";
type FanCurvePoint = [number, number];
interface SavedProfile {
  name: string;
  power_limit_w: number | null;
  core_offset_mhz: number | null;
  mem_offset_mhz:  number | null;
  fan_curve:       FanCurvePoint[];
}

const DEFAULT_FAN_CURVE: FanCurvePoint[] = [
  [30, 25], [50, 35], [70, 55], [80, 75], [90, 100],
];

// Reserved profile name the fan-curve UI auto-saves+activates under so the
// REAL persistent daemon (gb-gaming-fan-daemon.service, polls every 2s,
// survives the Suite window closing) actually follows whatever curve was
// just applied here. Without this, "Apply"/a preset/Auto Tune only drove a
// foreground setInterval in this window , the moment the Suite closes (e.g.
// to play fullscreen), the curve stops being enforced with no warning,
// despite the UI saying "daemon running". Filtered out of every
// user-facing profile list , it's plumbing, not something to pick from a
// dropdown or overwrite by name collision.
const LIVE_PROFILE_NAME = "__live_fan_curve";
const visibleProfileNames = (names: string[]) => names.filter(n => n !== LIVE_PROFILE_NAME);

type FanPreset = {
  id: "silent" | "normal" | "extracool";
  label: string;
  icon: string;
  description: string;
  curve: FanCurvePoint[];
};
const FAN_PRESETS: FanPreset[] = [
  {
    id: "silent", label: "Silent", icon: "🔇",
    description: "Near-inaudible at idle. Ramps only above 65 °C. Best for desktop use and light gaming.",
    curve: [[30, 0], [55, 0], [65, 18], [75, 38], [85, 60], [92, 80]],
  },
  {
    id: "normal", label: "Normal", icon: "🎮",
    description: "Balanced ramp from 50 °C. Daily gaming sweet-spot , quiet at idle, responsive under load.",
    curve: [[30, 25], [50, 35], [70, 55], [80, 75], [90, 100]],
  },
  {
    id: "extracool", label: "Extra Cool", icon: "❄️",
    description: "Aggressive from 40 °C. Keeps temps low during sustained 4K / VR sessions. Louder but cooler.",
    curve: [[30, 35], [40, 45], [55, 65], [68, 85], [78, 95], [85, 100]],
  },
];

type GpuPreset = {
  id:    "quiet" | "balanced" | "performance";
  label: string;
  description: string;
  core_offset_mhz: number;
  mem_offset_mhz:  number;
  power_pct:       number;
  fan_curve:       FanCurvePoint[];
};

const GPU_PRESETS: GpuPreset[] = [
  {
    id: "quiet", label: "Quiet",
    description: "Mild undervolt, low fan, ~85% TDP. Good for desktop / light work.",
    core_offset_mhz: 0, mem_offset_mhz: 0, power_pct: 0.85,
    fan_curve: [[30, 20], [50, 30], [70, 45], [80, 60], [90, 80]],
  },
  {
    id: "balanced", label: "Balanced",
    description: "Stock clocks, sensible fan ramp, full TDP. The default for daily gaming.",
    core_offset_mhz: 0, mem_offset_mhz: 0, power_pct: 1.00,
    fan_curve: [[30, 25], [50, 35], [70, 55], [80, 75], [90, 100]],
  },
  {
    id: "performance", label: "Performance",
    description: "Modest core/memory offsets, aggressive fan curve, full TDP. For sustained AAA / VR.",
    core_offset_mhz: 100, mem_offset_mhz: 600, power_pct: 1.00,
    fan_curve: [[30, 35], [50, 50], [70, 75], [80, 90], [90, 100]],
  },
];

function FanCurveEditor({
  points, onChange, currentTempC, height = 320,
}: {
  points: FanCurvePoint[];
  onChange: (next: FanCurvePoint[]) => void;
  currentTempC?: number | null;
  height?: number;
}) {
  const W = 600, H = height;
  const PAD_L = 44, PAD_R = 18, PAD_T = 18, PAD_B = 34;
  const T_MIN = 20, T_MAX = 100;
  const P_MIN = 0,  P_MAX = 100;
  const PLOT_W = W - PAD_L - PAD_R;
  const PLOT_H = H - PAD_T - PAD_B;
  const MAX_POINTS = 10;

  const xs = (t: number) => PAD_L + ((t - T_MIN) / (T_MAX - T_MIN)) * PLOT_W;
  const ys = (p: number) => (H - PAD_B) - ((p - P_MIN) / (P_MAX - P_MIN)) * PLOT_H;

  const svgRef = useRef<SVGSVGElement | null>(null);
  const dragIdx = useRef<number | null>(null);
  const [selectedIdx, setSelectedIdx] = useState<number | null>(null);
  const [hint, setHint] = useState<string | null>(null);

  const eventToTP = (e: React.PointerEvent<SVGElement>): [number, number] => {
    const svg = svgRef.current;
    if (!svg) return [T_MIN, P_MIN];
    const rect = svg.getBoundingClientRect();
    const px = (e.clientX - rect.left) * (W / rect.width);
    const py = (e.clientY - rect.top)  * (H / rect.height);
    const t = T_MIN + ((px - PAD_L) / PLOT_W) * (T_MAX - T_MIN);
    const p = P_MAX - ((py - PAD_T) / PLOT_H) * (P_MAX - P_MIN);
    return [Math.round(t), Math.round(p)];
  };

  const handlePointerMove = (e: React.PointerEvent<SVGSVGElement>) => {
    const i = dragIdx.current;
    if (i === null) return;
    const [tRaw, pRaw] = eventToTP(e);
    const last = points.length - 1;
    const tMin = i === 0    ? T_MIN : points[i - 1][0] + 1;
    const tMax = i === last ? T_MAX : points[i + 1][0] - 1;
    const t = Math.max(tMin, Math.min(tMax, tRaw));
    const p = Math.max(P_MIN, Math.min(P_MAX, pRaw));
    if (t === points[i][0] && p === points[i][1]) return;
    onChange(points.map((pt, j) => (j === i ? [t, p] : pt)));
  };

  const startDrag = (i: number) => (e: React.PointerEvent<SVGElement>) => {
    e.stopPropagation();
    (e.currentTarget as Element).setPointerCapture(e.pointerId);
    dragIdx.current = i;
    setSelectedIdx(i);
  };
  const endDrag = (e: React.PointerEvent<SVGSVGElement>) => {
    if (dragIdx.current === null) return;
    try { (e.target as Element).releasePointerCapture?.(e.pointerId); } catch {}
    dragIdx.current = null;
  };

  const handlePlotClick = (e: React.PointerEvent<SVGRectElement>) => {
    if (dragIdx.current !== null) return;
    if (points.length >= MAX_POINTS) {
      setHint(`Maximum of ${MAX_POINTS} points reached.`);
      setTimeout(() => setHint(null), 1800);
      return;
    }
    const [t, p] = eventToTP(e);
    if (points.some(([pt]) => pt === t)) return;
    const idx = points.findIndex(([pt]) => pt > t);
    const next: FanCurvePoint[] = idx < 0
      ? [...points, [t, p]]
      : [...points.slice(0, idx), [t, p], ...points.slice(idx)];
    onChange(next);
    setSelectedIdx(idx < 0 ? next.length - 1 : idx);
  };

  const deletePoint = (i: number) => {
    if (points.length <= 2) {
      setHint("Curve needs at least 2 points.");
      setTimeout(() => setHint(null), 1800);
      return;
    }
    const next = points.filter((_, j) => j !== i);
    onChange(next);
    setSelectedIdx(null);
  };

  const handleKey = (e: React.KeyboardEvent<SVGSVGElement>) => {
    if ((e.key === "Delete" || e.key === "Backspace") && selectedIdx !== null) {
      deletePoint(selectedIdx);
      e.preventDefault();
    }
  };

  const interpDuty = (t: number): number => {
    if (points.length === 0) return 0;
    if (t <= points[0][0]) return points[0][1];
    if (t >= points[points.length - 1][0]) return points[points.length - 1][1];
    for (let i = 0; i < points.length - 1; i++) {
      const [t0, p0] = points[i];
      const [t1, p1] = points[i + 1];
      if (t >= t0 && t <= t1) {
        const f = (t - t0) / (t1 - t0);
        return Math.round(p0 + f * (p1 - p0));
      }
    }
    return points[points.length - 1][1];
  };

  const grid: React.ReactElement[] = [];
  for (let t = T_MIN; t <= T_MAX; t += 10) {
    grid.push(<line key={`vt${t}`} x1={xs(t)} y1={PAD_T} x2={xs(t)} y2={H - PAD_B}
                    stroke="#262626" strokeWidth={1} />);
  }
  for (let p = P_MIN; p <= P_MAX; p += 10) {
    grid.push(<line key={`hp${p}`} x1={PAD_L} y1={ys(p)} x2={W - PAD_R} y2={ys(p)}
                    stroke={p % 50 === 0 ? "#2e2e2e" : "#222"}
                    strokeWidth={1} />);
  }

  const pathD = points
    .map((p, i) => `${i === 0 ? "M" : "L"} ${xs(p[0]).toFixed(1)} ${ys(p[1]).toFixed(1)}`)
    .join(" ");

  const liveT = currentTempC != null && currentTempC >= T_MIN && currentTempC <= T_MAX
    ? currentTempC : null;
  const liveDuty = liveT != null ? interpDuty(liveT) : null;

  return (
    <div>
      <div style={{ display: "flex", alignItems: "center",
                    justifyContent: "space-between",
                    fontSize: 12, color: "#9a9a9a",
                    padding: "0 2px 8px" }}>
        <div>
          {liveT != null && liveDuty != null ? (
            <>
              <span style={{ color: "#ffb347" }}>● </span>
              Current: <strong style={{ color: "#e6e6e6" }}>{liveT}°C</strong>
              {" → target "}
              <strong style={{ color: "#ffb347" }}>{liveDuty}%</strong>
            </>
          ) : (
            <span>No live temperature available.</span>
          )}
        </div>
        <div style={{ display: "flex", alignItems: "center", gap: 12 }}>
          <span>{points.length} points</span>
          <span style={{ color: "#8a9ab0" }}>
            Click curve to add · drag to edit · Del to remove
          </span>
        </div>
      </div>

      <svg
        ref={svgRef}
        viewBox={`0 0 ${W} ${H}`}
        width="100%" height={H}
        tabIndex={0}
        onKeyDown={handleKey}
        onPointerMove={handlePointerMove}
        onPointerUp={endDrag}
        onPointerLeave={endDrag}
        style={{ display: "block", borderRadius: 6,
                 background: "#1a1a1a",
                 outline: "none",
                 touchAction: "none",
                 userSelect: "none" }}
      >
        <rect x={PAD_L} y={PAD_T} width={PLOT_W} height={PLOT_H}
              fill="none" stroke="#333" strokeWidth={1} />
        {grid}
        <rect x={PAD_L} y={PAD_T} width={PLOT_W} height={PLOT_H}
              fill="transparent"
              onPointerDown={handlePlotClick}
              style={{ cursor: "crosshair" }} />

        {[T_MIN, 30, 40, 50, 60, 70, 80, 90, T_MAX].map(t => (
          <text key={`xl${t}`} x={xs(t)} y={H - PAD_B + 16}
                fill="#7a7a7a" fontSize={10} textAnchor="middle">
            {t}°C
          </text>
        ))}
        {[0, 25, 50, 75, 100].map(p => (
          <text key={`yl${p}`} x={PAD_L - 8} y={ys(p) + 3}
                fill="#7a7a7a" fontSize={10} textAnchor="end">{p}%</text>
        ))}
        <text x={PAD_L - 28} y={PAD_T + 4} fill="#7a7a7a"
              fontSize={10} textAnchor="end">Duty</text>

        {liveT != null && liveDuty != null && (
          <g pointerEvents="none">
            <line x1={xs(liveT)} y1={PAD_T} x2={xs(liveT)} y2={H - PAD_B}
                  stroke="#ffb347" strokeWidth={1}
                  strokeDasharray="4 4" opacity={0.65} />
            <circle cx={xs(liveT)} cy={ys(liveDuty)} r={4}
                    fill="#ffb347" stroke="#1a1a1a" strokeWidth={1.5} />
            {(() => {
              const lx = xs(liveT);
              const ly = ys(liveDuty);
              const flip = lx > W - PAD_R - 80;
              return (
                <text x={lx + (flip ? -8 : 8)} y={ly - 8}
                      fill="#ffb347" fontSize={11}
                      textAnchor={flip ? "end" : "start"}>
                  {liveT}°C → {liveDuty}%
                </text>
              );
            })()}
          </g>
        )}

        <path d={pathD} stroke="#76b900" strokeWidth={2.5}
              fill="none" strokeLinecap="round" strokeLinejoin="round" />

        {points.map(([t, p], i) => {
          const sel = i === selectedIdx;
          return (
            <g key={i}>
              <circle
                cx={xs(t)} cy={ys(p)} r={sel ? 8 : 6}
                fill="#76b900" stroke="#1a1a1a" strokeWidth={2}
                style={{ cursor: "grab" }}
                onPointerDown={startDrag(i)}
                onPointerUp={(e) => {
                  if (dragIdx.current === i) {
                    try { (e.currentTarget as Element).releasePointerCapture(e.pointerId); } catch {}
                    dragIdx.current = null;
                  }
                  setSelectedIdx(i);
                }}
              />
              {sel && points.length > 2 && (
                <g
                  transform={`translate(${xs(t) + 12}, ${ys(p) - 12})`}
                  style={{ cursor: "pointer" }}
                  onPointerDown={(e) => {
                    e.stopPropagation();
                    deletePoint(i);
                  }}
                >
                  <circle r={8} fill="#2a2a2a" stroke="#e05252" strokeWidth={1.5}/>
                  <line x1={-3.5} y1={-3.5} x2={3.5} y2={3.5}
                        stroke="#e05252" strokeWidth={1.6} strokeLinecap="round"/>
                  <line x1={-3.5} y1={3.5} x2={3.5} y2={-3.5}
                        stroke="#e05252" strokeWidth={1.6} strokeLinecap="round"/>
                </g>
              )}
              {sel && (
                <text x={xs(t)} y={ys(p) - 16}
                      fill="#e6e6e6" fontSize={11}
                      textAnchor="middle" pointerEvents="none">
                  {t}°C / {p}%
                </text>
              )}
            </g>
          );
        })}
      </svg>

      {hint && (
        <div style={{ marginTop: 6, fontSize: 11, color: "#e8a000" }}>{hint}</div>
      )}
    </div>
  );
}

export function GpuProfileView() {
  const [tab, setTab] = useState<ProfileTab>("overclock");
  const [gpu, setGpu] = useState<GpuInfo | null>(null);
  const [core, setCore] = useState(0);
  const [mem, setMem]   = useState(0);
  const [power, setPower] = useState(0);
  const [fanAuto, setFanAuto] = useState(true);
  const fanDaemonRef = useRef<ReturnType<typeof setInterval> | null>(null);
  const [fanCurve, setFanCurve] = useState<FanCurvePoint[]>(DEFAULT_FAN_CURVE);
  const [applyMsg, setApplyMsg] = useState<string | null>(null);
  const [applying, setApplying] = useState(false);

  const [profileNames, setProfileNames] = useState<string[]>([]);
  const [profileNameInput, setProfileNameInput] = useState("");
  const [profileMsg, setProfileMsg] = useState<string | null>(null);
  const [activeProfile, setActiveProfile] = useState<string | null>(null);
  const [selectedProfile, setSelectedProfile] = useState<string | null>(null);

  // Bind-to-game state
  const [allGames, setAllGames] = useState<Game[]>([]);
  const [boundAppids, setBoundAppids] = useState<string[]>([]);
  const [bindTarget, setBindTarget] = useState<string>("");
  const [bindingsLoading, setBindingsLoading] = useState(false);

  useEffect(() => {
    const fetchGpu = () => {
      invoke<GpuInfo>("get_gpu").then(g => setGpu(g)).catch(console.error);
    };
    fetchGpu();
    invoke<GpuInfo>("get_gpu").then(g => {
      const parsedOffset = parseInt(g.core_clock_offset) || 0;
      setCore(parsedOffset);
      const parsedMem = parseInt(g.mem_clock_offset) || 0;
      setMem(parsedMem);
      const parsedPower = parseInt(g.power_limit) || g.power_limit_max;
      setPower(parsedPower || 200);
    }).catch(console.error);
    const id = setInterval(fetchGpu, 2000);
    return () => clearInterval(id);
  }, []);

  useEffect(() => {
    invoke<string[]>("list_gpu_profiles").then(ns => setProfileNames(visibleProfileNames(ns))).catch(console.error);
    invoke<string | null>("get_active_gpu_profile")
      .then(setActiveProfile)
      .catch(console.error);
  }, [tab]);

  // Load all games once when the profiles tab is opened
  useEffect(() => {
    if (tab !== "profiles") return;
    invoke<Game[]>("get_games")
      .then(games => {
        setAllGames(games);
      })
      .catch(console.error);
  }, [tab]);

  // When the selected profile changes, batch-load overrides to find bound games
  useEffect(() => {
    if (!selectedProfile || tab !== "profiles") {
      setBoundAppids([]);
      setBindTarget("");
      return;
    }
    setBindingsLoading(true);
    const gamesWithAppid = allGames.filter(g => g.appid);
    if (gamesWithAppid.length === 0) {
      setBoundAppids([]);
      setBindingsLoading(false);
      return;
    }
    Promise.all(
      gamesWithAppid.map(g =>
        invoke<GameOverrides>("get_game_overrides", { appid: g.appid })
          .then(ov => ({ appid: g.appid!, bound: ov.gpu_profile === selectedProfile }))
          .catch(() => ({ appid: g.appid!, bound: false }))
      )
    ).then(results => {
      setBoundAppids(results.filter(r => r.bound).map(r => r.appid));
      setBindTarget("");
    }).finally(() => setBindingsLoading(false));
  }, [selectedProfile, allGames, tab]);

  const setActive = async (name: string | null) => {
    setProfileMsg(name ? `Activating "${name}"…` : "Deactivating…");
    try {
      await invoke("set_active_gpu_profile", { name });
      setActiveProfile(name);
      setProfileMsg(name
        ? `"${name}" is now active. Fan daemon will follow this curve.`
        : "No active profile. Fan handed back to driver.");
    } catch (e: any) {
      setProfileMsg("Failed: " + (e?.message ?? e));
    }
  };

  const [autoTuning, setAutoTuning] = useState(false);
  const [autoTuneMsg, setAutoTuneMsg] = useState<string | null>(null);
  const [activeFanPreset, setActiveFanPreset] = useState<FanPreset["id"] | null>(null);

  const [fanTestPhase, setFanTestPhase] = useState<null | number | "done" | string>(null);
  const fanTestRunning = fanTestPhase !== null && fanTestPhase !== "done"
                      && (typeof fanTestPhase !== "string" || !fanTestPhase.startsWith("error:"));

  const [coolbitsModal, setCoolbitsModal] = useState<null | InstallStreamProps>(null);

  const handleAutoTune = async () => {
    setAutoTuning(true); setAutoTuneMsg(null);
    try {
      // Full hardware-topology-aware recommendation from the backend
      // (reads GPU name, SM version, VRAM, TDP, NVML max clocks).
      const rec = await invoke<{
        label: string;
        core_offset_mhz: number;
        mem_offset_mt: number;
        power_w: number;
        lock_clocks_mhz: number | null;
        fan_curve: [number, number][];
        recommended_shader_threads: number;
        numa_nodes: number;
        physical_cores: number;
        logical_cores: number;
        l3_cache_kb: number;
        notes: string[];
      }>("gpu_auto_tune");

      setCore(rec.core_offset_mhz);
      setMem(rec.mem_offset_mt);
      setPower(rec.power_w);
      setFanCurve(rec.fan_curve.map(([t, p]) => [t, p] as FanCurvePoint));

      // Apply topology-derived shader thread recommendation to global settings.
      if (rec.recommended_shader_threads > 0) {
        try {
          const gs = await invoke<Record<string, unknown>>("get_global_settings");
          await invoke("save_global_settings", {
            settings: { ...gs, shader_threads: rec.recommended_shader_threads },
          });
        } catch { /* non-fatal , GPU OC still applied */ }
      }

      const msg: string = await invoke("apply_gpu",
        { core: rec.core_offset_mhz, mem: rec.mem_offset_mt, power: rec.power_w });

      const noteStr = rec.notes.length ? `\n${rec.notes.join(" | ")}` : "";
      setAutoTuneMsg(`Auto Tune , ${rec.label}: ${msg}${noteStr}`);
    } catch (e: any) {
      setAutoTuneMsg("Auto Tune error: " + (e?.message ?? e));
    }
    setAutoTuning(false);
  };

  const startFanDaemon = (curve: typeof fanCurve) => {
    if (fanDaemonRef.current) clearInterval(fanDaemonRef.current);
    const tick = () => {
      invoke<string>("apply_fan_curve_cmd", { points: curve })
        .then(msg  => setApplyMsg(msg))
        .catch((e: any) => setApplyMsg("Fan ctrl error: " + (e?.message ?? e)));
    };
    tick();
    fanDaemonRef.current = setInterval(tick, 4000);
  };

  const stopFanDaemon = () => {
    if (fanDaemonRef.current) { clearInterval(fanDaemonRef.current); fanDaemonRef.current = null; }
  };

  // Save + activate the reserved LIVE_PROFILE_NAME so gb-gaming-fan-daemon
  // (the actual persistent service) starts following `curve` too , not just
  // the foreground setInterval startFanDaemon() drives. Keeps current
  // core/mem/power offsets rather than resetting them, since this fires
  // from fan-only actions (preset, auto-tune) as well as the full Apply.
  // Returns whether the persistent gb-gaming-fan-daemon actually picked up
  // the curve. Previously this only console.error'd on failure while every
  // caller unconditionally showed "daemon running" , the foreground
  // setInterval (startFanDaemon) makes the fan respond immediately either
  // way, so the failure was invisible until the app was closed and the
  // background daemon reverted to whatever it had before.
  const activateLiveProfile = async (curve: FanCurvePoint[]): Promise<boolean> => {
    try {
      await invoke("save_gpu_profile", {
        profile: {
          name: LIVE_PROFILE_NAME,
          power_limit_w:   power,
          core_offset_mhz: core,
          mem_offset_mhz:  mem,
          fan_curve:       curve,
        } as SavedProfile,
      });
      await invoke("set_active_gpu_profile", { name: LIVE_PROFILE_NAME });
      return true;
    } catch (e) {
      console.error("activateLiveProfile failed", e);
      return false;
    }
  };

  const applyFanPreset = (preset: FanPreset) => {
    setFanCurve(preset.curve);
    setFanAuto(false);
    setSelectedProfile(null);
    setActiveFanPreset(preset.id);
    startFanDaemon(preset.curve);
    setApplyMsg(`Fan preset "${preset.label}" active.`);
    activateLiveProfile(preset.curve).then(ok => {
      setApplyMsg(ok
        ? `Fan preset "${preset.label}" active , daemon running.`
        : `Fan preset "${preset.label}" active this session, but saving it `
          + `for the background daemon failed , it won't persist after you `
          + `close the app.`);
    });
  };

  const handleFanAutoTune = async () => {
    try {
      const rec = await invoke<{ fan_curve: [number, number][]; label: string }>("gpu_auto_tune");
      const curve = rec.fan_curve.map(([t, p]) => [t, p] as FanCurvePoint);
      setFanCurve(curve);
      setFanAuto(false);
      setSelectedProfile(null);
      setActiveFanPreset(null);
      startFanDaemon(curve);
      setApplyMsg(`Fan Auto Tune applied for ${rec.label}.`);
      activateLiveProfile(curve).then(ok => {
        setApplyMsg(ok
          ? `Fan Auto Tune applied for ${rec.label} , daemon running.`
          : `Fan Auto Tune applied for ${rec.label} this session, but saving `
            + `it for the background daemon failed , it won't persist after `
            + `you close the app.`);
      });
    } catch {
      // Fallback to SM-based preset if backend is unavailable.
      if (!gpu) return;
      const sm = parseFloat(gpu.compute_cap || "0");
      const preset = sm >= 8.9
        ? FAN_PRESETS.find(p => p.id === "extracool")!
        : FAN_PRESETS.find(p => p.id === "normal")!;
      applyFanPreset(preset);
    }
  };

  const handleFanTest = async () => {
    if (fanTestRunning) return;
    setFanTestPhase(30);
    const delay = (ms: number) => new Promise(r => setTimeout(r, ms));
    const steps: Array<[number, number]> = [[30, 2200], [60, 2200], [100, 2500]];
    for (const [speed, wait] of steps) {
      setFanTestPhase(speed);
      try {
        await invoke("fan_manual", { speed });
      } catch (e: any) {
        const msg: string = e?.message ?? String(e);
        if (msg.startsWith("NEEDS_COOLBITS:")) {
          setFanTestPhase(null);
          invoke("fan_auto").catch(() => {});
          setCoolbitsModal({
            title:   "Enable Fan Control (Coolbits)",
            command: "enable_fan_control_streaming",
            onDone:  (ok) => {
              setCoolbitsModal(null);
              if (ok) {
                // enable_fan_control_streaming exited 0 , NVML helper is
                // available (or Coolbits was just written).  Retry the test
                // so the button reflects the real outcome.
                setFanTestPhase(null);
                setTimeout(() => handleFanTest(), 200);
              } else {
                setFanTestPhase("error:Could not enable fan control automatically. See modal log for manual instructions.");
              }
            },
          });
          return;
        }
        setFanTestPhase("error:" + msg);
        invoke("fan_auto").catch(() => {});
        return;
      }
      await delay(wait);
    }
    setFanTestPhase("restoring");
    try { await invoke("fan_auto"); } catch (_) {}
    setFanTestPhase("done");
    setTimeout(() => setFanTestPhase(p => p === "done" ? null : p), 5000);
  };

  const liveFanPct: number | null = (() => {
    if (!gpu) return null;
    const s = gpu.fan_speed;
    if (!s || s.toLowerCase().includes("idle")) return 0;
    const n = parseInt(s);
    return isNaN(n) ? null : n;
  })();

  const tempNum = gpu ? parseInt(gpu.temp) : 0;
  const tempColor = tempNum >= 85 ? "#ff4d4d" : tempNum >= 70 ? "#ffb347" : "#76b900";

  const handleApply = async () => {
    setApplying(true); setApplyMsg(null);
    try {
      const msg: string = await invoke("apply_gpu", { core, mem, power });
      if (fanAuto) {
        stopFanDaemon();
        await invoke("fan_auto");
        // Deactivate the live profile too , otherwise the persistent
        // daemon keeps re-applying the last curve every 2s, fighting
        // this "Auto" choice the moment this window loses focus.
        invoke("set_active_gpu_profile", { name: null }).catch(() => {});
        setApplyMsg(msg + " · Fan: auto");
      } else {
        startFanDaemon(fanCurve);
        setApplyMsg(msg + " · Fan curve active");
        activateLiveProfile(fanCurve).then(ok => {
          setApplyMsg(ok
            ? msg + " · Fan curve active (daemon will keep following it)"
            : msg + " · Fan curve active this session, but saving it for "
              + "the background daemon failed , it won't persist after you "
              + "close the app.");
        });
      }
    } catch (e: any) { setApplyMsg("Error: " + (e?.message ?? e)); }
    setApplying(false);
  };

  const saveCurrentProfile = async () => {
    const name = profileNameInput.trim();
    if (!name) { setProfileMsg("Enter a profile name first."); return; }
    setProfileMsg("Saving…");
    try {
      const payload: SavedProfile = {
        name,
        power_limit_w:   power,
        core_offset_mhz: core,
        mem_offset_mhz:  mem,
        fan_curve:       fanCurve,
      };
      await invoke("save_gpu_profile", { profile: payload });
      setProfileMsg(`Saved as "${name}".`);
      setProfileNames(visibleProfileNames(await invoke("list_gpu_profiles")));
    } catch (e: any) {
      setProfileMsg("Save failed: " + (e?.message ?? e));
    }
  };

  const loadProfile = async (name: string) => {
    setProfileMsg("Loading…");
    try {
      const p: SavedProfile | null = await invoke("load_gpu_profile", { name });
      if (!p) { setProfileMsg(`Profile "${name}" not found.`); return; }
      if (p.core_offset_mhz !== null) setCore(p.core_offset_mhz);
      if (p.mem_offset_mhz  !== null) setMem(p.mem_offset_mhz);
      if (p.power_limit_w   !== null) setPower(Math.round(p.power_limit_w));
      if (p.fan_curve.length > 0) setFanCurve(p.fan_curve);
      setProfileMsg(`Loaded "${name}". Click Apply to push to GPU.`);
    } catch (e: any) {
      setProfileMsg("Load failed: " + (e?.message ?? e));
    }
  };

  const bindGame = async (appid: string) => {
    if (!selectedProfile) return;
    try {
      const current = await invoke<GameOverrides>("get_game_overrides", { appid });
      await invoke("save_game_overrides", {
        appid,
        overrides: { ...current, gpu_profile: selectedProfile },
      });
      setBoundAppids(prev => prev.includes(appid) ? prev : [...prev, appid]);
      setBindTarget("");
    } catch (e: any) {
      setProfileMsg("Bind failed: " + (e?.message ?? e));
    }
  };

  const unbindGame = async (appid: string) => {
    if (!selectedProfile) return;
    try {
      const current = await invoke<GameOverrides>("get_game_overrides", { appid });
      await invoke("save_game_overrides", {
        appid,
        overrides: { ...current, gpu_profile: "" },
      });
      setBoundAppids(prev => prev.filter(id => id !== appid));
    } catch (e: any) {
      setProfileMsg("Unbind failed: " + (e?.message ?? e));
    }
  };

  const minPower = gpu?.power_limit_min ?? 50;
  const maxPower = gpu?.power_limit_max ?? 400;

  return (
    <>
      <div className="sub-nav">
        <button className={`sub-nav-tab${tab === "overclock" ? " active" : ""}`}
                onClick={() => setTab("overclock")}>Overclocking</button>
        <button className={`sub-nav-tab${tab === "fan-curve" ? " active" : ""}`}
                onClick={() => setTab("fan-curve")}>Fan Curve</button>
        <button className={`sub-nav-tab${tab === "profiles" ? " active" : ""}`}
                onClick={() => setTab("profiles")}>Profiles</button>
      </div>

      <div className="content-scroll">
        <div style={{ maxWidth: 720, margin: "0 auto" }}>

          {gpu && (
            <div className="gpu-stats-bar">
              <div className="gpu-stat">
                <Icon.Thermometer />
                <span style={{ color: tempColor }}>{gpu.temp}</span>
                <span className="gpu-stat-label">Temp</span>
              </div>
              <div className="gpu-stat">
                <Icon.Power />
                <span>{gpu.power_usage}</span>
                <span className="gpu-stat-label">Power</span>
              </div>
              <div className="gpu-stat">
                <Icon.Fan />
                <span>{gpu.fan_speed}</span>
                <span className="gpu-stat-label">Fan</span>
              </div>
              <div className="gpu-stat" style={{ flex: 2 }}>
                <Icon.Cpu />
                <span style={{ fontSize: 12 }}>{gpu.name}</span>
                {gpu.compute_cap && gpu.compute_cap !== "," && (
                  <span style={{
                    fontSize: 10, padding: "1px 6px",
                    background: "rgba(118,185,0,0.15)", color: "#76b900",
                    borderRadius: 8, fontWeight: 600, marginLeft: 4,
                  }}>SM {gpu.compute_cap}</span>
                )}
                <span className="gpu-stat-label">GPU</span>
              </div>
            </div>
          )}

          {tab === "overclock" && (
            <>
              <p className="section-title">Presets</p>
              <div className="section-card">
                <div style={{ display: "grid",
                              gridTemplateColumns: "repeat(3, 1fr)",
                              gap: 12 }}>
                  {GPU_PRESETS.map(p => (
                    <div
                      key={p.id}
                      role="button"
                      tabIndex={0}
                      onClick={() => {
                        setCore(p.core_offset_mhz);
                        setMem(p.mem_offset_mhz);
                        setPower(Math.round(maxPower * p.power_pct));
                        setFanCurve(p.fan_curve);
                        setApplyMsg(`Preset "${p.label}" loaded , click Apply to push to GPU.`);
                      }}
                      onKeyDown={e => {
                        if (e.key === "Enter" || e.key === " ") {
                          (e.currentTarget as HTMLDivElement).click();
                          e.preventDefault();
                        }
                      }}
                      style={{
                        display: "flex", flexDirection: "column", gap: 6,
                        padding: "14px 16px", background: "#1a1a1a",
                        border: "1px solid #2a2a2a", borderRadius: 6,
                        cursor: "pointer",
                        transition: "background 120ms ease, border-color 120ms ease",
                        boxSizing: "border-box", minWidth: 0, width: "100%",
                      }}
                      onMouseEnter={e => {
                        const t = e.currentTarget;
                        t.style.background   = "rgba(118,185,0,0.06)";
                        t.style.borderColor  = "#76b900";
                      }}
                      onMouseLeave={e => {
                        const t = e.currentTarget;
                        t.style.background   = "#1a1a1a";
                        t.style.borderColor  = "#2a2a2a";
                      }}
                    >
                      <div style={{ color: "#76b900", fontWeight: 600, fontSize: 13 }}>
                        {p.label}
                      </div>
                      <div style={{ color: "#9a9a9a", fontSize: 11,
                                    lineHeight: 1.5, whiteSpace: "normal" }}>
                        {p.description}
                      </div>
                    </div>
                  ))}
                </div>
              </div>

              <div style={{ marginBottom: 20, display: "flex",
                            alignItems: "center", gap: 16 }}>
                <button
                  className="btn-optimize"
                  onClick={handleAutoTune}
                  disabled={autoTuning || !gpu}
                  style={{ display: "flex", alignItems: "center", gap: 8 }}
                >
                  <Icon.Zap />
                  {autoTuning ? "Tuning…" : "Auto Tune"}
                </button>
                <div style={{ fontSize: 12, color: "#9a9a9a", lineHeight: 1.5 }}>
                  Automatically detects your GPU generation and applies the
                  optimal core / memory offsets and fan curve.
                </div>
              </div>
              {autoTuneMsg && (
                <div style={{
                  fontSize: 12,
                  color: autoTuneMsg.startsWith("Auto Tune error") ? "#e05252" : "#76b900",
                  marginBottom: 12, padding: "8px 12px",
                  background: autoTuneMsg.startsWith("Auto Tune error")
                    ? "rgba(224,82,82,0.08)" : "rgba(118,185,0,0.08)",
                  borderRadius: 6,
                  border: `1px solid ${autoTuneMsg.startsWith("Auto Tune error") ? "rgba(224,82,82,0.3)" : "rgba(118,185,0,0.3)"}`,
                }}>
                  {autoTuneMsg}
                </div>
              )}

              <p className="section-title">Overclocking</p>
              <div className="section-card">
                <div className="slider-row">
                  <div className="slider-label">
                    <span>Core Clock Offset</span>
                    <span className="slider-value">{core > 0 ? "+" : ""}{core} MHz</span>
                  </div>
                  <input type="range" min={-200} max={200} step={5} value={core}
                    onChange={e => setCore(Number(e.target.value))}
                    className="gb-slider" />
                  <div className="slider-bounds">
                    <span>-200 MHz</span><span>+200 MHz</span>
                  </div>
                </div>
                <div className="slider-row" style={{ marginTop: 16 }}>
                  <div className="slider-label">
                    <span>Memory Transfer Rate Offset</span>
                    <span className="slider-value">{mem > 0 ? "+" : ""}{mem} MHz</span>
                  </div>
                  <input type="range" min={-1000} max={2000} step={25} value={mem}
                    onChange={e => setMem(Number(e.target.value))}
                    className="gb-slider" />
                  <div className="slider-bounds">
                    <span>-1000 MHz</span><span>+2000 MHz</span>
                  </div>
                </div>
              </div>

              <p className="section-title">Power Limit</p>
              <div className="section-card">
                <div className="slider-row">
                  <div className="slider-label">
                    <span>TDP Limit</span>
                    <span className="slider-value">{power} W</span>
                  </div>
                  <input type="range" min={minPower} max={maxPower} step={5}
                    value={power}
                    onChange={e => setPower(Number(e.target.value))}
                    className="gb-slider" />
                  <div className="slider-bounds">
                    <span>{minPower} W</span><span>{maxPower} W</span>
                  </div>
                </div>
              </div>

              <div style={{ marginTop: 20, display: "flex",
                            alignItems: "center", gap: 16 }}>
                <button className="btn-optimize"
                        onClick={handleApply} disabled={applying}>
                  {applying ? "Applying…" : "Apply Profile"}
                </button>
                {applyMsg && (
                  <span style={{ fontSize: 12, color: "#76b900" }}>{applyMsg}</span>
                )}
              </div>
            </>
          )}

          {tab === "fan-curve" && (
            <>
              <p className="section-title">Fan Profile</p>

              <div style={{ display: "flex", alignItems: "center",
                            gap: 10, marginBottom: 16 }}>
                <button
                  className="btn-optimize"
                  onClick={handleFanAutoTune}
                  disabled={!gpu || fanTestRunning}
                  style={{ display: "flex", alignItems: "center", gap: 8, flexShrink: 0 }}
                >
                  <Icon.Zap />
                  Auto Tune
                </button>

                <button
                  className="btn-component"
                  onClick={handleFanTest}
                  disabled={fanTestRunning}
                  title="Ramp fans 30 % → 60 % → 100 % → auto to verify hardware control works"
                  style={{
                    display: "flex", alignItems: "center", gap: 7,
                    flexShrink: 0, padding: "8px 14px",
                    borderColor: fanTestPhase === "done" ? "#76b900"
                               : typeof fanTestPhase === "string" && fanTestPhase.startsWith("error:")
                                 ? "#e05252" : undefined,
                    color: fanTestPhase === "done" ? "#76b900"
                         : typeof fanTestPhase === "string" && fanTestPhase.startsWith("error:")
                           ? "#e05252" : undefined,
                  }}
                >
                  <span style={{
                    display: "inline-block",
                    animation: fanTestRunning ? "spin 0.7s linear infinite" : "none",
                  }}>
                    <Icon.Fan />
                  </span>
                  {fanTestRunning && typeof fanTestPhase === "number"
                    ? `Testing ${fanTestPhase}%…`
                    : fanTestPhase === "done" ? "✓ Fans OK"
                    : typeof fanTestPhase === "string" && fanTestPhase.startsWith("error:")
                      ? "✗ Test failed"
                      : "Test Fans"}
                </button>

                <div style={{
                  display: "flex", alignItems: "center", gap: 10,
                  padding: "8px 14px",
                  background: "#1a1a1a", border: "1px solid #2a2a2a",
                  borderRadius: 8, flex: 1, minWidth: 0,
                }}>
                  <span style={{ fontSize: 11, color: "#9a9ab0",
                                 flexShrink: 0, whiteSpace: "nowrap" }}>
                    Fan speed
                  </span>
                  <div style={{ flex: 1, height: 6, background: "#2a2a2a",
                                borderRadius: 99, overflow: "hidden" }}>
                    <div style={{
                      height: "100%",
                      width: `${liveFanPct ?? 0}%`,
                      background: (liveFanPct ?? 0) >= 80 ? "#ff6b6b"
                                : (liveFanPct ?? 0) >= 50 ? "#ffb347"
                                : "#76b900",
                      borderRadius: 99,
                      transition: "width 800ms ease, background 400ms ease",
                    }} />
                  </div>
                  <span style={{
                    fontSize: 15, fontWeight: 700, minWidth: 42,
                    textAlign: "right", flexShrink: 0,
                    color: (liveFanPct ?? 0) >= 80 ? "#ff6b6b"
                         : (liveFanPct ?? 0) >= 50 ? "#ffb347"
                         : "#76b900",
                  }}>
                    {liveFanPct !== null ? `${liveFanPct}%` : ","}
                  </span>
                </div>
              </div>

              {typeof fanTestPhase === "string" && fanTestPhase.startsWith("error:") && (() => {
                const msg = fanTestPhase.slice(6);
                const isInfo = msg.startsWith("Fan control enabled");
                return (
                  <div style={{
                    marginBottom: 12, padding: "10px 14px",
                    background: isInfo ? "rgba(118,185,0,0.08)" : "rgba(224,82,82,0.08)",
                    border: `1px solid ${isInfo ? "rgba(118,185,0,0.3)" : "rgba(224,82,82,0.3)"}`,
                    borderRadius: 6, fontSize: 12,
                    color: isInfo ? "#76b900" : "#e05252",
                    lineHeight: 1.6,
                  }}>
                    {isInfo
                      ? <><strong>Fan control configured.</strong>{" "}{msg}</>
                      : <><strong>Fan test failed:</strong>{" "}{msg}</>
                    }
                    <div style={{ marginTop: 8, display: "flex", gap: 8 }}>
                      <button
                        style={{
                          fontSize: 11, padding: "4px 10px",
                          background: "transparent",
                          border: `1px solid ${isInfo ? "#76b900" : "#e05252"}`,
                          borderRadius: 4,
                          color: isInfo ? "#76b900" : "#e05252",
                          cursor: "pointer",
                        }}
                        onClick={() => setFanTestPhase(null)}
                      >Dismiss</button>
                    </div>
                  </div>
                );
              })()}

              {coolbitsModal && <InstallStreamModal {...coolbitsModal} />}

              <div style={{ display: "grid",
                            gridTemplateColumns: "repeat(3, 1fr)",
                            gap: 10, marginBottom: 18 }}>
                {FAN_PRESETS.map(p => {
                  const active = activeFanPreset === p.id;
                  return (
                    <div
                      key={p.id}
                      role="button" tabIndex={0}
                      onClick={() => applyFanPreset(p)}
                      onKeyDown={e => {
                        if (e.key === "Enter" || e.key === " ") {
                          applyFanPreset(p); e.preventDefault();
                        }
                      }}
                      style={{
                        display: "flex", flexDirection: "column", gap: 5,
                        padding: "12px 14px",
                        background: active ? "rgba(118,185,0,0.10)" : "#1a1a1a",
                        border: `1px solid ${active ? "#76b900" : "#2a2a2a"}`,
                        borderRadius: 6, cursor: "pointer",
                        transition: "background 120ms, border-color 120ms",
                        boxSizing: "border-box", minWidth: 0,
                      }}
                      onMouseEnter={e => {
                        if (!active) {
                          e.currentTarget.style.background = "rgba(118,185,0,0.06)";
                          e.currentTarget.style.borderColor = "#76b900";
                        }
                      }}
                      onMouseLeave={e => {
                        if (!active) {
                          e.currentTarget.style.background = "#1a1a1a";
                          e.currentTarget.style.borderColor = "#2a2a2a";
                        }
                      }}
                    >
                      <div style={{ display: "flex", alignItems: "center", gap: 7 }}>
                        <span style={{ fontSize: 16 }}>{p.icon}</span>
                        <span style={{
                          color: active ? "#76b900" : "#e6e6e6",
                          fontWeight: 600, fontSize: 13,
                        }}>{p.label}</span>
                        {active && (
                          <span style={{
                            marginLeft: "auto",
                            background: "rgba(118,185,0,0.15)",
                            color: "#76b900", fontSize: 9,
                            fontWeight: 700, padding: "2px 6px",
                            borderRadius: 10, letterSpacing: 0.5,
                            textTransform: "uppercase",
                          }}>Active</span>
                        )}
                      </div>
                      <div style={{ color: "#8a9ab0", fontSize: 11,
                                    lineHeight: 1.5, whiteSpace: "normal" }}>
                        {p.description}
                      </div>
                    </div>
                  );
                })}
              </div>

              <div style={{ display: "flex", gap: 8, alignItems: "center",
                            marginBottom: 14 }}>
                <select
                  className="gb-select"
                  value={selectedProfile ?? "__custom"}
                  onChange={e => {
                    const v = e.target.value;
                    setActiveFanPreset(null);
                    if (v === "__custom") { setSelectedProfile(null); return; }
                    if (v === "__default") {
                      setSelectedProfile("__default");
                      setFanCurve(DEFAULT_FAN_CURVE);
                      setFanAuto(false);
                      return;
                    }
                    setSelectedProfile(v);
                    loadProfile(v);
                  }}
                  style={{ minWidth: 180 }}
                >
                  <option value="__default">Default</option>
                  <option value="__custom">Custom</option>
                  {profileNames.map(n => (
                    <option key={n} value={n}>{n}</option>
                  ))}
                </select>
                <button
                  className="btn-component"
                  title="Manage profiles (save / activate)"
                  disabled={selectedProfile === "__default"}
                  onClick={() => setTab("profiles")}
                  style={{ padding: "6px 10px",
                           opacity: selectedProfile === "__default" ? 0.4 : 1 }}
                >
                  <Icon.Pencil />
                </button>
                <button
                  className="btn-component"
                  title="Reset to default curve"
                  disabled={selectedProfile === "__default"}
                  onClick={() => { setFanCurve(DEFAULT_FAN_CURVE); setSelectedProfile(null); setActiveFanPreset(null); }}
                  style={{ padding: "6px 10px",
                           opacity: selectedProfile === "__default" ? 0.4 : 1 }}
                >
                  <Icon.RotateCcw />
                </button>

                <div style={{ marginLeft: "auto", display: "flex",
                              alignItems: "center", gap: 8 }}>
                  <span style={{ fontSize: 12, color: "#9a9a9a" }}>
                    {fanAuto ? "Auto (driver)" : "Custom curve"}
                  </span>
                  <div onClick={() => { setFanAuto(v => !v); }}
                       role="switch" aria-checked={!fanAuto} tabIndex={0}
                       title={fanAuto ? "Switch to custom fan curve" : "Return fan to driver control"}
                       style={{
                         display: "inline-flex", alignItems: "center",
                         width: 44, height: 22, padding: 2,
                         background: !fanAuto ? "#76b900" : "#3a3a3a",
                         borderRadius: 999, cursor: "pointer",
                         transition: "background 120ms ease",
                       }}>
                    <div style={{
                      width: 18, height: 18, borderRadius: 999, background: "#fff",
                      transform: `translateX(${!fanAuto ? 22 : 0}px)`,
                      transition: "transform 120ms ease",
                    }} />
                  </div>
                </div>
              </div>

              {selectedProfile === "__default" && (
                <div style={{ fontSize: 11, color: "#e8a000",
                              marginBottom: 6, padding: "4px 0" }}>
                  Default profile , read-only. Select Custom to edit.
                </div>
              )}
              <div style={{
                opacity: (fanAuto || selectedProfile === "__default") ? 0.45 : 1,
                pointerEvents: (fanAuto || selectedProfile === "__default") ? "none" : "auto",
                transition: "opacity 150ms ease",
              }}>
                <FanCurveEditor
                  points={fanCurve}
                  onChange={setFanCurve}
                  currentTempC={tempNum || null}
                  height={300}
                />
              </div>

              <p style={{ fontSize: 11, color: "#8a9ab0",
                          marginTop: 10, lineHeight: 1.5 }}>
                Drag anchor points to reshape the curve · click empty area to add ·
                click the red × on a selected point to remove.
                The in-app daemon polls temp every 4 s and adjusts via
                <code> nvidia-settings</code> (X11 / XWayland).
              </p>

              <div style={{ marginTop: 16, display: "flex",
                            alignItems: "center", gap: 16,
                            padding: "12px 0",
                            borderTop: "1px solid #2a2a2a" }}>
                <button className="btn-optimize"
                        onClick={handleApply} disabled={applying}>
                  {applying ? "Applying…" : "Apply"}
                </button>
                {!fanAuto && fanDaemonRef.current && (
                  <button className="btn-component"
                          style={{ borderColor: "#e05252", color: "#e05252" }}
                          onClick={() => {
                            stopFanDaemon();
                            invoke("fan_auto").catch(console.error);
                            invoke("set_active_gpu_profile", { name: null }).catch(() => {});
                            setApplyMsg("Fan returned to auto.");
                          }}>
                    Stop curve
                  </button>
                )}
                {applyMsg && (
                  <span style={{ fontSize: 12, color: "#76b900" }}>{applyMsg}</span>
                )}
              </div>
            </>
          )}

          {tab === "profiles" && (
            <>
              <p className="section-title">Save current settings as profile</p>
              <div className="section-card">
                <div style={{ display: "flex", gap: 10 }}>
                  <input
                    type="text"
                    placeholder="Profile name (e.g. quiet, performance)"
                    value={profileNameInput}
                    onChange={e => setProfileNameInput(e.target.value)}
                    style={{
                      flex: 1, padding: "8px 12px",
                      background: "#1a1a1a", border: "1px solid #2a2a2a",
                      borderRadius: 6, color: "#e6e6e6", fontSize: 13,
                    }}
                  />
                  <button className="btn-component"
                          onClick={saveCurrentProfile}
                          disabled={!profileNameInput.trim()}>
                    Save
                  </button>
                </div>
                <p style={{ fontSize: 11, color: "#8a9ab0", marginTop: 8 }}>
                  Stored at{" "}
                  <code>~/.config/greenboost-gaming/profiles/&lt;name&gt;.json</code>
                </p>
              </div>

              <p className="section-title" style={{ marginTop: 24 }}>
                Saved profiles
              </p>
              <div className="section-card">
                {profileNames.length === 0 ? (
                  <p style={{ color: "#8a9ab0" }}>
                    No profiles saved yet. Tune Overclocking + Fan Curve
                    above, then save one.
                  </p>
                ) : (
                  profileNames.map(name => {
                    const isActive = activeProfile === name;
                    const isSelected = selectedProfile === name;
                    return (
                      <div key={name} className="info-row"
                           style={{
                             gap: 8,
                             background: isSelected ? "rgba(118,185,0,0.05)" : "transparent",
                             borderRadius: 4,
                             margin: isSelected ? "0 -8px" : "0",
                             padding: isSelected ? "10px 8px" : "10px 0",
                           }}>
                        <div
                          style={{ flex: 1, display: "flex",
                                    alignItems: "center", gap: 10,
                                    cursor: "pointer" }}
                          onClick={() =>
                            setSelectedProfile(isSelected ? null : name)
                          }
                        >
                          <div className="info-label"
                               style={{
                                 fontWeight: 600,
                                 color: isSelected ? "#76b900" : undefined,
                               }}>{name}</div>
                          {isActive && (
                            <span style={{
                              background: "rgba(118,185,0,0.15)",
                              color: "#76b900",
                              fontSize: 10, fontWeight: 600,
                              padding: "2px 8px", borderRadius: 10,
                              letterSpacing: 0.5, textTransform: "uppercase",
                            }}>Active</span>
                          )}
                        </div>
                        <button className="btn-component"
                                onClick={() => loadProfile(name)}>
                          Load
                        </button>
                        <button className="btn-component"
                                onClick={() => setActive(isActive ? null : name)}
                                title={isActive
                                  ? "Stop the fan daemon from following this profile"
                                  : "Tell the fan daemon to follow this profile's curve"}>
                          {isActive ? "Deactivate" : "Activate"}
                        </button>
                      </div>
                    );
                  })
                )}
                {activeProfile && profileNames.length > 0 && (
                  <p style={{ fontSize: 11, color: "#8a9ab0",
                              margin: "14px 0 0", lineHeight: 1.5 }}>
                    Fan daemon is following <b>{activeProfile}</b>.
                    Make sure <code>gb-gaming-fan-daemon</code> is enabled:{" "}
                    <code>systemctl --user enable --now gb-gaming-fan-daemon</code>.
                  </p>
                )}
                {profileMsg && (
                  <p style={{ fontSize: 12, color: "#76b900",
                              marginTop: 12 }}>{profileMsg}</p>
                )}
              </div>

              {/* Bound games section , shown when a profile is selected */}
              {selectedProfile && (() => {
                const boundGames = allGames.filter(g => g.appid && boundAppids.includes(g.appid));
                const unboundGames = allGames.filter(
                  g => g.appid && !boundAppids.includes(g.appid)
                );
                const noGamesAtAll = allGames.length === 0;

                return (
                  <div style={{ marginTop: 24 }}>
                    <p className="section-title" style={{ marginBottom: 0 }}>
                      Bound games{boundGames.length > 0 ? ` (${boundGames.length})` : ""}
                    </p>
                    <div className="section-card" style={{ marginTop: 8 }}>
                      {bindingsLoading ? (
                        <p style={{ color: "#8a9ab0", fontSize: 12 }}>
                          Loading game bindings…
                        </p>
                      ) : noGamesAtAll ? (
                        <p style={{ color: "#8a9ab0", fontSize: 12, lineHeight: 1.5 }}>
                          No games found , launch Steam to scan your library.
                        </p>
                      ) : (
                        <>
                          {boundGames.length === 0 && (
                            <p style={{ color: "#8a9ab0", fontSize: 12,
                                        marginBottom: unboundGames.length > 0 ? 12 : 0 }}>
                              No games bound to <b style={{ color: "#e6e6e6" }}>{selectedProfile}</b> yet.
                            </p>
                          )}

                          {boundGames.map(g => (
                            <div
                              key={g.appid}
                              style={{
                                display: "flex", alignItems: "center",
                                justifyContent: "space-between",
                                padding: "7px 0",
                                borderBottom: "1px solid #1e1e1e",
                              }}
                            >
                              <div style={{ display: "flex", alignItems: "center", gap: 8 }}>
                                <span style={{
                                  display: "inline-block", width: 8, height: 8,
                                  borderRadius: "50%", background: "#76b900",
                                  flexShrink: 0,
                                }} />
                                <span style={{ fontSize: 13, color: "#e6e6e6" }}>
                                  {g.name}
                                </span>
                              </div>
                              <button
                                onClick={() => unbindGame(g.appid!)}
                                title="Remove binding"
                                style={{
                                  background: "transparent",
                                  border: "1px solid #3a3a3a",
                                  borderRadius: 4,
                                  color: "#9a9a9a",
                                  cursor: "pointer",
                                  fontSize: 13,
                                  lineHeight: 1,
                                  padding: "2px 7px",
                                  transition: "border-color 120ms, color 120ms",
                                }}
                                onMouseEnter={e => {
                                  e.currentTarget.style.borderColor = "#e05252";
                                  e.currentTarget.style.color = "#e05252";
                                }}
                                onMouseLeave={e => {
                                  e.currentTarget.style.borderColor = "#3a3a3a";
                                  e.currentTarget.style.color = "#9a9a9a";
                                }}
                              >
                                ×
                              </button>
                            </div>
                          ))}

                          {unboundGames.length > 0 && (
                            <div style={{
                              display: "flex", alignItems: "center",
                              gap: 8, marginTop: boundGames.length > 0 ? 12 : 4,
                            }}>
                              <select
                                className="gb-select"
                                value={bindTarget}
                                onChange={e => setBindTarget(e.target.value)}
                                style={{ flex: 1, minWidth: 0 }}
                              >
                                <option value="">Bind game…</option>
                                {unboundGames.map(g => (
                                  <option key={g.appid} value={g.appid!}>
                                    {g.name}
                                  </option>
                                ))}
                              </select>
                              <button
                                className="btn-component"
                                disabled={!bindTarget}
                                onClick={() => bindTarget && bindGame(bindTarget)}
                                style={{ flexShrink: 0 }}
                              >
                                Add
                              </button>
                            </div>
                          )}

                          {unboundGames.length === 0 && boundGames.length > 0 && (
                            <p style={{ fontSize: 11, color: "#8a9ab0",
                                        marginTop: 10, lineHeight: 1.5 }}>
                              All scanned games are already bound to this profile.
                            </p>
                          )}
                        </>
                      )}
                    </div>
                  </div>
                );
              })()}
            </>
          )}

        </div>
      </div>
    </>
  );
}
