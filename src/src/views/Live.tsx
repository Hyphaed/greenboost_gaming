// Copyright 2026 Ferran Duarri , GPL v2
import { useState, useEffect, useRef, useCallback } from "react";
import { invoke } from "@tauri-apps/api/core";
import { Channel } from "@tauri-apps/api/core";
import type { GpuMetrics, PoolBrief, GpuInfo, ViewType } from "../types";
import { Icon } from "../icons";

// ─── throttle significance ───────────────────────────────────────────────────
// NVML reports the truth, but not all of it is a problem worth interrupting
// someone over. Measured on this box (RTX 5070, driver 595.84): at an idle
// desktop , 14 W of a 250 W limit, 3% util , `nvidia-smi -q -d PERFORMANCE`
// shows an SW-Power-Capping counter of 11,027 seconds. The bit flickers on
// constantly while nothing is wrong. Rendering that as "GPU clock throttled"
// is how a banner trains its user to ignore banners.
//
// So a reason has to clear three bars before it is shown:
//   1. the GPU is actually doing work            (idle downclocking is fine)
//   2. reason-specific plausibility              (see reasonIsReal below)
//   3. it persists across consecutive polls      (kills single-tick flicker)

const THROTTLE_MIN_UTIL_PCT  = 20;
const POWER_CAP_NEAR_LIMIT   = 0.95;
const THROTTLE_MIN_TICKS     = 3;

function reasonIsReal(reason: string, m: GpuMetrics): boolean {
  if (reason === "Power limit") {
    // Only real when the card is genuinely drawing near its ceiling.
    return m.power_w != null && m.power_limit_w != null && m.power_limit_w > 0
        && m.power_w >= m.power_limit_w * POWER_CAP_NEAR_LIMIT;
  }
  // Thermal, power brake, hardware slowdown, sync boost, display clock:
  // none of these have an idle false-positive mode worth filtering.
  return true;
}

function significantReasons(m: GpuMetrics): string[] {
  if (!m.throttle_known || m.throttle_reasons.length === 0) return [];
  if ((m.gpu_util_pct ?? 0) < THROTTLE_MIN_UTIL_PCT) return [];
  return m.throttle_reasons.filter(r => reasonIsReal(r, m));
}

/** What to tell the user, and what we can do about it, per reason. */
type ThrottleAction =
  | { kind: "power";   label: string; targetW: number }
  | { kind: "fans";    label: string }
  | { kind: "none" };

/**
 * @param raisableToW board maximum, but only when it is actually above the
 *   limit currently in force , otherwise there is nothing to raise and
 *   offering the button would be a lie.
 */
function throttleAdvice(reason: string, raisableToW: number | null): {
  advice: string; action: ThrottleAction;
} {
  switch (reason) {
    case "Thermal (hardware)":
    case "Thermal (driver)":
      return {
        advice: "The GPU is cutting its own clocks to shed heat. More airflow "
              + "fixes this; a lower power limit also works if the fans are "
              + "already maxed.",
        action: { kind: "fans", label: "Open fan curve" },
      };
    case "Hardware slowdown":
      return {
        advice: "The driver dropped clocks hard , usually an emergency thermal "
              + "or power condition. Check airflow first.",
        action: { kind: "fans", label: "Open fan curve" },
      };
    case "Power limit":
      return raisableToW != null
        ? { advice: "The card is holding at its power ceiling, so it can't boost "
                  + "any higher. Raising the limit lets it draw more , within "
                  + "what your PSU and cooling allow.",
            action: { kind: "power", label: `Raise limit to ${raisableToW} W`, targetW: raisableToW } }
        : { advice: "The card is holding at its power ceiling and is already at "
                  + "the highest limit this board allows, so there is no headroom "
                  + "left to give it. Better cooling is what buys clocks from here.",
            action: { kind: "none" } };
    case "Power brake (PSU/connector)":
      return {
        advice: "The board pulled its own power brake , this is a PSU or "
              + "power-connector condition, not something software can raise "
              + "around. Check the 12V connector is fully seated.",
        action: { kind: "none" },
      };
    case "Sync boost group":
      return {
        advice: "Clocks are being held down to stay in sync with another GPU in "
              + "a sync-boost group. Expected on multi-GPU setups.",
        action: { kind: "none" },
      };
    case "Display clock":
      return {
        advice: "A display mode is holding a clock floor/ceiling. Usually a "
              + "high-refresh or multi-monitor arrangement.",
        action: { kind: "none" },
      };
    default:
      return { advice: "The driver is holding clocks below maximum.", action: { kind: "none" } };
  }
}

// ─── types ───────────────────────────────────────────────────────────────────

interface LayerStats {
  fps:           number;
  mean_ms:       number;
  p1_fps:        number;
  worst_ms:      number;
  hitches:       number;
  t2_mb:         number;
  t3_mb:         number;
  oom:           number;
  pso_compiles:  number;
  present_count: number;
}

const EMPTY_STATS: LayerStats = {
  fps: 0, mean_ms: 0, p1_fps: 0, worst_ms: 0,
  hitches: 0, t2_mb: 0, t3_mb: 0, oom: 0,
  pso_compiles: 0, present_count: 0,
};

// ─── SIGUSR1/SIGUSR2 dump parser ───────────────────────────────────────────────
// Vulkan line:  GreenBoost|fps=60|mean_ms=16.7|hitches=2|t2_mb=1024|t3_mb=0|oom=0|pso_compiles=156|present_count=3600
// OpenGL line:  GreenBoost-GL|fps=60|mean_ms=16.7|p1_fps=58|worst_ms=22.1|t2_tex=4|t3_tex=0|t2_tex_mb=1024|t3_tex_mb=0|t2_buf=2|t3_buf=0|oom=0
// The GL layer emits a different key set: no hitches/pso_compiles/present_count
// (it doesn't track them), and its VRAM-spill fields are named `t2_tex_mb`/
// `t3_tex_mb`, not `t2_mb`/`t3_mb` , remapped below rather than left to
// silently read as 0.

function parseStatsLine(line: string): LayerStats | null {
  const isGl = line.startsWith("GreenBoost-GL|");
  if (!isGl && !line.startsWith("GreenBoost|")) return null;
  const kv: Record<string, string> = {};
  for (const part of line.split("|").slice(1)) {
    const eq = part.indexOf("=");
    if (eq > 0) kv[part.slice(0, eq)] = part.slice(eq + 1);
  }
  const n = (k: string) => parseFloat(kv[k] ?? "0") || 0;
  return {
    fps:           n("fps"),
    mean_ms:       n("mean_ms"),
    p1_fps:        n("p1_fps"),
    worst_ms:      n("worst_ms"),
    hitches:       n("hitches"),
    t2_mb:         isGl ? n("t2_tex_mb") : n("t2_mb"),
    t3_mb:         isGl ? n("t3_tex_mb") : n("t3_mb"),
    oom:           n("oom"),
    pso_compiles:  n("pso_compiles"),
    present_count: n("present_count"),
  };
}

// ─── SVG sparkline ────────────────────────────────────────────────────────────

