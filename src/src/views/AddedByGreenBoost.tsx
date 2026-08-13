import { useState, useEffect, useCallback } from "react";
import { invoke } from "@tauri-apps/api/core";
import { CollapsibleSection } from "../components/CollapsibleSection";
import type { GlobalSettingsState } from "../types";

interface Feature {
  title: string;
  tagline: string;
  whatItDoes: string;
  whyLinuxGap: string;
  howToVerify: string;
  screenshot?: string; // placeholder note , no fabricated images
  // Boolean GlobalSettingsState field this feature's toggle controls.
  // Omitted for features with no real on/off dimension (kernel-level
  // behavior, one-off tools/actions, or automatic bookkeeping) , those
  // stay descriptive-only rather than getting a fake switch.
  settingKey?: keyof GlobalSettingsState;
}

const FEATURES: { category: string; items: Feature[] }[] = [
  {
    category: "Live overlay",
    items: [
      {
        title: "On-screen display: GPU info + GreenBoost memory tiers",
        tagline: "See your GPU stats and how much system RAM GreenBoost is using to extend VRAM, live, in-game.",
        whatItDoes:
          "Turns on MangoHud's overlay (FPS, frametime graph, GPU temp/power/clocks, "
          + "CPU load, VRAM/RAM usage) and adds one GreenBoost-specific line on top of "
          + "it: live T1/T2/T3 memory-tier occupancy , how much is in real GPU VRAM "
          + "(T1), how much has spilled into system DDR (T2), and how much is on NVMe "
          + "(T3) , read directly from the kernel module's own live counters.",
        whyLinuxGap:
          "MangoHud on its own has no awareness of GreenBoost's kernel module at all; "
          + "there is no first-party way to see tier occupancy without a terminal open "
          + "on the side. GreenBoost wires MangoHud's own `exec=` directive to the "
          + "kernel module's live status file, so the same overlay you already use for "
          + "FPS shows this too , no separate window, no MangoHud fork.",
        howToVerify:
          "Turn this on, launch a game, and look for the extra GreenBoost line in the "
          + "overlay. You can see the exact same numbers it's reading from any time via "
          + "`cat /sys/class/greenboost/greenboost/pool_brief` in a terminal.",
        settingKey: "mangohud_enabled",
      },
    ],
  },
  {
    category: "Shader compilation & frame pacing",
    items: [
      {
        title: "Background shader compiling (dxvk-gplasync)",
        tagline: "Removes the freeze when something new appears on screen.",
        whatItDoes:
          "Proton/DXVK has to compile a shader the first time your GPU sees a new "
          + "material, effect, or camera angle , normally that compile blocks the "
          + "render thread and you see a stutter. GreenBoost stages the "
          + "dxvk-gplasync overlay into every game's Proton prefix automatically, "
          + "which moves that compilation to a background thread instead, and "
          + "keeps a persistent on-disk cache so a game you've played before "
          + "starts warm.",
        whyLinuxGap:
          "This isn't something NVIDIA ships at all, on any platform , gplasync "
          + "is a community DXVK fork that Linux/Proton gamers have had to install "
          + "and manage by hand, per-game, for years. GreenBoost is what "
          + "automates it: fetch, stage, keep updated, one toggle.",
        howToVerify:
          "Play a shader-heavy game's first 2–3 minutes in a new area with the "
          + "toggle off, then on (Global Settings → GreenBoost Runtime → Proton + "
          + "System). Record a MangoHud frametime graph (Update DLSS's sibling "
          + "wrapper already exports it, or run `mangohud %command%` in Steam "
          + "launch options) for both runs , the spikes on first-encounter with a "
          + "new shader should be visibly shorter and less frequent with it on.",
        settingKey: "gplasync",
      },
      {
        title: "Persistent VkPipelineCache",
        tagline: "The SECOND time you play, the game loads faster than the first.",
        whatItDoes:
          "Saves the driver's already-compiled shader pipelines to disk "
          + "(~/.local/share/greenboost/proton-cache/vk-pipeline/<AppID>.bin) and "
          + "re-injects them on your next launch, so shaders compiled last "
          + "session don't have to be recompiled from scratch this session.",
        whyLinuxGap:
          "Vulkan's own VK_EXT_pipeline_cache_control exists, but nothing wires "
          + "it up automatically per-game on Linux , this is plumbing GreenBoost "
          + "built, not something the driver or Proton does by itself.",
        howToVerify:
          "Time how long it takes to reach a specific in-game moment (e.g. first "
          + "combat encounter) on a completely fresh install vs. your second "
          + "session of the same game , the gap should shrink noticeably with the "
          + "cache warm.",
        settingKey: "vk_pipeline_cache",
      },
    ],
  },
  {
    category: "Memory & performance under the hood",
    items: [
      {
        title: "GPU memory overflow to system RAM (T1/T2/T3 tiering)",
        tagline: "Extends what your graphics card's memory can hold.",
        whatItDoes:
          "GreenBoost's kernel module can spill GPU allocations that don't fit "
          + "in real VRAM (T1) into system RAM (T2) and, beyond that, NVMe "
          + "storage (T3) , useful for GreenBoost's own AI-inference workloads "
          + "sharing this machine with games.",
        whyLinuxGap:
          "AMD's own Linux driver (RADV/amdgpu) does something like this "
          + "natively for any Vulkan game , but NVIDIA's Linux Vulkan driver has "
          + "no automatic VRAM-oversubscription at all (confirmed directly on "
          + "the NVIDIA developer forums), unlike the Windows driver. Be precise "
          + "about scope here: this tiering is currently real and active for "
          + "GreenBoost's own CUDA-based AI inference, not for an arbitrary "
          + "game's own Vulkan/DirectX allocations , that gap is a genuinely "
          + "unsolved problem industry-wide on NVIDIA/Linux, not something this "
          + "app can currently claim to close for gaming specifically.",
        howToVerify:
          "Query live tier occupancy any time via the Status view's \"T2 DDR "
          + "Used\" tile (Live GPU section) or the Live view's \"Live Pool "
          + "State\" gauge , both read the kernel module's live counters "
          + "directly, MB-precision. `cat /sys/class/greenboost/greenboost/status` "
          + "shows the same numbers in a terminal if you want the raw source. "
          + "Meaningful mainly when running local AI inference alongside a game "
          + ", not something a game's own frame-time will reflect today. This "
          + "always runs whenever the kernel module is loaded , there's no "
          + "app-level on/off switch for it.",
      },
      {
        title: "Performance Lock (CPU governor + GPU clocks/power)",
        tagline: "One toggle: your whole system stops holding back for battery life.",
        whatItDoes:
          "For the length of a game session, forces the CPU governor to "
          + "performance mode, locks GPU/memory clocks, and sets NVIDIA "
          + "PowerMizer to \"Prefer Max Performance\" , then puts everything back "
          + "to normal automatically the moment you quit.",
        whyLinuxGap:
          "NVIDIA App (Windows-only) has a broadly similar \"Performance\" mode. "
          + "There is no NVIDIA-provided equivalent on Linux at all , this is "
          + "GreenBoost reimplementing that capability using nvidia-smi, "
          + "cpupower, and PowerMizer directly.",
        howToVerify:
          "Watch `nvidia-smi --query-gpu=clocks.sm,power.draw --format=csv -l 1` "
          + "in a terminal while launching a game with the toggle on vs off , "
          + "clocks should jump to (near) max immediately with it on, instead of "
          + "ramping up gradually under load.",
        settingKey: "perf_lock",
      },
      {
        title: "Pause desktop effects while playing",
        tagline: "Your desktop's own animations stop competing with the game for GPU time.",
        whatItDoes:
          "Suspends compositor effects (KWin/GNOME Shell animations) for the "
          + "duration of the game, restoring them the instant you quit.",
        whyLinuxGap:
          "Desktop-environment-specific, and no upstream game launcher on Linux "
          + "does this automatically today , usually a manual per-DE setting "
          + "most players never discover.",
        howToVerify:
          "Compare 1% low framerates (MangoHud) in a CPU-bound scene with the "
          + "toggle on vs off , the effect is small but consistent on "
          + "compositor-heavy desktops.",
        settingKey: "compositor_suspend",
      },
    ],
  },
  {
    category: "DLSS & upscaling management",
    items: [
      {
        title: "Multi-version DLSS/Streamline library cache",
        tagline: "Every version you've ever fetched stays available , pick any of them, per game.",
        whatItDoes:
          "Fetches DLSS Super Resolution, Frame Generation, Ray Reconstruction, "
          + "and the Streamline plumbing DLLs from NVIDIA's own official GitHub "
          + "repos, keeps every distinct version ever downloaded (not just the "
          + "latest), and lets you pick exactly which one to install into any "
          + "given game from a per-game dropdown , including reverting to the "
          + "exact version the game originally shipped with.",
        whyLinuxGap:
          "NVIDIA App's DLSS-swap feature is Windows-only. On Linux, updating a "
          + "game's DLSS files has meant manually downloading DLLs and copying "
          + "them into a Wine prefix by hand , no version history, no safe "
          + "revert. Nothing else on Linux tracks \"what did this game originally "
          + "ship with\" at all.",
        howToVerify:
          "Games view → any game with detected libraries → DLSS SETTINGS. Fetch "
          + "a version, then check the dropdown shows both \"Shipped: vX\" and "
          + "every \"Cached: vY\" entry , visually compare image sharpness at "
          + "the same in-game spot across two DLSS model versions if you want "
          + "to see the quality difference directly. This is a manual per-DLL "
          + "action, not a background behavior , there's no on/off switch for it.",
      },
      {
        title: "NVIDIA Image Scaling (NIS), system-wide",
        tagline: "A universal sharpen/upscale layer that works even when a game has no upscaler of its own.",
        whatItDoes:
          "Injects NVIDIA Image Scaling via a Vulkan layer that works across "
          + "any Vulkan or Proton-translated game, not dependent on the game "
          + "itself integrating it.",
        whyLinuxGap:
          "NIS ships as an SDK NVIDIA expects each game studio to integrate "
          + "individually; very few Linux/Proton titles do. GreenBoost applies "
          + "it at the driver-adjacent layer instead, so it works regardless of "
          + "whether the game developer bothered.",
        howToVerify:
          "Global Settings → GreenBoost Runtime , Vulkan Layer, turn both NIS "
          + "toggles on with the Upscale ratio slider below 100%, and compare a "
          + "static scene's sharpness/detail before and after , take your own "
          + "screenshot pair if you want a visual reference for this specific "
          + "machine's actual output. The toggle here mirrors that same setting; "
          + "sharpness/scale/dispatch-mode tuning stays in Global Settings.",
        settingKey: "nis_enable",
      },
      {
        title: "NVIDIA Reflex, system-wide",
        tagline: "Lower input lag, even in games that never added Reflex support themselves.",
        whatItDoes:
          "Injects VK_NV_low_latency2 markers so the driver paces how far ahead "
          + "your CPU gets from your GPU, shortening the delay between input "
          + "and the frame that shows it.",
        whyLinuxGap:
          "Same integration problem as NIS , Reflex needs per-game SDK "
          + "integration upstream; GreenBoost's layer applies it independent of "
          + "whether the game shipped it.",
        howToVerify:
          "Needs a Reflex Analyzer-capable monitor/mouse combo for a hard "
          + "number, same as on Windows. Without that hardware, the honest "
          + "signal is felt input responsiveness in fast-paced sections, not a "
          + "number this app can show you today.",
        settingKey: "reflex_enable",
      },
    ],
  },
  {
    category: "Visibility NVIDIA's Linux driver doesn't give you",
    items: [
      {
        title: "Driver update status: checked vs. update available",
        tagline: "Know your driver situation without running apt commands yourself.",
        whatItDoes:
          "Checks the installed NVIDIA driver against your system package "
          + "manager and clearly distinguishes three states: not yet checked, "
          + "checked and up to date, or a specific newer version available.",
        whyLinuxGap:
          "The NVIDIA Linux driver has no update-notification UI of any kind , "
          + "you either know to run `apt list --upgradable` yourself or you "
          + "don't find out. Windows users get this from NVIDIA App/GeForce "
          + "Experience; Linux users get nothing from NVIDIA at all.",
        howToVerify:
          "Status view , the Driver row shows the current state live. This is "
          + "an on-demand check, not a background behavior , there's no "
          + "on/off switch for it.",
      },
      {
        title: "\"Upgraded from shipped\" tracking on every DLSS file",
        tagline: "Always know whether a file is still what the game shipped, or something you changed.",
        whatItDoes:
          "The first time GreenBoost ever touches a DLL, it snapshots the "
          + "original permanently. Every game and settings panel shows a clear "
          + "\"⬆ upgraded from vX\" badge whenever the installed file differs "
          + "from what shipped.",
        whyLinuxGap:
          "No other tool, on either platform, tracks per-file provenance like "
          + "this , most DLSS-swapping tools (including NVIDIA's own) only ever "
          + "keep a single most-recent backup, not the true original.",
        howToVerify:
          "Update a game's DLSS files once, then check any DLL row for the "
          + "purple \"upgraded\" badge and the \"game shipped with vX\" note. "
          + "Automatic bookkeeping, always on , there's no meaningful \"off\" "
          + "state for it.",
      },
    ],
  },
  {
    category: "Storage & I/O",
    items: [
      {
        title: "DirectStorage awareness",
        tagline: "Know whether a game's fast-NVMe-loading tech is actually engaged, not just present.",
        whatItDoes:
          "DirectStorage itself is already implemented in Proton , vkd3d-proton "
          + "(the D3D12-on-Vulkan layer inside Proton) has shipped real "
          + "DirectStorage support, including GPU-accelerated GDeflate "
          + "decompression, since 2023. GreenBoost doesn't reimplement that; it "
          + "detects whether a selected game actually ships DirectStorage "
          + "(dstorage.dll/dstoragecore.dll present), whether the Proton build "
          + "Steam will launch it with is new enough to have that support, and "
          + "whether the game's install actually sits on NVMe storage , the one "
          + "condition DirectStorage needs to deliver any real benefit at all. "
          + "All three show up as a plain status line next to a detected game's "
          + "DLSS/library section.",
        whyLinuxGap:
          "Nothing on Linux tells you whether DirectStorage is actually doing "
          + "anything for a specific game and Proton build combination , you "
          + "either take it on faith or dig through vkd3d-proton changelogs "
          + "yourself. On Windows, DirectStorage's own diagnostics are equally "
          + "opaque to end users, so this isn't solving a Windows-vs-Linux gap "
          + "so much as a genuine visibility gap DirectStorage has everywhere.",
        howToVerify:
          "Select a game that ships dstorage.dll in the Games view and check "
          + "the DirectStorage status line. Cross-check the reported Proton "
          + "build against Steam's own Compatibility tab for that game, and the "
          + "storage type against `lsblk -d -o name,rota` for the disk your "
          + "Steam library actually lives on.",
      },
    ],
  },
];

