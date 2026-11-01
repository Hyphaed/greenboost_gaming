// Copyright 2026 Ferran Duarri , GPL v2
// Live GPU readings as bars: a bar answers "how close to the limit?" faster than a number does.
import type { GpuMetrics, PoolBrief } from "../types";

function Gauge({ label, value, detail, pct, warnAt = 90 }: { label: string; value: string; detail?: string; pct: number | null; warnAt?: number }) {
  const p = pct == null ? 0 : Math.max(0, Math.min(100, pct));
  const warn = pct != null && pct >= warnAt;
  return (
    <div className={`gauge${warn ? " gauge-warn" : ""}`} role="group" aria-label={label}>
      <div className="gauge-head">
        <span className="gauge-label">{label}</span>
        <span className="gauge-value">{value}</span>
      </div>
      <div className="gauge-track" role="progressbar" aria-valuemin={0} aria-valuemax={100} aria-valuenow={Math.round(p)} aria-label={label}>
        <div className="gauge-fill" style={{ width: `${p}%` }} />
      </div>
      {detail && <div className="gauge-detail">{detail}</div>}
    </div>
  );
}

const mb = (n: number) => (n >= 1024 ? `${(n / 1024).toFixed(1)} GB` : `${n} MB`);

export function LiveGauges({ m, pool }: { m: GpuMetrics | null; pool: PoolBrief | null }) {
  const vramPct = m?.vram_used_mb != null && m.vram_total_mb ? (m.vram_used_mb / m.vram_total_mb) * 100 : null;
  const powerPct = m?.power_w != null && m.power_limit_w ? (m.power_w / m.power_limit_w) * 100 : null;
  const t2 = pool ? (pool.t2_alloc_mb ?? pool.t2_alloc_gb * 1024) : null;
  return (
    <section aria-labelledby="live-title">
      <h2 id="live-title" className="section-title">Live GPU</h2>
      <div className="gauge-grid">
        {m && <>
          <Gauge label="Video memory" pct={vramPct} warnAt={88}
                 value={m.vram_used_mb != null ? `${(m.vram_used_mb / 1024).toFixed(1)} / ${m.vram_total_mb ? (m.vram_total_mb / 1024).toFixed(1) : "?"} GB` : "–"}
                 detail={t2 != null && t2 > 0 ? `${mb(t2)} spilled to system memory` : undefined} />
          <Gauge label="GPU load" pct={m.gpu_util_pct ?? null} warnAt={101} value={m.gpu_util_pct != null ? `${m.gpu_util_pct}%` : "–"}
                 detail={m.clock_gpu_mhz != null ? `${m.clock_gpu_mhz} MHz core · ${m.clock_mem_mhz ?? "–"} MHz memory` : undefined} />
          <Gauge label="Power" pct={powerPct} warnAt={95} value={m.power_w != null ? `${m.power_w.toFixed(0)} / ${m.power_limit_w?.toFixed(0) ?? "?"} W` : "–"} />
          <Gauge label="Temperature" pct={m.temp_c != null ? ((m.temp_c - 30) / 65) * 100 : null} warnAt={((85 - 30) / 65) * 100}
                 value={m.temp_c != null ? `${m.temp_c} °C` : "–"} detail={m.fan_pct != null ? (m.fan_pct === 0 ? "Fans idle (zero-RPM)" : `Fans at ${m.fan_pct}%`) : undefined} />
        </>}
        {!m && pool && t2 != null && (
          <Gauge label="System memory pool" pct={pool.t2_max_gb ? (t2 / (pool.t2_max_gb * 1024)) * 100 : null} value={`${mb(t2)} of ${pool.t2_max_gb} GB`} />
        )}
      </div>
    </section>
  );
}