const SPARK_W = 340;
const SPARK_H = 64;
const MAX_POINTS = 60;

function Sparkline({ series, color = "#22d3ee" }: { series: number[]; color?: string }) {
  if (series.length < 2) {
    return (
      <svg width="100%" height={SPARK_H} viewBox={`0 0 ${SPARK_W} ${SPARK_H}`}
           preserveAspectRatio="none" style={{ display: "block" }}>
        <text x={SPARK_W / 2} y={SPARK_H / 2 + 5} textAnchor="middle"
          fill="#4b5563" fontSize={12}>waiting for data…</text>
      </svg>
    );
  }
  const max = Math.max(...series, 1);
  const pts = series.map((v, i) => {
    const x = (i / (series.length - 1)) * SPARK_W;
    const y = SPARK_H - (v / max) * (SPARK_H - 8) - 4;
    return `${x.toFixed(1)},${y.toFixed(1)}`;
  });
  const poly = pts.join(" ");
  const fill = `${pts[0]} ${SPARK_W},${SPARK_H - 4} 0,${SPARK_H - 4}`;
  return (
    <svg width="100%" height={SPARK_H} viewBox={`0 0 ${SPARK_W} ${SPARK_H}`}
         preserveAspectRatio="none" style={{ display: "block", overflow: "visible" }}>
      <defs>
        <linearGradient id="spark-grad" x1="0" y1="0" x2="0" y2="1">
          <stop offset="0%" stopColor={color} stopOpacity={0.25} />
          <stop offset="100%" stopColor={color} stopOpacity={0} />
        </linearGradient>
      </defs>
      <polygon points={fill} fill="url(#spark-grad)" />
      <polyline points={poly} fill="none" stroke={color} strokeWidth={1.5}
        strokeLinejoin="round" strokeLinecap="round" />
    </svg>
  );
}

// ─── memory tier bar ──────────────────────────────────────────────────────────

function TierRow({ label, pct, color, value, warn }: {
  label: string; pct: number; color: string; value: string; warn?: boolean;
}) {
  return (
    <div style={{ marginBottom: 8 }}>
      <div style={{ display: "flex", justifyContent: "space-between", marginBottom: 3, fontSize: 11 }}>
        <span style={{ color: warn ? color : "#9ca3af" }}>{label}</span>
        <span style={{ color: warn ? color : "#6b7280", fontVariantNumeric: "tabular-nums" }}>{value}</span>
      </div>
      <div style={{ height: 5, borderRadius: 3, background: "#1f2937", overflow: "hidden" }}>
        <div style={{
          height: "100%", width: `${pct}%`, borderRadius: 3,
          background: color, opacity: warn ? 1 : 0.55,
          transition: "width 0.35s ease",
        }} />
      </div>
    </div>
  );
}

function MemoryTierBar({ t1Used, t1Total, t2, t3 }: {
  t1Used: number | null; t1Total: number | null; t2: number; t3: number;
}) {
  const ref    = t1Total ?? 16384;
  const t1Pct  = t1Used != null && t1Total ? Math.min(100, (t1Used / t1Total) * 100) : 0;
  const t2Pct  = Math.min(100, (t2 / ref) * 100);
  const t3Pct  = Math.min(100, (t3 / ref) * 100);
  const fmt    = (mb: number) => mb >= 1024 ? `${(mb / 1024).toFixed(1)} GB` : `${mb.toFixed(0)} MB`;
  const t1Warn = t1Pct > 90;

  return (
    <div className="card" style={{ padding: "12px 16px" }}>
      <div style={{ fontSize: 11, fontWeight: 600, color: "#6b7280",
        textTransform: "uppercase", letterSpacing: "0.05em", marginBottom: 10 }}>
        Memory Tiers
      </div>
      <TierRow label="T1 VRAM" pct={t1Pct} color={t1Warn ? "#f87171" : "#22d3ee"}
        value={t1Used != null && t1Total != null ? `${fmt(t1Used)} / ${fmt(t1Total)}` : ","}
        warn={t1Warn} />
      <TierRow label="T2 DDR spill" pct={t2Pct} color="#fbbf24"
        value={t2 > 0 ? fmt(t2) : ","} warn={t2 > 0} />
      <TierRow label="T3 NVMe spill" pct={t3Pct} color="#f87171"
        value={t3 > 0 ? fmt(t3) : ","} warn={t3 > 0} />
      {t3 > 0 && (
        <div style={{ fontSize: 10, color: "#f87171", marginTop: 4, lineHeight: 1.4 }}>
          NVMe spill detected , severe performance impact. Reduce texture quality.
        </div>
      )}
      {t2 > 0 && t3 === 0 && (
        <div style={{ fontSize: 10, color: "#fbbf24", marginTop: 4, lineHeight: 1.4 }}>
          DDR spill active , consider lowering VRAM-heavy settings.
        </div>
      )}
    </div>
  );
}

// ─── live pool brief gauge (un-lagged, straight from pool_brief sysfs) ────────
// Distinct from MemoryTierBar above: that one only updates from the game
// layer's SIGUSR1 dump (game-scoped, only while a game is running).  This
// polls the kernel module's pool_brief sysfs file directly (G1), so it
// reflects whole-system T1/T2/T3 state , including AI-inference activity ,
// at all times, not just during a session.

function PoolBriefGauge({ brief }: { brief: PoolBrief | null }) {
  const fmtGb = (gb: number) => `${gb} GB`;
  // MB-precision figures (from the `status` sysfs file) beat pool_brief's
  // truncated integer GB , a sub-1 GB spill would otherwise render "0 GB".
  const fmtMb = (mb: number) => mb >= 1024 ? `${(mb / 1024).toFixed(1)} GB` : `${mb.toFixed(0)} MB`;
  const t2Alloc = brief?.t2_alloc_mb ?? (brief ? brief.t2_alloc_gb * 1024 : 0);
  const t2Pct = brief?.t2_fill_pct ?? (brief ? Math.min(100, brief.t2_pct) : 0);
  const t3AllocMb = brief?.t3_alloc_mb ?? (brief ? brief.t3_alloc_gb * 1024 : 0);
  const t3Pct = brief && brief.t3_max_gb > 0
    ? Math.min(100, (brief.t3_alloc_gb / brief.t3_max_gb) * 100) : 0;
  // NOTE: this is T3's pressure enum (swap_pressure), not T2's , see the
  // PoolBrief.t3_pressure doc comment in live_stats.rs. Labeled honestly.
  const pressureBad = brief != null && brief.t3_pressure !== "ok";

  return (
    <div className="card" style={{ padding: "12px 16px" }}>
      <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", marginBottom: 10 }}>
        <span style={{ fontSize: 11, fontWeight: 600, color: "#6b7280",
          textTransform: "uppercase", letterSpacing: "0.05em" }}>
          Live Pool State (system-wide)
        </span>
        {brief && (
          <span style={{ fontSize: 10, color: pressureBad ? "#f87171" : "#22c55e",
            fontWeight: 600, textTransform: "uppercase" }}
            title="T3 (NVMe swap) pressure , not T2's">
            T3 {brief.t3_pressure}
          </span>
        )}
      </div>
      {brief == null ? (
        <div style={{ fontSize: 11, color: "#374151" }}>
          Kernel module not loaded, or pool_brief unavailable.
        </div>
      ) : (
        <>
          <TierRow label="T1 VRAM" pct={100} color="#22d3ee"
            value={fmtGb(brief.t1_gb)} />
          <TierRow label="T2 DDR" pct={t2Pct} color="#fbbf24"
            value={`${fmtMb(t2Alloc)} / ${fmtGb(brief.t2_max_gb)} (${t2Pct.toFixed(1)}%)`}
            warn={t2Alloc > 0} />
          <TierRow label="T3 NVMe" pct={t3Pct} color="#f87171"
            value={`${fmtMb(t3AllocMb)} / ${fmtGb(brief.t3_max_gb)}`}
            warn={t3AllocMb > 0} />
          <div style={{ fontSize: 10, color: "#6b7280", marginTop: 4 }}>
            KV reserve {brief.kv_reserve_mb} MB · KV in T2 {brief.kv_t2_mb} MB
          </div>
        </>
      )}
    </div>
  );
}

