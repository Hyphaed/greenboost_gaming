// Browser preview of the Tauri UI with a mocked backend, for design review and regression screenshots.
// Usage: (cd src && npx vite --port 5188 &) ; node tools/ui-preview.mjs <out-dir> [scenario]   scenario = ok | issues
// The Tauri `invoke` bridge is replaced by canned replies (taken from a real RTX 5070 box); unknown commands answer null.
import fs from "node:fs";
import path from "node:path";
import { createRequire } from "node:module";

const require = createRequire(`${process.env.STUDIO_DIR ?? `${process.env.HOME}/Dev/svg-animation-studio`}/apps/studio/package.json`);
const { chromium } = require("playwright-core");
const out = process.argv[2] ?? "/tmp/gb-ui"; const scenario = process.argv[3] ?? "ok";
fs.mkdirSync(out, { recursive: true });

const mock = (scenario) => `(() => {
  const issues = ${JSON.stringify(scenario === "issues")};
  const replies = {
    get_status: { module: issues ? "Not loaded" : "Loaded", module_version: "3.5", vulkan_layer: issues ? "Missing" : "Installed", gl_layer: issues ? "Missing" : "Installed", nvidia_driver: "595.84", cpu_governor: issues ? "powersave" : "performance",
      proton_installed: true, cpu_name: "Intel(R) Core(TM) i9-14900KF", total_ram_gb: 61.4, kernel_version: "7.1.8-hyphaed", session_type: "wayland", greenboost_gaming_mode: false },
    get_gpu: { name: "NVIDIA GeForce RTX 5070", temp: "41 °C", power_limit: "250 W", power_usage: "22.1 W", core_clock_offset: "0", mem_clock_offset: "0", fan_speed: "Idle (zero-RPM)" },
    check_nvidia_update: { update_available: issues, available_version: "595.97", installed_version: "595.84", update_command: null, source: "apt", checked: true },
    poll_gpu_metrics: { temp_c: issues ? 87 : 41, power_w: issues ? 236 : 19, power_limit_w: 250, clock_gpu_mhz: issues ? 2550 : 337, clock_mem_mhz: issues ? 14001 : 810, gpu_util_pct: issues ? 99 : 7, mem_util_pct: 12,
      vram_used_mb: issues ? 11120 : 2692, vram_total_mb: 12227, fan_pct: issues ? 78 : 0, throttle_reasons: issues ? ["power cap"] : [], throttle_known: true },
    get_pool_brief: { t1_gb: 11, t2_alloc_gb: issues ? 1.4 : 0, t2_alloc_mb: issues ? 1434 : 0, t2_max_gb: 42, t2_pct: 0, t3_alloc_gb: 0, t3_max_gb: 73, t3_pressure: "ok", kv_reserve_mb: 2048, kv_t2_mb: 0 },
    get_session_history: [ { appid: "1245620", game_name: "ELDEN RING", gpu: "RTX 5070", vram_mb: 12227, peak_vram_mb: 9800, vram_samples: 40, duration_s: 5400, rc: 0, ts: new Date(Date.now()-3600e3).toISOString() },
                           { appid: "1091500", game_name: "Cyberpunk 2077", gpu: "RTX 5070", vram_mb: 12227, peak_vram_mb: 11300, vram_samples: 30, duration_s: 2400, rc: 0, ts: new Date(Date.now()-86400e3).toISOString() } ],
    get_controllers: [], get_active_gpu_profile: null,
    get_global_settings: new Proxy({ nis_sharpness: 0.5, nis_scale: 1, dlss_preset: "default", close_action: "tray", proton_upstream: "", shader_threads: 22, shader_cache_gb: 8 }, { get: (t, k) => (k in t ? t[k] : (typeof k === "string" ? (/config|path|hud|env|preset|args|upstream|action|name|mode|tool|vars|dir/.test(k) ? "" : true) : undefined)) }),
    scan_games: [],
    get_games: [
      { name: "ELDEN RING", appid: "1245620", path: "/mnt/games/steamapps/common/ELDEN RING/Game", installed: true, is_optimized: true, has_backup: true,
        dlls: [ { name: "nvngx_dlss.dll", path: "/mnt/games/steamapps/common/ELDEN RING/Game/nvngx_dlss.dll", version: "3.7.10.0", tech_type: "DLSS" }, { name: "eossdk-win64-shipping.dll", path: "/x/eos.dll", version: "1.16.1.0", tech_type: "Other" } ],
        optimizations: [ { key: "dlss_preset", display: "DLSS preset", current: "K", recommended: "K", needs_change: false }, { key: "shader_cache", display: "Shader cache", current: "off", recommended: "on", needs_change: true } ] },
      { name: "Cyberpunk 2077", appid: "1091500", path: "/mnt/games/steamapps/common/Cyberpunk 2077/bin/x64", installed: true, is_optimized: false, has_backup: false,
        dlls: [ { name: "nvngx_dlss.dll", path: "/y/nvngx_dlss.dll", version: "3.5.0.0", tech_type: "DLSS" }, { name: "nvngx_dlssg.dll", path: "/y/nvngx_dlssg.dll", version: "3.7.0.0", tech_type: "Frame Generation" }, { name: "sl.interposer.dll", path: "/y/sl.dll", version: "2.4.0.0", tech_type: "Streamline" } ],
        optimizations: [ { key: "dlss_preset", display: "DLSS preset", current: "default", recommended: "K", needs_change: true } ] },
      { name: "Hades II", appid: "1145350", path: "/mnt/games/steamapps/common/Hades II", installed: true, is_optimized: false, has_backup: false, dlls: [], optimizations: [] },
    ],
    list_cached_dlls: [ { name: "nvngx_dlss.dll", version: "310.2.1.0", sha256: "ab12cd34ef56", source: "NVIDIA", fetched_at: 1761700000, size_bytes: 31457280, path: "/c/310/nvngx_dlss.dll" }, { name: "nvngx_dlss.dll", version: "3.7.10.0", sha256: "99aa88bb77cc", source: "NVIDIA", fetched_at: 1761600000, size_bytes: 28311552, path: "/c/3710/nvngx_dlss.dll" } ],
    get_suite_version: "0.9.4",
    query_displays: [
      { name: "DP-1", connected: true, enabled: true, primary: true, current_mode: "2560x1440", current_rate: 74.968, vrr: true, gsync_compatible: true, connector: "DisplayPort", width_mm: 597, height_mm: 336,
        modes: [ { resolution: "2560x1440", rates: [74.968, 59.951, 143.912] }, { resolution: "1920x1080", rates: [60, 74.97, 143.85] } ] },
      { name: "HDMI-A-1", connected: true, enabled: true, primary: false, current_mode: "2560x1440", current_rate: 60, vrr: false, gsync_compatible: false, connector: "HDMI", width_mm: 597, height_mm: 336,
        modes: [ { resolution: "2560x1440", rates: [59.951, 60] }, { resolution: "1920x1080", rates: [60, 50] } ] },
    ],
    get_night_light: { enabled: false, temperature: 4500, schedule_auto: true, manual_from: 22, manual_to: 7, available: true },
    list_gpu_profiles: ["Quiet", "Balanced", "Performance"],
    get_game_overrides: { fps_cap: 0, nis: null, hdr: false, dlss_preset: "", reflex: false, governor: "", env: {}, wrappers: { gamemode: true, mangohud: false, gamescope: [] }, gpu_profile: "", gplasync: null, perf_lock: null, compositor_suspend: null, vk_pipeline_cache: null, external_dlls_enabled: false, external_dll_dir: "", external_dll_targets: {}, external_dll_disabled: [], dlss5_enabled: false, dlss5_proxy_dll: "" },
    get_game_settings: [ { title: "Graphics", settings: [ { key: "dlss_preset", display: "DLSS preset", current: "default", recommended: "K", needs_change: true }, { key: "shader_cache", display: "Shader cache", current: "on", recommended: "on", needs_change: false } ] }, { title: "Launch", settings: [ { key: "gamemode", display: "Game mode", current: "off", recommended: "on", needs_change: true } ] } ],
    get_dlss_status: { scanned: 3, out_of_date: 2, scanned_at: Math.floor(Date.now()/1000) - 7200, source: { nvngx: "310.2.1.0", nvngx_url: "", streamline: "2.8.0" }, findings: [
      { path: "/y/nvngx_dlss.dll", family: "dlss", name: "nvngx_dlss.dll", pretty: "DLSS Super Resolution", current: "3.5.0.0", latest: "310.2.1.0", via: "NVIDIA", needs_update: true, game_root: "/y", shipped: "3.5.0.0", upgraded: false, can_restore_shipped: false, restore_points: [] },
      { path: "/y/nvngx_dlssg.dll", family: "dlssg", name: "nvngx_dlssg.dll", pretty: "DLSS Frame Generation", current: "3.7.0.0", latest: "310.2.1.0", via: "NVIDIA", needs_update: true, game_root: "/y", shipped: "3.7.0.0", upgraded: false, can_restore_shipped: false, restore_points: [] },
      { path: "/y/sl.interposer.dll", family: "streamline", name: "sl.interposer.dll", pretty: "Streamline", current: "2.8.0.0", latest: "2.8.0", via: "NVIDIA", needs_update: false, game_root: "/y", shipped: "2.4.0.0", upgraded: true, can_restore_shipped: true, restore_points: [ { path: "/y/a.bak", label: "2.4.0.0", mtime: Math.floor(Date.now()/1000) - 86400 } ] } ] },
    get_dlss_sources: { sources: [ { id: "nvidia", label: "NVIDIA GitHub", description: "Latest release", active: true } ], active: "nvidia", custom_url: "", streamline_origin: "" },
    list_dlss_preset_choices: [ { id: "default", label: "Default (game decides)" }, { id: "render_preset_k", label: "Preset K (RTX 20/30)" } ],
    list_proton_installs: [ ["GE-Proton10-1", "/p"] ],
    analyze_game_sessions: { appid: "1245620", game_name: "ELDEN RING", session_count: 7, avg_vram_mb: 8600, peak_vram_mb: 9800, vram_includes_other_apps: false, avg_duration_min: 95, total_play_hours: 11, gpu_total_vram_mb: 12227, peak_t2_mb: 0 },
    get_compat_tool: "GE-Proton10-1",
    get_dataflux_recent: [],
  };
  window.__TAURI_INTERNALS__ = {
    invoke: async (cmd) => (cmd in replies ? replies[cmd] : /^list_/.test(cmd) ? [] : null),
    transformCallback: () => 0, convertFileSrc: (p) => p, metadata: { currentWindow: { label: "main" }, currentWebview: { label: "main", windowLabel: "main" } },
  };
})();`;