export function AddedByGreenBoostView() {
  const [openCategory, setOpenCategory] = useState<string | null>(FEATURES[0].category);
  const [settings, setSettings] = useState<GlobalSettingsState | null>(null);
  const [msg, setMsg] = useState<string | null>(null);

  useEffect(() => {
    invoke<GlobalSettingsState>("get_global_settings")
      .then(setSettings)
      .catch(e => setMsg(`Load failed: ${e}`));
  }, []);

  const update = useCallback(async (patch: Partial<GlobalSettingsState>) => {
    if (!settings) return;
    const next = { ...settings, ...patch };
    setSettings(next);
    try {
      await invoke("save_global_settings", { settings: next });
      setMsg(null);
    } catch (e: any) {
      setMsg(`Save failed: ${e?.message ?? e}`);
      invoke<GlobalSettingsState>("get_global_settings").then(setSettings).catch(() => {});
    }
  }, [settings]);

  const toggle = (on: boolean, onClick: () => void) => (
    <div onClick={onClick}
         role="switch" aria-checked={on}
         tabIndex={0}
         style={{
           display: "inline-flex", alignItems: "center", flexShrink: 0,
           width: 44, height: 22, padding: 2,
           background: on ? "#76b900" : "#3a3a3a",
           borderRadius: 999, cursor: "pointer",
           transition: "background 120ms ease",
         }}>
      <div style={{
        width: 18, height: 18, borderRadius: 999,
        background: "#ffffff",
        transform: `translateX(${on ? 22 : 0}px)`,
        transition: "transform 120ms ease",
      }} />
    </div>
  );

  return (
    <div className="content-scroll">
      <div className="section-card" style={{ marginBottom: 24 }}>
        <div className="section-card-title">What GreenBoost actually adds</div>
        <p style={{ fontSize: 13, color: "#b8c0cc", lineHeight: 1.6, margin: 0 }}>
          Everything below is something GreenBoost Gaming Suite does for you
          that isn't part of a stock Linux install, and in most cases isn't
          offered by NVIDIA on Linux at all , NVIDIA App / GeForce Experience,
          the closest Windows equivalent for several of these, has never
          shipped for Linux. Each entry says plainly what it does, why the
          gap exists on Linux specifically, and how to check the effect on
          your own machine , no invented numbers, just a real method you can
          run yourself. Entries with a switch on the right control the exact
          same setting as its counterpart in Global Settings , flip it here
          or there, it's one shared value. Entries with no switch don't have
          a real on/off state (always-on kernel behavior, a manual action, or
          automatic bookkeeping) and say so plainly instead of faking one.
          Screenshots comparing before/after aren't included yet; several
          entries note where a screenshot pair would help and that's a good
          next addition once we capture real ones from this machine.
        </p>
        {msg && <p style={{ fontSize: 12, color: "#e8a000", marginTop: 10 }}>{msg}</p>}
      </div>

      {FEATURES.map(group => (
        <CollapsibleSection
          key={group.category}
          title={group.category.toUpperCase()}
          defaultOpen={openCategory === group.category}
        >
          {group.items.map(f => (
            <div key={f.title} className="gs-row"
                 style={{ display: "block", padding: "16px" }}
                 onClick={() => setOpenCategory(group.category)}>
              <div style={{ display: "flex", alignItems: "flex-start", justifyContent: "space-between", gap: 12, marginBottom: 2 }}>
                <div style={{ fontSize: 14, fontWeight: 600, color: "#e6e6e6" }}>
                  {f.title}
                </div>
                {f.settingKey && settings && (() => {
                  const key = f.settingKey!;
                  return (
                    <div onClick={e => e.stopPropagation()}>
                      {toggle(
                        !!settings[key],
                        () => update({ [key]: !settings[key] } as Partial<GlobalSettingsState>),
                      )}
                    </div>
                  );
                })()}
              </div>
              <div style={{ fontSize: 12, color: "#76b900", fontWeight: 600, marginBottom: 10 }}>
                {f.tagline}
              </div>

              <div style={{ marginBottom: 10 }}>
                <div style={{ fontSize: 11, color: "#8a9ab0", textTransform: "uppercase",
                              letterSpacing: "0.04em", marginBottom: 3 }}>What it does</div>
                <div style={{ fontSize: 12.5, color: "#d0d0d0", lineHeight: 1.6 }}>{f.whatItDoes}</div>
              </div>

              <div style={{ marginBottom: 10 }}>
                <div style={{ fontSize: 11, color: "#8a9ab0", textTransform: "uppercase",
                              letterSpacing: "0.04em", marginBottom: 3 }}>Why this is a Linux/NVIDIA gap</div>
                <div style={{ fontSize: 12.5, color: "#d0d0d0", lineHeight: 1.6 }}>{f.whyLinuxGap}</div>
              </div>

              <div>
                <div style={{ fontSize: 11, color: "#8a9ab0", textTransform: "uppercase",
                              letterSpacing: "0.04em", marginBottom: 3 }}>How to see it yourself</div>
                <div style={{ fontSize: 12.5, color: "#a5b4fc", lineHeight: 1.6 }}>{f.howToVerify}</div>
              </div>
            </div>
          ))}
        </CollapsibleSection>
      ))}
    </div>
  );
}