// ─── stat row ─────────────────────────────────────────────────────────────────

function StatRow({ label, value, unit, warn, icon }: {
  label: string; value: string | number; unit?: string; warn?: boolean;
  icon?: React.ReactNode;
}) {
  return (
    <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center",
      padding: "4px 0", borderBottom: "1px solid #1f2937" }}>
      <span style={{ display: "flex", alignItems: "center", gap: 5,
                     color: "#9ca3af", fontSize: 13 }}>
        {icon && <span style={{ opacity: 0.7, display: "flex", alignItems: "center" }}>{icon}</span>}
        {label}
      </span>
      <span style={{ color: warn ? "#f87171" : "#e5e7eb", fontVariantNumeric: "tabular-nums",
        fontSize: 14, fontWeight: 600 }}>
        {value}{unit ? <span style={{ color: "#6b7280", fontSize: 11, marginLeft: 3 }}>{unit}</span> : null}
      </span>
    </div>
  );
}

// ─── main view ────────────────────────────────────────────────────────────────

interface RecordedEntry {
  ts: string;     // ISO timestamp
  stats: LayerStats;
}

/** One-line human summary for a dataflux event, dispatched on `kind`.
 * Event shapes vary by kind (this is a shared, evolving core log, not a
 * fixed schema) , falls back to listing whatever fields are present rather
 * than showing nothing for a kind this panel doesn't specifically know. */
function summarizeDataflux(ev: Record<string, any>): string {
  const kind = ev.kind ?? "";
  switch (kind) {
    case "gaming_session":
      return ev.action === "start"
        ? `started (appid ${ev.appid ?? "?"}, ${ev.gpu ?? "GPU"})`
        : `ended after ${Math.round((ev.elapsed_s ?? 0) / 60)} min , peak ${ev.peak_vram_mb ?? "?"} MB VRAM`;
    case "quantize":
    case "quantize_to_fit":
      return `${ev.component ?? "model"} → ${ev.bits ?? "?"} bit (budget ${ev.budget_gb ?? "?"} GiB, actual ${ev.actual_gb ?? "?"} GiB)`;
    case "tier_move":
      return `${ev.label ?? ev.node ?? "buffer"} ${ev.from ?? "?"} → ${ev.to ?? "?"}${ev.size_mb ? ` (${ev.size_mb} MB)` : ""}`;
    case "gaming_vram_pressure": {
      // t2_alloc_mb/t3_alloc_mb (MB precision) are emitted alongside the
      // legacy truncated-GB fields as of the T2-spill session tracking
      // change , prefer them so a sub-1GB spill doesn't read "0 GB".
      const t2 = ev.t2_alloc_mb != null ? `${ev.t2_alloc_mb} MB` : `${ev.t2_alloc_gb ?? "?"} GB`;
      const t3 = ev.t3_alloc_mb != null ? `${ev.t3_alloc_mb} MB` : `${ev.t3_alloc_gb ?? "?"} GB`;
      // `pressure` here is swap_pressure (T3's enum), not T2's , see
      // PoolBrief.t3_pressure's doc comment.
      return `T2 ${t2} (${ev.t2_pct ?? "?"}%) · T3 ${t3} spilled , T3 pressure ${ev.pressure ?? "?"}`;
    }
    case "turboquant_activate":
      return `k=${ev.k_bits ?? "?"} v=${ev.v_bits ?? "?"} bits on ${ev.device ?? "GPU"}`;
    case "kernel_backend":
      return String(ev.backend ?? ev.engine ?? "");
    default: {
      const skip = new Set(["kind", "ts", "pid", "node"]);
      const parts = Object.entries(ev)
        .filter(([k]) => !skip.has(k))
        .slice(0, 4)
        .map(([k, v]) => `${k}=${v}`);
      return parts.join(" · ");
    }
  }
}