const browser = await chromium.launch({ executablePath: "/usr/bin/google-chrome", headless: true, args: ["--no-sandbox", "--disable-gpu"] });
const views = [["Status","status"],["Games","games"],["Displays","displays"],["Profile","profile"],["Live","live"],["About","about"]];
const errors = [];
for (const [w, h] of [[1500, 940], [1100, 720]]) {
  const page = await (await browser.newContext({ viewport: { width: w, height: h }, reducedMotion: "reduce" })).newPage();
  page.on("pageerror", (e) => errors.push(e.message));
  await page.addInitScript(mock(scenario));
  await page.goto(`http://localhost:${process.env.PORT ?? 5188}/`); await page.waitForTimeout(1500);
  for (const [label, file] of views) {
    await page.locator(".nav-item", { hasText: label }).first().click({ timeout: 5000 }).catch((e) => errors.push("nav " + label + ": " + String(e.message).split("\n")[0]));
    await page.waitForTimeout(1000);
    await page.screenshot({ path: path.join(out, `${file}-${w}-${scenario}.png`) });
    if (w === 1500 && file !== "status") { await page.setViewportSize({ width: 1500, height: 2000 }); await page.waitForTimeout(400); await page.screenshot({ path: path.join(out, `${file}-tall-${scenario}.png`) }); await page.setViewportSize({ width: w, height: h }); }
    const subs = { profile: ["Fan Curve", "Profiles"], about: ["Preferences"], displays: ["Night Light"], live: ["Layer log"] }[file] ?? [];
    for (const t of subs) {
      const b = page.locator(".sub-nav-tab, [role=tab], button").filter({ hasText: new RegExp("^\\s*" + t + "\\s*$") }).first();
      if (await b.count()) { await b.click(); await page.waitForTimeout(700); await page.screenshot({ path: path.join(out, `${file}-${t.toLowerCase().replace(/ /g, "")}-${w}-${scenario}.png`) });
        if (w === 1500) { await page.setViewportSize({ width: 1500, height: 1700 }); await page.waitForTimeout(300); await page.screenshot({ path: path.join(out, `${file}-${t.toLowerCase().replace(/ /g, "")}-tall-${scenario}.png`) }); await page.setViewportSize({ width: w, height: h }); } }
    }
    if (subs.length) { const first = page.locator(".sub-nav-tab, [role=tab]").first(); if (await first.count()) await first.click().catch(() => {}); }
    if (file === "games") {
      const tab = page.getByText("This Game", { exact: true }); if (await tab.count()) { await tab.first().click(); await page.waitForTimeout(800); await page.screenshot({ path: path.join(out, `game-${w}-${scenario}.png`) }); if (w === 1500) { await page.getByText("Cyberpunk 2077").first().click(); await page.setViewportSize({ width: 1500, height: 2000 }); await page.waitForTimeout(800); await page.screenshot({ path: path.join(out, `game-tall-${scenario}.png`) }); await page.setViewportSize({ width: w, height: h }); } }
    }
  }
  await page.close();
}
console.log(errors.length ? "page errors: " + errors.slice(0, 3).join(" | ") : "no page errors");
await browser.close();