export function LiveView({ onNavigate }: { onNavigate?: (v: ViewType) => void } = {}) {
  const [gamePid, setGamePid]       = useState<number | null>(null);
  const [stats, setStats]           = useState<LayerStats>(EMPTY_STATS);
  const [fpsSeries, setFpsSeries]   = useState<number[]>([]);
  const [logs, setLogs]             = useState<string[]>([]);
  // Telemetry and the raw log used to share one row, with telemetry pinned
  // to a hard-coded 380px column and the log taking everything else , so on
  // any wide screen the numbers you actually watch were squeezed into a
  // narrow strip while the log sprawled. They're tabs now: each gets the
  // full width, and the log keeps streaming in the background either way.
  const [liveTab, setLiveTab] = useState<"telemetry" | "log">("telemetry");
  const [streaming, setStreaming]   = useState(false);
  const [error, setError]           = useState<string | null>(null);
  const [recording, setRecording]   = useState(false);
  const [recordedCount, setRecordedCount] = useState(0);
  const recordingRef = useRef(false);  // mirror of recording, readable inside closures
  const [gpuMetrics, setGpuMetrics] = useState<GpuMetrics | null>(null);
  const [dataflux, setDataflux] = useState<Record<string, any>[]>([]);
  const [poolBrief, setPoolBrief] = useState<PoolBrief | null>(null);
  const [contention, setContention] = useState<Record<string, any> | null>(null);

  const [gpuClockSeries, setGpuClockSeries] = useState<number[]>([]);
  const [gpuPowerSeries, setGpuPowerSeries] = useState<number[]>([]);

  const [psoStalling, setPsoStalling] = useState(false);
  const psoStallTimer = useRef<ReturnType<typeof setTimeout> | null>(null);
  const prevPsoRef    = useRef<number>(0);

  // Throttle: NVML tells us the real reason. The rolling-peak-clock heuristic
  // below is the fallback for drivers where NVML won't report it (throttle_known
  // === false) , inference, and labelled as such in the banner.
  const peakClockRef  = useRef<number>(0);
  const [throttle, setThrottle] = useState<{
    reasons:  string[];
    inferred: boolean;
    /** Frozen at trigger time so the banner quotes the numbers that caused it. */
    at:       { power_w: number | null; power_limit_w: number | null;
                temp_c: number | null; clock_gpu_mhz: number | null };
  } | null>(null);
  const throttleTimer = useRef<ReturnType<typeof setTimeout> | null>(null);
  const throttleTicks = useRef<number>(0);

  // Power ceiling this card allows, for the "Raise limit" action. Read once ,
  // it's a hardware property, not telemetry.
  const [maxPowerW, setMaxPowerW] = useState<number | null>(null);
  const [powerBusy, setPowerBusy] = useState(false);
  const [actionNote, setActionNote] = useState<string | null>(null);

  const logsRef      = useRef<HTMLDivElement>(null);
  const channelRef   = useRef<Channel<string> | null>(null);
  const pollRef      = useRef<ReturnType<typeof setInterval> | null>(null);
  const logBuf       = useRef<string[]>([]);
  const recEntries   = useRef<RecordedEntry[]>([]);
  const recLogs      = useRef<string[]>([]);

  // ── NVML GPU metrics poll (1 s , no game needed) ────────────────────────────
  useEffect(() => {
    const tick = async () => {
      try {
        const m = await invoke<GpuMetrics>("poll_gpu_metrics");
        setGpuMetrics(m);
        if (m.clock_gpu_mhz != null) {
          setGpuClockSeries(prev => {
            const next = [...prev, m.clock_gpu_mhz!];
            return next.length > MAX_POINTS ? next.slice(-MAX_POINTS) : next;
          });
          if (m.clock_gpu_mhz > peakClockRef.current) peakClockRef.current = m.clock_gpu_mhz;
        }
        // Real reason from NVML when the driver reports it; only guess when it
        // doesn't. Either way the banner holds 12 s so a brief dip stays visible.
        const flagThrottle = (reasons: string[], inferred: boolean) => {
          setThrottle({
            reasons, inferred,
            at: { power_w: m.power_w, power_limit_w: m.power_limit_w,
                  temp_c: m.temp_c, clock_gpu_mhz: m.clock_gpu_mhz },
          });
          if (throttleTimer.current) clearTimeout(throttleTimer.current);
          throttleTimer.current = setTimeout(() => setThrottle(null), 12000);
        };
        if (m.throttle_known) {
          const reasons = significantReasons(m);
          if (reasons.length > 0) {
            // Bar 3: only after it has held for a few consecutive polls.
            throttleTicks.current += 1;
            if (throttleTicks.current >= THROTTLE_MIN_TICKS) flagThrottle(reasons, false);
          } else {
            throttleTicks.current = 0;
          }
        } else if (
          m.clock_gpu_mhz != null &&
          m.temp_c != null && m.temp_c >= 83 &&
          peakClockRef.current > 0 &&
          m.clock_gpu_mhz < peakClockRef.current * 0.92
        ) {
          // No NVML reason available , fall back to temp + clock-drop inference.
          flagThrottle(["Thermal (inferred)"], true);
        }
        if (m.power_w != null) {
          setGpuPowerSeries(prev => {
            const next = [...prev, m.power_w!];
            return next.length > MAX_POINTS ? next.slice(-MAX_POINTS) : next;
          });
        }
      } catch { /* NVML unavailable */ }
    };
    tick();
    const t = setInterval(tick, 1000);
    return () => clearInterval(t);
  }, []);

  // Board power ceiling , only needed to label/act on a power-limit throttle.
  useEffect(() => {
    invoke<GpuInfo>("get_gpu")
      .then(g => setMaxPowerW(g.power_limit_max > 0 ? Math.round(g.power_limit_max) : null))
      .catch(() => { /* no nvidia-smi , the action button just won't offer */ });
  }, []);

  // Act on the throttle rather than only naming it. Raising the limit needs
  // root (nvml_control.py via pkexec), so this can be declined , report that
  // honestly instead of leaving the button looking like it worked.
  const raisePowerLimit = useCallback(async (watts: number) => {
    setPowerBusy(true);
    setActionNote(null);
    try {
      await invoke<string>("set_power_limit", { watts });
      setActionNote(`Power limit raised to ${watts} W.`);
      throttleTicks.current = 0;
      setThrottle(null);
    } catch (e) {
      setActionNote(String(e));
    } finally {
      setPowerBusy(false);
    }
  }, []);

  // ── GreenBoost dataflux poll (5 s , same cadence as core's SnapshotRecorder) ─
  // Cross-subsystem visibility: shows inference/tiering activity (gb_quant,
  // gb_cluster, tier moves) happening on the same GPU at the same time as
  // the game, plus this session's own gaming_session start/stop events ,
  // one shared timeline instead of two blind subsystems.
  useEffect(() => {
    const tick = async () => {
      try {
        const events = await invoke<Record<string, any>[]>("get_dataflux_recent", { limit: 12 });
        setDataflux(events);
      } catch { /* core not installed, or nothing emitted yet */ }
    };
    tick();
    const t = setInterval(tick, 5000);
    return () => clearInterval(t);
  }, []);

  // ── G1: live pool_brief poll (2 s , UI cadence, un-lagged, always on) ───────
  useEffect(() => {
    const tick = async () => {
      try {
        const brief = await invoke<PoolBrief | null>("get_pool_brief");
        setPoolBrief(brief ?? null);
      } catch { setPoolBrief(null); }
    };
    tick();
    const t = setInterval(tick, 2000);
    return () => clearInterval(t);
  }, []);

  // ── G2: GB-Semantics gaming_inference_contention poll (10 s) ────────────────
  // Governed segment (main repo's gb_semantics.py): true when a gaming
  // session is active AND AI-inference t2_pressure_fraction > 0.3 , the
  // real coexistence conflict between this game and inference on the GPU.
  useEffect(() => {
    const tick = async () => {
      try {
        const seg = await invoke<Record<string, any>>("get_gaming_inference_contention");
        setContention(seg?.matched ? seg : null);
      } catch { setContention(null); }
    };
    tick();
    const t = setInterval(tick, 10000);
    return () => clearInterval(t);
  }, []);

  // ── PID poll (2 s) ──────────────────────────────────────────────────────────
  useEffect(() => {
    const poll = async () => {
      try {
        const pid: number | null = await invoke("find_game_pid");
        setGamePid(pid ?? null);
      } catch {
        setGamePid(null);
      }
    };
    poll();
    const t = setInterval(poll, 2000);
    return () => clearInterval(t);
  }, []);

  // ── SIGUSR1 stats poll (1 s when game is running) ──────────────────────────
  useEffect(() => {
    if (pollRef.current) clearInterval(pollRef.current);
    if (!gamePid) return;
    const tick = async () => {
      try { await invoke("send_sigusr1", { pid: gamePid }); } catch { /* layer may not be loaded */ }
    };
    tick();
    pollRef.current = setInterval(tick, 1000);
    return () => { if (pollRef.current) clearInterval(pollRef.current); };
  }, [gamePid]);

  // ── log stream lifecycle , always live, auto-restarts on disconnect ──────────
  const startStream = useCallback(async () => {
    if (streaming) return;
    setError(null);
    setStreaming(true);

    const ch = new Channel<string>();
    channelRef.current = ch;

    ch.onmessage = (line: string) => {
      if (line.startsWith("[error]")) {
        setError(line);
        setStreaming(false);
        return;
      }
      const parsed = parseStatsLine(line);
      if (parsed) {
        if (parsed.pso_compiles > prevPsoRef.current) {
          setPsoStalling(true);
          if (psoStallTimer.current) clearTimeout(psoStallTimer.current);
          psoStallTimer.current = setTimeout(() => setPsoStalling(false), 8000);
        }
        prevPsoRef.current = parsed.pso_compiles;
        setStats(parsed);
        setFpsSeries(prev => {
          const next = [...prev, parsed.fps];
          return next.length > MAX_POINTS ? next.slice(next.length - MAX_POINTS) : next;
        });
        if (recordingRef.current) {
          recEntries.current.push({ ts: new Date().toISOString(), stats: parsed });
          setRecordedCount(recEntries.current.length);
        }
      }
      if (recordingRef.current) recLogs.current.push(line);
      logBuf.current = [...logBuf.current.slice(-499), line];
      setLogs([...logBuf.current]);
    };

    try {
      await invoke("stream_layer_log", { channel: ch });
    } catch (e) {
      setError(String(e));
    } finally {
      setStreaming(false);
    }
  }, [streaming]);

  // Auto-start stream on mount and restart after a short delay if it stops.
  useEffect(() => {
    startStream();
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  useEffect(() => {
    if (!streaming) {
      const t = setTimeout(() => startStream(), 2000);
      return () => clearTimeout(t);
    }
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [streaming]);

  const startRecording = useCallback(() => {
    recEntries.current = [];
    recLogs.current = [];
    setRecordedCount(0);
    recordingRef.current = true;
    setRecording(true);
  }, []);

  const stopRecording = useCallback(() => {
    recordingRef.current = false;
    setRecording(false);
  }, []);

  const exportJson = useCallback(() => {
    const payload = {
      exported_at: new Date().toISOString(),
      total_samples: recEntries.current.length,
      samples: recEntries.current,
      raw_logs: recLogs.current,
    };
    const blob = new Blob([JSON.stringify(payload, null, 2)], { type: "application/json" });
    const url  = URL.createObjectURL(blob);
    const a    = document.createElement("a");
    a.href     = url;
    a.download = `greenboost-session-${new Date().toISOString().slice(0, 19).replace(/:/g, "-")}.json`;
    a.click();
    URL.revokeObjectURL(url);
  }, []);

  const exportLogs = useCallback(() => {
    const now = new Date().toISOString();
    const lines = logBuf.current;

    const gpuSection = gpuMetrics
      ? [
          "## GPU Snapshot",
          "",
          "| Metric | Value |",
          "|---|---|",
          gpuMetrics.temp_c        != null ? `| Temperature | ${gpuMetrics.temp_c} °C |` : "",
          gpuMetrics.clock_gpu_mhz != null ? `| GPU Clock | ${gpuMetrics.clock_gpu_mhz} MHz |` : "",
          gpuMetrics.power_w       != null ? `| Power | ${gpuMetrics.power_w.toFixed(1)} W |` : "",
          gpuMetrics.vram_used_mb  != null ? `| VRAM Used | ${gpuMetrics.vram_used_mb} MB |` : "",
          gpuMetrics.fan_pct       != null ? `| Fan | ${gpuMetrics.fan_pct} % |` : "",
          // Never export a blank throttle row as "fine" , say when NVML is silent.
          `| Throttle (NVML) | ${
            !gpuMetrics.throttle_known ? "unknown , not reported by driver"
              : gpuMetrics.throttle_reasons.length > 0
                ? gpuMetrics.throttle_reasons.join(", ")
                : "none"
          } |`,
        ].filter(Boolean).join("\n")
      : "";

    const recSection = recEntries.current.length > 0
      ? [
          "## Recorded Session Summary",
          "",
          `Samples: ${recEntries.current.length}`,
          "",
          "| Timestamp | FPS | p1 FPS | Mean ms | Hitches | PSO |",
          "|---|---|---|---|---|---|",
          ...recEntries.current.map(e =>
            `| ${e.ts} | ${e.stats.fps} | ${e.stats.p1_fps.toFixed(1)} | ${e.stats.mean_ms.toFixed(2)} | ${e.stats.hitches} | ${e.stats.pso_compiles} |`
          ),
        ].join("\n")
      : "";

    const md = [
      "# GreenBoost Gaming Suite , Live Stats Log",
      "",
      `**Exported:** ${now}`,
      gamePid ? `**Game PID:** ${gamePid}` : "",
      "",
      gpuSection,
      gpuSection ? "" : "",
      recSection,
      recSection ? "" : "",
      "## Log",
      "",
      "```",
      lines.length > 0 ? lines.join("\n") : "(no log entries)",
      "```",
      "",
      "---",
      "*Generated by GreenBoost Gaming Suite , https://github.com/Hyphaed*",
    ].filter(l => l !== undefined).join("\n");

    const blob = new Blob([md], { type: "text/markdown" });
    const url  = URL.createObjectURL(blob);
    const a    = document.createElement("a");
    a.href     = url;
    a.download = `greenboost-log-${now.slice(0, 19).replace(/:/g, "-")}.md`;
    a.click();
    URL.revokeObjectURL(url);
  }, [gamePid, gpuMetrics]);

  const exportCsv = useCallback(() => {
    if (recEntries.current.length === 0) return;
    const header = "timestamp,fps,mean_ms,p1_fps,worst_ms,hitches,t2_mb,t3_mb,oom,pso_compiles,present_count";
    const rows = recEntries.current.map(e =>
      [e.ts, e.stats.fps, e.stats.mean_ms, e.stats.p1_fps, e.stats.worst_ms,
       e.stats.hitches, e.stats.t2_mb, e.stats.t3_mb, e.stats.oom,
       e.stats.pso_compiles, e.stats.present_count].join(",")
    );
    const blob = new Blob([[header, ...rows].join("\n")], { type: "text/csv" });
    const url  = URL.createObjectURL(blob);
    const a    = document.createElement("a");
    a.href     = url;
    a.download = `greenboost-session-${new Date().toISOString().slice(0, 19).replace(/:/g, "-")}.csv`;
    a.click();
    URL.revokeObjectURL(url);
  }, []);

  // auto-scroll log
  useEffect(() => {
    if (logsRef.current) {
      logsRef.current.scrollTop = logsRef.current.scrollHeight;
    }
  }, [logs]);

  // ── render ──────────────────────────────────────────────────────────────────
  const hasGame = gamePid !== null;
  const canExport = gpuMetrics !== null || logs.length > 0 || recEntries.current.length > 0;

  return (
    <div style={{ flex: 1, display: "flex", flexDirection: "column", overflow: "hidden",
      gap: 16, padding: "0 4px" }}>

      {/* top bar */}
      <div style={{ display: "flex", alignItems: "center", gap: 12, flexShrink: 0 }}>
        <div style={{ display: "flex", alignItems: "center", gap: 8 }}>
          <div style={{
            width: 10, height: 10, borderRadius: "50%",
            background: hasGame ? "#22c55e" : "#374151",
            boxShadow: hasGame ? "0 0 6px #22c55e" : "none",
          }} />
          <span style={{ color: hasGame ? "#d1fae5" : "#6b7280", fontSize: 13 }}>
            {hasGame ? `Game PID ${gamePid}` : "No game detected"}
          </span>
        </div>

        <div style={{ marginLeft: "auto", display: "flex", gap: 8, alignItems: "center", flexWrap: "wrap" }}>
          {/* stream status indicator */}
          <span style={{ fontSize: 11, color: streaming ? "#22c55e" : "#6b7280",
                         display: "flex", alignItems: "center", gap: 5 }}>
            <span style={{ width: 6, height: 6, borderRadius: "50%",
                           background: streaming ? "#22c55e" : "#6b7280",
                           boxShadow: streaming ? "0 0 5px #22c55e" : "none",
                           display: "inline-block" }} />
            {streaming ? "Live" : "Reconnecting…"}
          </span>

          {/* recording controls */}
          {recording ? (
            <button
              onClick={stopRecording}
              style={{
                display: "inline-flex", alignItems: "center", gap: 6,
                padding: "5px 12px", borderRadius: 5, cursor: "pointer",
                background: "rgba(239,68,68,0.15)", border: "1px solid rgba(239,68,68,0.4)",
                color: "#f87171", fontSize: 12, fontWeight: 600,
              }}
            >
              <span style={{ width: 8, height: 8, borderRadius: "50%", background: "#f87171",
                animation: "pulse 1s infinite" }} />
              Stop REC ({recordedCount})
            </button>
          ) : (
            <button
              onClick={startRecording}
              style={{
                display: "inline-flex", alignItems: "center", gap: 6,
                padding: "5px 12px", borderRadius: 5, cursor: "pointer",
                background: "rgba(239,68,68,0.08)", border: "1px solid rgba(239,68,68,0.3)",
                color: "#f87171", fontSize: 12, fontWeight: 600,
              }}
            >
              ● Start REC
            </button>
          )}

          {/* Export Logs , enabled as soon as GPU metrics are available */}
          <button
            onClick={exportLogs}
            disabled={!canExport}
            style={{
              padding: "5px 12px", borderRadius: 5,
              cursor: canExport ? "pointer" : "default",
              background: canExport ? "rgba(99,102,241,0.08)" : "rgba(99,102,241,0.03)",
              border: `1px solid ${canExport ? "rgba(99,102,241,0.3)" : "rgba(99,102,241,0.15)"}`,
              color: canExport ? "#a5b4fc" : "#4a4a7a",
              fontSize: 12, fontWeight: 600,
            }}
            title={canExport ? "Export GPU snapshot + session log as .md" : "Waiting for GPU data…"}
          >
            Export Logs
          </button>

          {/* export buttons , shown when there are recorded samples */}
          {recordedCount > 0 && !recording && (
            <>
              <button
                onClick={exportJson}
                style={{
                  padding: "5px 12px", borderRadius: 5, cursor: "pointer",
                  background: "rgba(118,185,0,0.08)", border: "1px solid rgba(118,185,0,0.3)",
                  color: "#76b900", fontSize: 12, fontWeight: 600,
                }}
                title={`Export ${recordedCount} samples as JSON`}
              >
                Export JSON
              </button>
              <button
                onClick={exportCsv}
                style={{
                  padding: "5px 12px", borderRadius: 5, cursor: "pointer",
                  background: "rgba(118,185,0,0.08)", border: "1px solid rgba(118,185,0,0.3)",
                  color: "#76b900", fontSize: 12, fontWeight: 600,
                }}
                title={`Export ${recordedCount} samples as CSV`}
              >
                Export CSV
              </button>
            </>
          )}
        </div>
      </div>

      {error && (
        <div style={{ background: "#450a0a", border: "1px solid #7f1d1d", borderRadius: 6,
          padding: "8px 12px", color: "#fca5a5", fontSize: 13 }}>
          {error}
        </div>
      )}

      {contention && (
        <div style={{
          background: "rgba(248,113,113,0.12)", border: "1px solid rgba(248,113,113,0.5)",
          borderRadius: 6, padding: "8px 14px", color: "#f87171", fontSize: 13,
          display: "flex", alignItems: "center", gap: 10, flexShrink: 0,
        }}>
          <span style={{ fontSize: 16 }}>⚠</span>
          <span>
            <b>AI inference is competing with this game for VRAM</b> , GreenBoost
            detected high inference memory pressure while a game session is
            active ({contention.doc ?? "gaming_inference_contention"}).
          </span>
        </div>
      )}

      {throttle && (() => {
        // Reasons arrive severity-ordered from the backend, so the first one
        // is the one worth acting on.
        const primary = throttle.reasons[0];
        const { power_w, power_limit_w, temp_c, clock_gpu_mhz } = throttle.at;
        // Only offer to raise the limit when there is headroom above the one
        // currently in force.
        const raisableToW = (maxPowerW != null && power_limit_w != null
                             && maxPowerW > power_limit_w + 1) ? maxPowerW : null;
        const { advice, action } = throttleAdvice(primary, raisableToW);
        // The numbers that triggered it, so the claim is checkable on sight
        // rather than something the user has to take on faith.
        const facts = [
          power_w != null && power_limit_w != null
            ? `${power_w.toFixed(0)} W of ${power_limit_w.toFixed(0)} W` : null,
          temp_c != null        ? `${temp_c} °C`            : null,
          clock_gpu_mhz != null ? `${clock_gpu_mhz} MHz`    : null,
        ].filter(Boolean).join("  ·  ");
        const showAction = action.kind === "power"
          || (action.kind === "fans" && onNavigate != null);

        return (
          <div style={{
            background: "rgba(251,146,60,0.10)", border: "1px solid rgba(251,146,60,0.45)",
            borderRadius: 8, padding: "12px 16px", flexShrink: 0,
            display: "flex", alignItems: "flex-start", gap: 14,
          }}>
            <span style={{ fontSize: 18, lineHeight: "22px" }}>🌡</span>

            <div style={{ flex: 1, minWidth: 0, display: "flex", flexDirection: "column", gap: 6 }}>
              {/* headline: what, plus the reason as a chip , not buried in prose */}
              <div style={{ display: "flex", alignItems: "center", gap: 8, flexWrap: "wrap" }}>
                <b style={{ color: "#fb923c", fontSize: 13.5 }}>GPU clock throttled</b>
                {throttle.reasons.map(r => (
                  <span key={r} style={{
                    background: "rgba(251,146,60,0.18)", color: "#fdba74",
                    borderRadius: 4, padding: "1px 7px", fontSize: 11.5, fontWeight: 600,
                  }}>{r}</span>
                ))}
                <span style={{ color: "#71717a", fontSize: 11.5 }}>
                  {throttle.inferred
                    ? "inferred , driver reports no reason"
                    : "reported by the driver (NVML)"}
                </span>
              </div>

              {/* the evidence */}
              {facts && (
                <div style={{ color: "#a1a1aa", fontSize: 12, fontVariantNumeric: "tabular-nums" }}>
                  {facts}
                </div>
              )}

              {/* what it means and what to do , one reason, one instruction */}
              <div style={{ color: "#d4d4d8", fontSize: 12.5, lineHeight: 1.5 }}>{advice}</div>

              {actionNote && (
                <div style={{ color: "#a1a1aa", fontSize: 12 }}>{actionNote}</div>
              )}
            </div>

            {showAction && (
              <button
                disabled={powerBusy}
                onClick={() => {
                  if (action.kind === "power") raisePowerLimit(action.targetW);
                  else if (action.kind === "fans") onNavigate?.("profile");
                }}
                style={{
                  flexShrink: 0, alignSelf: "center", whiteSpace: "nowrap",
                  padding: "6px 14px", borderRadius: 5,
                  cursor: powerBusy ? "default" : "pointer",
                  background: powerBusy ? "rgba(251,146,60,0.06)" : "rgba(251,146,60,0.14)",
                  border: "1px solid rgba(251,146,60,0.45)",
                  color: powerBusy ? "#8a6440" : "#fdba74",
                  fontSize: 12, fontWeight: 600,
                }}
              >
                {powerBusy ? "Applying…" : action.label}
              </button>
            )}
          </div>
        );
      })()}

      {/* main columns */}
      <div className="sub-nav" style={{ flexShrink: 0 }}>
        <button className={`sub-nav-tab${liveTab === "telemetry" ? " active" : ""}`}
                onClick={() => setLiveTab("telemetry")}>Telemetry</button>
        <button className={`sub-nav-tab${liveTab === "log" ? " active" : ""}`}
                onClick={() => setLiveTab("log")}>
          Layer log{logs.length > 0 ? ` (${logs.length})` : ""}
          {streaming && <span style={{ color: "#22c55e", marginLeft: 6 }}>●</span>}
        </button>
      </div>

      <div style={{ flex: 1, display: "flex", gap: 16, overflow: "hidden", minHeight: 0 }}>

        {/* Telemetry , full width, auto-fitting columns capped at 4.
            Column sizing and the card chrome live in .telemetry-grid / .card
            (index.css) rather than inline, because capping the track count
            needs a max() in the track definition. */}
        <div
          className="telemetry-grid"
          style={{
            display: liveTab === "telemetry" ? "grid" : "none",
            flex: 1, minWidth: 0, overflowY: "auto", paddingRight: 4,
          }}
        >

          {/* FPS sparkline card */}
          <div className="card" style={{ padding: "12px 16px" }}>
            <div style={{ display: "flex", justifyContent: "space-between", marginBottom: 8 }}>
              <span style={{ color: "#9ca3af", fontSize: 12, textTransform: "uppercase",
                letterSpacing: "0.05em" }}>FPS (last 60 s)</span>
              <span style={{ color: "#22d3ee", fontVariantNumeric: "tabular-nums",
                fontWeight: 700, fontSize: 22 }}>
                {stats.fps > 0 ? stats.fps.toFixed(0) : ","}
              </span>
            </div>
            <Sparkline series={fpsSeries} color="#22d3ee" />
          </div>

          {/* GPU clock sparkline */}
          {gpuClockSeries.length > 1 && gpuMetrics && (
            <div className="card" style={{ padding: "12px 16px" }}>
              <div style={{ display: "flex", justifyContent: "space-between", marginBottom: 8 }}>
                <span style={{ color: "#9ca3af", fontSize: 12, textTransform: "uppercase",
                  letterSpacing: "0.05em" }}>GPU Clock</span>
                <span style={{ color: "#a78bfa", fontVariantNumeric: "tabular-nums",
                  fontWeight: 700, fontSize: 18 }}>
                  {gpuMetrics.clock_gpu_mhz ?? ","}
                  <span style={{ color: "#6b7280", fontSize: 11, marginLeft: 3 }}>MHz</span>
                </span>
              </div>
              <Sparkline series={gpuClockSeries} color="#a78bfa" />
            </div>
          )}

          {/* GPU power sparkline */}
          {gpuPowerSeries.length > 1 && gpuMetrics && (
            <div className="card" style={{ padding: "12px 16px" }}>
              <div style={{ display: "flex", justifyContent: "space-between", marginBottom: 8 }}>
                <span style={{ color: "#9ca3af", fontSize: 12, textTransform: "uppercase",
                  letterSpacing: "0.05em" }}>GPU Power</span>
                <span style={{ fontVariantNumeric: "tabular-nums", fontWeight: 700, fontSize: 18,
                  color: gpuMetrics.power_w != null && gpuMetrics.power_limit_w != null
                    && gpuMetrics.power_w >= gpuMetrics.power_limit_w * 0.97
                    ? "#f97316" : "#fb923c" }}>
                  {gpuMetrics.power_w?.toFixed(0) ?? ","}
                  {gpuMetrics.power_limit_w != null && (
                    <span style={{ color: "#6b7280", fontSize: 13, fontWeight: 400 }}>
                      {" / "}{gpuMetrics.power_limit_w.toFixed(0)}
                    </span>
                  )}
                  <span style={{ color: "#6b7280", fontSize: 11, marginLeft: 3 }}>W</span>
                </span>
              </div>
              <Sparkline series={gpuPowerSeries} color="#fb923c" />
            </div>
          )}

          {/* stats table */}
          <div className="card" style={{ padding: "12px 16px" }}>
            <StatRow label="Frame time (mean)" value={stats.mean_ms.toFixed(1)} unit="ms"
              warn={stats.mean_ms > 33} />
            <StatRow label="1% Low FPS"  value={stats.p1_fps  > 0 ? stats.p1_fps.toFixed(1)  : ","} />
            <StatRow label="Worst frame" value={stats.worst_ms > 0 ? stats.worst_ms.toFixed(1) : ","} unit={stats.worst_ms > 0 ? "ms" : undefined}
              warn={stats.worst_ms > 50} />
            <StatRow label="Hitches" value={stats.hitches} warn={stats.hitches > 0} />
            <StatRow label="T2 DDR used" value={stats.t2_mb.toFixed(0)} unit="MB" />
            <StatRow label="T3 NVMe used" value={stats.t3_mb.toFixed(0)} unit="MB"
              warn={stats.t3_mb > 0} />
            <StatRow label="OOM events" value={stats.oom} warn={stats.oom > 0} />
            <StatRow label="PSO compiles" value={stats.pso_compiles}
              warn={psoStalling} />
            {psoStalling && (
              <div style={{
                marginTop: 4, padding: "5px 8px",
                background: "rgba(251,191,36,0.1)",
                border: "1px solid rgba(251,191,36,0.35)",
                borderRadius: 4, fontSize: 11, color: "#fbbf24",
                lineHeight: 1.5,
              }}>
                Shader compilation stutter , gplasync reduces this.
                Enable <b>Background shader compiling</b> in All Games if not already on.
              </div>
            )}
            <StatRow label="Present count" value={stats.present_count} />
          </div>

          {/* Memory tier visualization */}
          <MemoryTierBar
            t1Used={gpuMetrics?.vram_used_mb ?? null}
            t1Total={gpuMetrics?.vram_total_mb ?? null}
            t2={stats.t2_mb}
            t3={stats.t3_mb}
          />

          {/* Live pool_brief gauge (G1) , system-wide, un-lagged */}
          <PoolBriefGauge brief={poolBrief} />

          {/* ── GPU Metrics (NVML) ───────────────────────────────────────────── */}
          {gpuMetrics && (
            <div className="card" style={{ padding: "12px 16px" }}>
              <div style={{ fontSize: 11, fontWeight: 600, color: "#6b7280",
                            textTransform: "uppercase", letterSpacing: "0.05em",
                            marginBottom: 8 }}>
                GPU
              </div>
              <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr", gap: "0 24px" }}>
                <StatRow label="Temperature"  value={gpuMetrics.temp_c  ?? ","} unit={gpuMetrics.temp_c  != null ? "°C" : undefined}
                         warn={gpuMetrics.temp_c != null && gpuMetrics.temp_c > 85}
                         icon={<Icon.Thermometer />} />
                <StatRow label="Fan"         value={gpuMetrics.fan_pct  ?? ","} unit={gpuMetrics.fan_pct != null ? "%" : undefined}
                         icon={<Icon.Fan />} />
                <StatRow label="GPU Clock"   value={gpuMetrics.clock_gpu_mhz ?? ","} unit={gpuMetrics.clock_gpu_mhz != null ? "MHz" : undefined}
                         icon={<Icon.Activity />} />
                <StatRow label="Mem Clock"   value={gpuMetrics.clock_mem_mhz ?? ","} unit={gpuMetrics.clock_mem_mhz != null ? "MHz" : undefined}
                         icon={<Icon.MemChip />} />
                <StatRow label="GPU Load"    value={gpuMetrics.gpu_util_pct ?? ","} unit={gpuMetrics.gpu_util_pct != null ? "%" : undefined}
                         warn={gpuMetrics.gpu_util_pct != null && gpuMetrics.gpu_util_pct > 95}
                         icon={<Icon.Cpu />} />
                <StatRow label="VRAM Load"   value={gpuMetrics.mem_util_pct ?? ","} unit={gpuMetrics.mem_util_pct != null ? "%" : undefined}
                         icon={<Icon.Layers />} />
                <StatRow label="Power"
                         value={gpuMetrics.power_w != null
                           ? `${gpuMetrics.power_w.toFixed(0)}${gpuMetrics.power_limit_w != null ? ` / ${gpuMetrics.power_limit_w.toFixed(0)}` : ""}`
                           : ","}
                         unit={gpuMetrics.power_w != null ? "W" : undefined}
                         icon={<Icon.Power />} />
                <StatRow label="VRAM Used"
                         value={gpuMetrics.vram_used_mb != null
                           ? `${gpuMetrics.vram_used_mb.toLocaleString()}${gpuMetrics.vram_total_mb != null ? ` / ${gpuMetrics.vram_total_mb.toLocaleString()}` : ""}`
                           : ","}
                         unit={gpuMetrics.vram_used_mb != null ? "MB" : undefined}
                         icon={<Icon.Package />} />
                {gpuMetrics.clock_gpu_mhz != null && gpuMetrics.power_w != null && gpuMetrics.power_w > 5 && (
                  <StatRow
                    label="Efficiency"
                    value={(gpuMetrics.clock_gpu_mhz / gpuMetrics.power_w).toFixed(1)}
                    unit="MHz/W"
                  />
                )}
              </div>
            </div>
          )}

          {/* ── GreenBoost Activity (dataflux) ──────────────────────────────── */}
          <div className="card" style={{ padding: "12px 16px" }}>
            <div style={{ fontSize: 11, fontWeight: 600, color: "#6b7280",
                          textTransform: "uppercase", letterSpacing: "0.05em",
                          marginBottom: 8 }}>
              GreenBoost Activity
            </div>
            {dataflux.length === 0 ? (
              <div style={{ fontSize: 11, color: "#374151" }}>
                No recent activity , nothing from core GreenBoost, gaming
                sessions, or tier moves in the last window.
              </div>
            ) : (
              <div className="telemetry-log" style={{ display: "flex", flexDirection: "column", gap: 4 }}>
                {dataflux.map((ev, i) => (
                  <div key={i} style={{
                    display: "flex", alignItems: "baseline", gap: 8,
                    fontSize: 11, color: "#9ca3af",
                  }}>
                    <span style={{
                      color: "#22d3ee", fontFamily: "monospace",
                      minWidth: 92, flexShrink: 0,
                    }}>
                      {String(ev.kind ?? "event")}
                    </span>
                    <span>{summarizeDataflux(ev)}</span>
                  </div>
                ))}
              </div>
            )}
          </div>
        </div>

        {/* Layer log , its own tab now. Kept mounted rather than unmounted
            so the stream, the 500-line ring buffer and the recording state
            survive tab switches. */}
        <div style={{
          display: liveTab === "log" ? "flex" : "none",
          flex: 1, flexDirection: "column", minWidth: 0,
        }}>
          <div style={{ color: "#6b7280", fontSize: 12, marginBottom: 6,
            textTransform: "uppercase", letterSpacing: "0.05em", display: "flex", alignItems: "center", gap: 8 }}>
            <span>Layer log , {logs.length} lines</span>
            {streaming && <span style={{ color: "#22c55e" }}>● live</span>}
            {recording && <span style={{ color: "#f87171" }}>● rec ({recordedCount} samples)</span>}
          </div>
          <div ref={logsRef} style={{
            flex: 1, overflowY: "auto", background: "#0a0a0a", borderRadius: 6,
            border: "1px solid #1f2937", padding: "8px 10px",
            fontFamily: "monospace", fontSize: 11, lineHeight: 1.6,
            color: "#6b7280",
          }}>
            {logs.length === 0
              ? <span style={{ color: "#374151" }}>No log lines yet. Start stream and launch a game.</span>
              : logs.map((l, i) => {
                  const isStats = l.startsWith("GreenBoost|") || l.startsWith("GreenBoost-GL|");
                  const isErr   = l.includes("[error]") || l.includes("ERROR");
                  const isWarn  = l.includes("WARN") || l.includes("warn");
                  return (
                    <div key={i} style={{
                      color: isStats ? "#22d3ee" : isErr ? "#f87171" : isWarn ? "#fbbf24" : "#6b7280",
                      whiteSpace: "pre-wrap", wordBreak: "break-all",
                    }}>
                      {l}
                    </div>
                  );
                })
            }
          </div>
        </div>
      </div>
    </div>
  );
}
