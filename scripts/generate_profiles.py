#!/usr/bin/env python3
"""
Generate per-game optimization profiles for GreenBoost Gaming Suite.
Data sourced from known game databases, DLSS/DLAA/RT support lists, and engine documentation.
Run from the greenboost_gaming root: python3 scripts/generate_profiles.py
"""

import json
import os
import pathlib

OUTPUT_DIR = pathlib.Path(__file__).parent.parent / "profiles" / "per-game"
OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

# ─── helpers ────────────────────────────────────────────────────────────────

def tier(ultra, high, medium, low):
    return {"ultra": ultra, "high": high, "medium": medium, "low": low}

def dlss(u="Quality", h="Balanced", m="Performance", l="UltraPerformance"):
    return tier(u, h, m, l)

def fsr(u="Quality", h="Balanced", m="Performance", l="UltraPerformance"):
    return tier(u, h, m, l)

def xess(u="Quality", h="Balanced", m="Performance", l="Performance"):
    return tier(u, h, m, l)

def framegen(u="true", h="true", m="false", l="false"):
    return tier(u, h, m, l)

def reflex(u="enabled+boost", h="enabled+boost", m="enabled", l="enabled"):
    return tier(u, h, m, l)

def shadow(u="Ultra", h="High", m="Medium", l="Low"):
    return tier(u, h, m, l)

def texq(u="4", h="3", m="2", l="1"):
    return tier(u, h, m, l)

def aamod(u="DLSS", h="DLSS", m="DLSS", l="DLSS"):
    return tier(u, h, m, l)

# ─── base templates per engine ───────────────────────────────────────────────

def unreal_base(desc, dlss_s=True, rt=False, framegen_s=False, lumen=False, extra=None):
    s = {
        "r.MotionBlurQuality": "0",
        "r.ScreenPercentage": "100",
        "r.ShaderPipelineCache.Enabled": "1",
        "r.Streaming.PoolSize": "2048",
        "r.TextureStreaming": "1",
        "sg.ShadowQuality": tier("3", "2", "1", "0"),
        "sg.PostProcessQuality": tier("3", "2", "1", "0"),
        "sg.EffectsQuality": tier("3", "2", "1", "0"),
    }
    if dlss_s:
        s["DLSS"] = dlss()
    if framegen_s:
        s["FrameGenerationEnable"] = framegen()
    if rt:
        s["r.RayTracing"] = tier("1", "1", "0", "0")
        s["r.RayTracing.Shadows"] = tier("1", "1", "0", "0")
        s["r.RayTracing.Reflections"] = tier("1", "0", "0", "0")
        s["r.RayTracing.AmbientOcclusion"] = tier("1", "1", "0", "0")
    if lumen:
        s["r.Lumen.Reflections.Allow"] = tier("1", "1", "0", "0")
        s["r.Lumen.GlobalIllumination.Allow"] = tier("1", "1", "0", "0")
        s["r.Nanite.Enabled"] = "1"
    if extra:
        s.update(extra)
    return s

def unity_base(desc, dlss_s=True, rt=False, extra=None):
    s = {
        "targetFrameRate": "-1",
        "vSyncCount": "0",
        "shadowDistance": tier("200", "150", "100", "50"),
        "shadowCascades": tier("4", "4", "2", "1"),
        "antiAliasing": "0",
    }
    if dlss_s:
        s["DLSS"] = dlss()
    if rt:
        s["rayTracingEnabled"] = tier("true", "true", "false", "false")
    if extra:
        s.update(extra)
    return s

def source2_base(desc, dlss_s=False, fsr_s=False, extra=None):
    s = {
        "mat_queue_mode": "-1",
        "r_queued_decals": "1",
        "r_shadowrendertotexture": "1",
        "r_flashlightdepthtexture": "1",
        "fps_max": "0",
        "r_drawparticles": tier("1", "1", "1", "0"),
        "r_waterreflections": tier("1", "1", "0", "0"),
    }
    if dlss_s:
        s["DLSS"] = dlss()
    if fsr_s:
        s["FSR"] = fsr()
    if extra:
        s.update(extra)
    return s

def re_engine_base(desc, dlss_s=True, rt=False, extra=None):
    s = {
        "ImageQuality": tier("100", "85", "70", "60"),
        "MotionBlur": "Off",
        "DepthOfField": "On",
        "AnisotropicFiltering": "16",
        "ShadowQuality": tier("High", "High", "Medium", "Low"),
        "ImageFilter": "DLSS" if dlss_s else "FSR",
    }
    if dlss_s:
        s["DLSS"] = dlss("Quality", "Balanced", "Performance", "Performance")
    if rt:
        s["RayTracing"] = tier("true", "true", "false", "false")
        s["RayTracedShadows"] = tier("true", "true", "false", "false")
        s["RayTracedGI"] = tier("true", "false", "false", "false")
    if extra:
        s.update(extra)
    return s

def id_tech_base(extra=None):
    s = {
        "com_skipIntroVideo": "1",
        "r_syncFlush": "0",
        "image_anisotropy": "16",
        "r_shadowAtlasSize": tier("4096", "2048", "1024", "512"),
        "r_useShadowCaching": "1",
    }
    if extra:
        s.update(extra)
    return s

def frostbite_base(dlss_s=True, rt=False, extra=None):
    s = {
        "GstRender.Dx12Enabled": "1",
        "GstRender.MotionBlurEnable": "false",
        "GstRender.ShadowmapResolution": tier("2048", "1024", "512", "256"),
    }
    if dlss_s:
        s["GstRender.DLSS"] = dlss()
    if rt:
        s["GstRender.RaytracingEnable"] = tier("true", "true", "false", "false")
        s["GstRender.RaytracingShadowsEnable"] = tier("true", "true", "false", "false")
    if extra:
        s.update(extra)
    return s

def cryengine_base(extra=None):
    s = {
        "r_MotionBlur": "0",
        "r_VSync": "0",
        "r_AntiAliasMode": "3",
        "r_ShadowsMaxTexRes": tier("2048", "1024", "512", "256"),
        "sys_MaxFPS": "0",
    }
    if extra:
        s.update(extra)
    return s

# ─── game database ────────────────────────────────────────────────────────────
# Format: "game name lowercase": ("engine", "description", settings_dict)

GAMES = {}

def add(name, engine, description, settings):
    GAMES[name] = {"engine": engine, "description": description, "settings": settings}

# ── Counter-Strike series ──────────────────────────────────────────────────
add("counter-strike 2", "source2", "Counter-Strike 2 , maximize FPS, minimal latency for competitive play",
    source2_base("", fsr_s=True, extra={
        "r_low_latency": "2",
        "fps_max": "300",
        "r_texturefilteringquality": tier("3", "2", "1", "0"),
        "r_shadowrendertotexture": "1",
        "r_waterreflections": "0",
        "ReflexEnable": "1",
    }))

add("counter-strike global offensive", "source", "CS:GO , competitive FPS optimization",
    {"fps_max": "300", "mat_queue_mode": "-1", "r_dynamic": "0",
     "r_shadows": "0", "r_shadowrendertotexture": "0",
     "cl_interp": "0", "cl_interp_ratio": "1", "rate": "786432"})

# ── Valve / Source ─────────────────────────────────────────────────────────
add("dota 2", "source2", "Dota 2 , balanced visuals for competitive play on Source 2",
    source2_base("", fsr_s=True, extra={
        "r_deferred_height_fog": "1",
        "r_deferred_simple_sky": "1",
        "fps_max": "300",
    }))

add("team fortress 2", "source", "TF2 , maximum FPS on aging Source engine",
    {"fps_max": "300", "mat_queue_mode": "-1", "r_dynamic": "0",
     "r_maxdlights": "4", "r_shadows": "0", "cl_new_impact_effects": "0"})

add("half-life alyx", "source2", "Half-Life: Alyx , VR-optimized Source 2 settings",
    source2_base("", extra={"vr_msaa": "0", "hlvr_continuous_normal_speed": "220"}))

add("left 4 dead 2", "source", "Left 4 Dead 2 , smooth co-op experience",
    {"fps_max": "300", "mat_queue_mode": "-1", "r_shadows": "0", "r_dynamic": "0"})

# ── PUBG ──────────────────────────────────────────────────────────────────
add("pubg battlegrounds", "unreal", "PUBG , competitive battle royale, minimize input lag",
    unreal_base("", dlss_s=True, extra={
        "DLSS": dlss("Balanced", "Performance", "Performance", "UltraPerformance"),
        "ReflexEnable": tier("1", "1", "1", "0"),
        "sg.ShadowQuality": tier("2", "1", "0", "0"),
        "sg.PostProcessQuality": tier("1", "1", "0", "0"),
        "r.LandscapeLODBias": "-1",
    }))

# ── Epic Games / Unreal ────────────────────────────────────────────────────
add("fortnite", "unreal", "Fortnite , Nanite + Lumen UE5, DLSS 3.5 Frame Gen",
    unreal_base("", dlss_s=True, rt=False, framegen_s=True, lumen=True, extra={
        "DLSS": dlss("Quality", "Balanced", "Performance", "UltraPerformance"),
        "ReflexEnable": tier("2", "2", "1", "1"),
        "r.Nanite.Enabled": "1",
    }))

add("rocket league", "unreal", "Rocket League , maximize FPS for competitive play",
    unreal_base("", dlss_s=False, extra={
        "sg.ShadowQuality": tier("3", "2", "1", "0"),
        "sg.PostProcessQuality": tier("1", "1", "0", "0"),
        "bMotionBlur": "False",
        "MaxFPS": "0",
    }))

# ── Black Myth: Wukong ─────────────────────────────────────────────────────
add("black myth wukong", "unreal", "Black Myth: Wukong , UE5 Nanite/Lumen, DLSS 3.5 + Frame Gen",
    {
        "r.Lumen.Reflections.Allow": tier("1", "1", "0", "0"),
        "r.Lumen.GlobalIllumination.Allow": tier("1", "1", "0", "0"),
        "r.Nanite.Enabled": "1",
        "r.ScreenPercentage": "100",
        "r.MotionBlurQuality": "0",
        "DLSS": dlss("Quality", "Balanced", "Performance", "Performance"),
        "FrameGenerationEnable": framegen(),
        "r.ShaderPipelineCache.Enabled": "1",
        "ReflexEnable": tier("2", "2", "1", "1"),
    })

# ── Cyberpunk 2077 ─────────────────────────────────────────────────────────
add("cyberpunk 2077", "custom", "Cyberpunk 2077 , REDengine 4, DLSS 3.5 + Path Tracing",
    {
        "RayTracing": tier("PathTracing", "Ultra", "Off", "Off"),
        "DLSS": dlss("Quality", "Balanced", "Performance", "UltraPerformance"),
        "DLSSFrameGeneration": framegen(),
        "ReflexMode": reflex(),
        "MotionBlurScale": "0",
        "ChromAberr": "false",
        "DepthOfField": "true",
        "Sharpness": "0.7",
        "LensFlare": "false",
        "CrowdDensity": tier("High", "High", "Medium", "Low"),
    })

# ── Baldur's Gate 3 ───────────────────────────────────────────────────────
add("baldur's gate 3", "vulkan", "Baldur's Gate 3 , Divinity 4.0 engine, FSR 3 + Frame Gen",
    {
        "FSR": fsr("Quality", "Balanced", "Performance", "UltraPerformance"),
        "FrameGeneration": framegen(),
        "ShadowQuality": shadow("Ultra", "High", "Medium", "Low"),
        "TextureQuality": texq(),
        "SSR": tier("true", "true", "false", "false"),
        "DepthOfField": "false",
        "MotionBlur": "false",
        "VSync": "false",
    })

# ── Elden Ring ─────────────────────────────────────────────────────────────
add("elden ring", "fromsoftware", "Elden Ring , maximize framerate, disable CPU-heavy effects",
    {
        "MotionBlur": "0",
        "Vignette": "0",
        "AntiAliasing": "2",
        "TextureQuality": texq(),
        "ShadowQuality": shadow("Ultra", "High", "Medium", "Low"),
        "DLSS": dlss("Quality", "Balanced", "Performance", "Off"),
    })

# ── Hogwarts Legacy ────────────────────────────────────────────────────────
add("hogwarts legacy", "unreal", "Hogwarts Legacy , UE4, DLSS 3 + Frame Gen, heavy RT",
    unreal_base("", dlss_s=True, rt=True, framegen_s=True, extra={
        "DLSS": dlss("Quality", "Balanced", "Performance", "UltraPerformance"),
        "ReflexEnable": tier("2", "2", "1", "1"),
    }))

# ── Alan Wake 2 ────────────────────────────────────────────────────────────
add("alan wake 2", "northlight", "Alan Wake 2 , Northlight engine, DLSS 3.5 Ray Reconstruction",
    {
        "DLSS": dlss("Quality", "Balanced", "Performance", "UltraPerformance"),
        "DLSS_RR": tier("true", "true", "false", "false"),
        "FrameGeneration": framegen(),
        "ReflexMode": reflex(),
        "PathTracing": tier("true", "false", "false", "false"),
        "RayTracing": tier("true", "true", "false", "false"),
        "ShadowQuality": shadow(),
        "MotionBlur": "false",
    })

# ── STALKER 2 ──────────────────────────────────────────────────────────────
add("s.t.a.l.k.e.r. 2 heart of chornobyl", "unreal", "STALKER 2 , UE5, DLSS 3.5 + Frame Gen, heavy Lumen",
    unreal_base("", dlss_s=True, rt=False, framegen_s=True, lumen=True, extra={
        "DLSS": dlss("Quality", "Balanced", "Performance", "UltraPerformance"),
        "r.Nanite.Enabled": "1",
        "ReflexEnable": tier("2", "2", "1", "1"),
    }))

# ── Dragon Age: The Veilguard ──────────────────────────────────────────────
add("dragon age the veilguard", "frostbite", "Dragon Age: The Veilguard , Frostbite, DLSS 3 + Frame Gen",
    frostbite_base(dlss_s=True, rt=True, extra={
        "GstRender.FrameGeneration": framegen(),
        "GstRender.Reflex": reflex("enabled", "enabled", "enabled", "disabled"),
    }))

# ── Indiana Jones and the Great Circle ────────────────────────────────────
add("indiana jones and the great circle", "id tech 7", "Indiana Jones , id Tech 7, DLSS 3.5 + Frame Gen",
    id_tech_base(extra={
        "DLSS": dlss("Quality", "Balanced", "Performance", "UltraPerformance"),
        "FrameGeneration": framegen(),
        "ReflexMode": reflex(),
        "r_rayTracing": tier("1", "1", "0", "0"),
        "r_rayTracingShadows": tier("1", "1", "0", "0"),
    }))

# ── Palworld ───────────────────────────────────────────────────────────────
add("palworld", "unreal", "Palworld , UE5 large open world, DLSS 3",
    unreal_base("", dlss_s=True, lumen=True, framegen_s=True, extra={
        "DLSS": dlss("Quality", "Balanced", "Performance", "UltraPerformance"),
    }))

# ── Call of Duty ───────────────────────────────────────────────────────────
add("call of duty black ops 6", "iw", "Call of Duty Black Ops 6 , IW engine, DLSS 3 + Frame Gen",
    {
        "DLSS": dlss("Quality", "Balanced", "Performance", "UltraPerformance"),
        "FrameGeneration": framegen(),
        "ReflexMode": reflex(),
        "AntiAliasing": tier("DLSS", "DLSS", "DLSS", "TSR"),
        "ShadowMap": tier("Extra", "High", "Normal", "Low"),
        "MotionBlur": "0",
        "FilmGrain": "0",
    })

add("call of duty warzone", "iw", "Call of Duty Warzone , IW engine, DLSS 3 competitive",
    {
        "DLSS": dlss("Balanced", "Performance", "UltraPerformance", "UltraPerformance"),
        "FrameGeneration": framegen(),
        "ReflexMode": reflex(),
        "ShadowMap": tier("Normal", "Low", "Low", "Low"),
        "MotionBlur": "0",
        "FilmGrain": "0",
    })

add("call of duty modern warfare iii", "iw", "Call of Duty MWIII , IW engine, DLSS 3 + Frame Gen",
    {
        "DLSS": dlss("Quality", "Balanced", "Performance", "UltraPerformance"),
        "FrameGeneration": framegen(),
        "ReflexMode": reflex(),
        "ShadowMap": tier("Extra", "High", "Normal", "Low"),
        "MotionBlur": "0",
    })

add("call of duty modern warfare ii", "iw", "Call of Duty MWII , IW engine, DLSS 3",
    {
        "DLSS": dlss("Quality", "Balanced", "Performance", "UltraPerformance"),
        "ReflexMode": reflex(),
        "ShadowMap": tier("Extra", "High", "Normal", "Low"),
        "MotionBlur": "0",
    })

# ── Final Fantasy VII Rebirth ──────────────────────────────────────────────
add("final fantasy vii rebirth", "unreal", "FF7 Rebirth , UE4, DLSS 3 + Frame Gen",
    unreal_base("", dlss_s=True, framegen_s=True, rt=True, extra={
        "DLSS": dlss("Quality", "Balanced", "Performance", "UltraPerformance"),
        "ReflexEnable": tier("2", "2", "1", "1"),
    }))

add("final fantasy xvi", "unreal", "Final Fantasy XVI , UE4, DLSS 3 high quality mode",
    unreal_base("", dlss_s=True, rt=True, extra={
        "DLSS": dlss("Quality", "Balanced", "Performance", "UltraPerformance"),
    }))

add("final fantasy xiv online", "custom", "Final Fantasy XIV , custom engine, maximize stability",
    {
        "AntiAliasing": tier("DLSS", "TAA", "TAA", "FXAA"),
        "DLSS": dlss("Quality", "Balanced", "Performance", "Off"),
        "ShadowLOD": tier("2", "1", "0", "0"),
        "WaterWetEffects": tier("2", "1", "0", "0"),
        "TextureAnisotropicQuality": "16",
    })

# ── Monster Hunter ─────────────────────────────────────────────────────────
add("monster hunter wilds", "re engine", "Monster Hunter Wilds , RE Engine 4.0, DLSS 4 + Frame Gen",
    re_engine_base("", dlss_s=True, rt=True, extra={
        "DLSS": dlss("Quality", "Balanced", "Performance", "UltraPerformance"),
        "FrameGeneration": framegen(),
        "ReflexMode": reflex("enabled", "enabled", "enabled", "disabled"),
        "MultiFrameGen": tier("true", "false", "false", "false"),
    }))

add("monster hunter world", "re engine", "Monster Hunter: World , RE Engine, optimize for 60+ FPS",
    re_engine_base("", dlss_s=True, extra={
        "DLSS": dlss("Quality", "Balanced", "Performance", "Performance"),
    }))

add("monster hunter rise", "re engine", "Monster Hunter Rise , RE Engine, DLSS 2",
    re_engine_base("", dlss_s=True, extra={
        "DLSS": dlss("Quality", "Balanced", "Performance", "Performance"),
    }))

# ── Apex Legends ───────────────────────────────────────────────────────────
add("apex legends", "source", "Apex Legends , Source engine, maximize FPS for competitive play",
    {
        "mat_queue_mode": "-1",
        "fps_max": "300",
        "r_dynamic": "0",
        "r_shadows": "0",
        "ambient_occlusion": "0",
        "sun_shadow_depth_dimen_minmax": "512 512",
        "ReflexEnable": "1",
    })

# ── Valorant ───────────────────────────────────────────────────────────────
add("valorant", "unreal", "Valorant , UE4, maximize FPS for competitive play",
    unreal_base("", dlss_s=False, extra={
        "sg.ShadowQuality": tier("1", "1", "0", "0"),
        "sg.PostProcessQuality": tier("1", "0", "0", "0"),
        "sg.EffectsQuality": tier("1", "1", "0", "0"),
        "bShowFPSCounter": "True",
        "ReflexEnable": tier("2", "2", "2", "1"),
    }))

# ── Helldivers 2 ───────────────────────────────────────────────────────────
add("helldivers 2", "custom", "Helldivers 2 , Autodesk Stingray/custom, DLSS 3",
    {
        "DLSS": dlss("Quality", "Balanced", "Performance", "UltraPerformance"),
        "MotionBlur": "false",
        "ShadowQuality": shadow(),
        "TextureQuality": texq(),
        "SSR": tier("true", "true", "false", "false"),
    })

# ── Diablo IV ──────────────────────────────────────────────────────────────
add("diablo iv", "custom", "Diablo IV , custom engine, DLSS 3.5 + Frame Gen",
    {
        "DLSS": dlss("Quality", "Balanced", "Performance", "UltraPerformance"),
        "FrameGeneration": framegen(),
        "ReflexMode": reflex(),
        "ShadowQuality": shadow(),
        "SSAO": tier("High", "Medium", "Low", "Off"),
        "MotionBlur": "0",
    })

# ── Star Wars Outlaws ──────────────────────────────────────────────────────
add("star wars outlaws", "snowdrop", "Star Wars Outlaws , Snowdrop engine, DLSS 3.5 + Frame Gen",
    {
        "DLSS": dlss("Quality", "Balanced", "Performance", "UltraPerformance"),
        "DLSS_RR": tier("true", "true", "false", "false"),
        "FrameGeneration": framegen(),
        "ReflexMode": reflex(),
        "RayTracing": tier("true", "true", "false", "false"),
        "ShadowQuality": shadow(),
        "MotionBlur": "false",
    })

# ── Silent Hill 2 (Remake) ─────────────────────────────────────────────────
add("silent hill 2", "unreal", "Silent Hill 2 Remake , UE5, DLSS 3.5 Frame Gen, heavy Lumen",
    unreal_base("", dlss_s=True, framegen_s=True, lumen=True, extra={
        "DLSS": dlss("Quality", "Balanced", "Performance", "UltraPerformance"),
        "r.Nanite.Enabled": "1",
        "ReflexEnable": tier("2", "2", "1", "1"),
    }))

# ── Hellblade II ───────────────────────────────────────────────────────────
add("senua's saga hellblade ii", "unreal", "Hellblade II , UE5 Nanite/Lumen, DLSS 3.5 Frame Gen",
    unreal_base("", dlss_s=True, framegen_s=True, lumen=True, extra={
        "DLSS": dlss("Quality", "Quality", "Balanced", "Performance"),
        "r.Nanite.Enabled": "1",
        "ReflexEnable": tier("2", "2", "1", "1"),
        "r.Lumen.Hardware.AllowWaveOps": "1",
    }))

# ── Dragon's Dogma 2 ───────────────────────────────────────────────────────
add("dragon's dogma 2", "re engine", "Dragon's Dogma 2 , RE Engine 4.0, DLSS 3.5 + Frame Gen",
    re_engine_base("", dlss_s=True, rt=True, extra={
        "DLSS": dlss("Quality", "Balanced", "Performance", "UltraPerformance"),
        "FrameGeneration": framegen(),
        "ReflexMode": reflex("enabled", "enabled", "enabled", "disabled"),
    }))

# ── Starfield ──────────────────────────────────────────────────────────────
add("starfield", "creation engine 2", "Starfield , Creation Engine 2, FSR 3 Frame Gen",
    {
        "FSR": fsr("Quality", "Balanced", "Performance", "UltraPerformance"),
        "FrameGeneration": framegen(),
        "bEnableDepthOfField": "0",
        "bMotionBlur": "0",
        "uGridsToLoad": tier("10", "8", "6", "4"),
        "iShadowMapResolution": tier("4096", "2048", "1024", "512"),
        "fShadowDistance": tier("20000", "15000", "10000", "5000"),
    })

# ── Avatar: Frontiers of Pandora ───────────────────────────────────────────
add("avatar frontiers of pandora", "snowdrop", "Avatar: Frontiers of Pandora , Snowdrop, DLSS 3.5 + Frame Gen",
    {
        "DLSS": dlss("Quality", "Balanced", "Performance", "UltraPerformance"),
        "DLSS_RR": tier("true", "true", "false", "false"),
        "FrameGeneration": framegen(),
        "ReflexMode": reflex(),
        "RayTracing": tier("true", "true", "false", "false"),
        "ShadowQuality": shadow(),
    })

# ── ARK: Survival Ascended ────────────────────────────────────────────────
add("ark survival ascended", "unreal", "ARK: Survival Ascended , UE5, DLSS 3 + Frame Gen",
    unreal_base("", dlss_s=True, framegen_s=True, lumen=True, extra={
        "DLSS": dlss("Quality", "Balanced", "Performance", "UltraPerformance"),
        "r.Nanite.Enabled": "1",
    }))

# ── Destiny 2 ─────────────────────────────────────────────────────────────
add("destiny 2", "tiger", "Destiny 2 , Tiger engine, DLSS 3 + Frame Gen",
    {
        "DLSS": dlss("Quality", "Balanced", "Performance", "UltraPerformance"),
        "FrameGeneration": framegen(),
        "ReflexMode": reflex(),
        "ShadowQuality": shadow(),
        "DepthOfField": tier("High", "Medium", "Low", "Off"),
        "MotionBlur": "false",
    })

# ── Marvel Rivals ──────────────────────────────────────────────────────────
add("marvel rivals", "unreal", "Marvel Rivals , UE5, DLSS 3 + Frame Gen",
    unreal_base("", dlss_s=True, framegen_s=True, lumen=False, extra={
        "DLSS": dlss("Quality", "Balanced", "Performance", "UltraPerformance"),
        "ReflexEnable": tier("2", "2", "2", "1"),
    }))

# ── Path of Exile 2 ───────────────────────────────────────────────────────
add("path of exile 2", "custom", "Path of Exile 2 , custom engine, smooth performance at high density",
    {
        "DLSS": dlss("Quality", "Balanced", "Performance", "UltraPerformance"),
        "ShadowQuality": shadow("High", "High", "Medium", "Low"),
        "DynamicResolution": tier("false", "false", "true", "true"),
        "ParticleQuality": tier("High", "High", "Medium", "Low"),
        "MotionBlur": "false",
    })

add("path of exile", "custom", "Path of Exile , custom engine, performance for endgame mapping",
    {
        "TextureQuality": texq("2", "2", "1", "0"),
        "ShadowQuality": shadow("High", "Medium", "Low", "Off"),
        "EffectsQuality": tier("High", "Medium", "Low", "Very Low"),
        "FullscreenParticles": "false",
    })

# ── Rust ───────────────────────────────────────────────────────────────────
add("rust", "unity", "Rust , Unity3D, maximize FPS in high-pop servers",
    unity_base("", dlss_s=True, extra={
        "DLSS": dlss("Balanced", "Performance", "UltraPerformance", "UltraPerformance"),
        "grassDisplacement": "false",
        "objectQuality": tier("100", "75", "50", "25"),
        "terrainQuality": tier("100", "75", "50", "25"),
    }))

# ── Grand Theft Auto ───────────────────────────────────────────────────────
add("grand theft auto v", "rage", "GTA V , RAGE engine enhanced, maximize FPS",
    {
        "MSAA": "0",
        "FXAA": "0",
        "txaa": "0",
        "DLSS": dlss("Quality", "Balanced", "Performance", "Off"),
        "shadowQuality": tier("very high", "high", "normal", "low"),
        "extrashadows": tier("1", "1", "0", "0"),
        "grassQuality": tier("very high", "high", "normal", "low"),
        "postFX": tier("very high", "high", "normal", "low"),
        "motionblur": "false",
    })

add("grand theft auto vi", "rage", "GTA VI , RAGE engine, DLSS 3.5 + Frame Gen (anticipated)",
    {
        "DLSS": dlss("Quality", "Balanced", "Performance", "UltraPerformance"),
        "FrameGeneration": framegen(),
        "ReflexMode": reflex(),
        "RayTracing": tier("true", "true", "false", "false"),
        "ShadowQuality": shadow(),
        "MotionBlur": "false",
    })

# ── Red Dead Redemption 2 ──────────────────────────────────────────────────
add("red dead redemption 2", "rage", "Red Dead Redemption 2 , RAGE engine, DLSS 3",
    {
        "DLSS": dlss("Quality", "Balanced", "Performance", "UltraPerformance"),
        "TAA_SHARPENING_ENABLED": "true",
        "VolumetricRaymarchResolution": tier("1.0", "0.75", "0.5", "0.25"),
        "SHADOW_QUALITY": shadow("Ultra", "High", "Medium", "Low"),
        "WATER_REFLECTION_QUALITY": tier("4", "3", "2", "1"),
        "ambientOcclusion": tier("HBAO+", "SSAO", "SSAO", "Off"),
        "motionblur": "false",
    })

# ── The Witcher ────────────────────────────────────────────────────────────
add("the witcher 4", "unreal", "The Witcher 4 , UE5, DLSS 4 Multi-Frame Gen (anticipated)",
    unreal_base("", dlss_s=True, framegen_s=True, lumen=True, extra={
        "DLSS": dlss("Quality", "Balanced", "Performance", "UltraPerformance"),
        "r.Nanite.Enabled": "1",
        "ReflexEnable": tier("2", "2", "1", "1"),
    }))

add("the witcher 3 wild hunt", "redengine3", "The Witcher 3 Next-Gen , DLSS 3.5 + RT",
    {
        "DLSS": dlss("Quality", "Balanced", "Performance", "UltraPerformance"),
        "RayTracing": tier("true", "true", "false", "false"),
        "RayTracedGI": tier("true", "false", "false", "false"),
        "FrameGeneration": framegen(),
        "ReflexMode": reflex(),
        "MotionBlur": "0",
        "ShadowQuality": shadow(),
    })

# ── The Elder Scrolls ──────────────────────────────────────────────────────
add("the elder scrolls vi", "creation engine 2", "TES VI , Creation Engine 2, DLSS 4 (anticipated)",
    {
        "DLSS": dlss("Quality", "Balanced", "Performance", "UltraPerformance"),
        "FrameGeneration": framegen(),
        "iShadowMapResolution": tier("4096", "2048", "1024", "512"),
        "uGridsToLoad": tier("10", "8", "6", "4"),
        "bMotionBlur": "0",
    })

add("the elder scrolls v skyrim special edition", "creation engine", "Skyrim SE , ENB/TAA optimized",
    {
        "iShadowMapResolution": tier("4096", "2048", "1024", "512"),
        "uGridsToLoad": tier("7", "7", "5", "5"),
        "bMotionBlur": "0",
        "bTreesReceiveShadows": tier("1", "1", "0", "0"),
        "iShadowCascadeCount": tier("3", "3", "2", "1"),
    })

# ── Assassin's Creed ───────────────────────────────────────────────────────
add("assassin's creed shadows", "anvil next", "AC: Shadows , AnvilNext 2.5, DLSS 4 + Frame Gen",
    {
        "DLSS": dlss("Quality", "Balanced", "Performance", "UltraPerformance"),
        "DLSS_RR": tier("true", "true", "false", "false"),
        "FrameGeneration": framegen(),
        "ReflexMode": reflex(),
        "RayTracing": tier("true", "true", "false", "false"),
        "ShadowQuality": shadow(),
        "MotionBlur": "false",
    })

add("assassin's creed mirage", "anvil next", "AC: Mirage , AnvilNext 2.0, DLSS 3",
    {
        "DLSS": dlss("Quality", "Balanced", "Performance", "UltraPerformance"),
        "FrameGeneration": framegen(),
        "ReflexMode": reflex("enabled", "enabled", "enabled", "disabled"),
        "ShadowQuality": shadow(),
        "MotionBlur": "false",
    })

add("assassin's creed valhalla", "anvil next", "AC: Valhalla , AnvilNext 2.0, DLSS 2",
    {
        "DLSS": dlss("Quality", "Balanced", "Performance", "UltraPerformance"),
        "ShadowQuality": shadow(),
        "MotionBlur": "false",
        "VolumetricClouds": tier("Ultra", "High", "Medium", "Low"),
    })

add("assassin's creed odyssey", "anvil next", "AC: Odyssey , AnvilNext 2.0, optimize for open world",
    {
        "ShadowQuality": shadow(),
        "MotionBlur": "false",
        "TextureQuality": texq(),
        "AmbientOcclusion": tier("HBAO+", "SSAO", "SSAO", "Off"),
    })

add("assassin's creed origins", "anvil next", "AC: Origins , AnvilNext 2.0",
    {
        "ShadowQuality": shadow(),
        "MotionBlur": "false",
        "TextureQuality": texq(),
    })

# ── Far Cry ────────────────────────────────────────────────────────────────
add("far cry 6", "dunia", "Far Cry 6 , Dunia engine, DLSS 2 + RT",
    {
        "DLSS": dlss("Quality", "Balanced", "Performance", "UltraPerformance"),
        "DXR_Shadows": tier("true", "true", "false", "false"),
        "DXR_Reflections": tier("true", "true", "false", "false"),
        "MotionBlur": "false",
        "ShadowQuality": shadow(),
    })

add("far cry 5", "dunia", "Far Cry 5 , Dunia engine, smooth 60+ FPS",
    {"ShadowQuality": shadow(), "MotionBlur": "false", "TextureQuality": texq()})

# ── Watch Dogs ─────────────────────────────────────────────────────────────
add("watch dogs legion", "disrupt", "Watch Dogs Legion , Disrupt engine, DLSS 2 + RT",
    {
        "DLSS": dlss("Quality", "Balanced", "Performance", "UltraPerformance"),
        "RayTracedShadows": tier("true", "true", "false", "false"),
        "RayTracedReflections": tier("true", "true", "false", "false"),
        "RayTracedAO": tier("true", "true", "false", "false"),
        "MotionBlur": "false",
    })

# ── Rainbow Six ────────────────────────────────────────────────────────────
add("tom clancy's rainbow six siege", "anvil next", "Rainbow Six Siege , AnvilNext, maximize FPS competitive",
    {
        "DLSS": dlss("Off", "Off", "Quality", "Balanced"),
        "TextureQuality": texq("2", "2", "1", "1"),
        "ShadowQuality": shadow("Medium", "Low", "Low", "Off"),
        "LODQuality": tier("3", "2", "1", "0"),
    })

add("tom clancy's rainbow six extraction", "anvil next", "Rainbow Six Extraction , AnvilNext, DLSS 2",
    {"DLSS": dlss("Quality", "Balanced", "Performance", "UltraPerformance"), "MotionBlur": "false"})

# ── The Division ───────────────────────────────────────────────────────────
add("tom clancy's the division 2", "snowdrop", "The Division 2 , Snowdrop engine, DLSS 2",
    {"DLSS": dlss("Quality", "Balanced", "Performance", "UltraPerformance"),
     "ShadowQuality": shadow(), "MotionBlur": "false"})

# ── Overwatch ─────────────────────────────────────────────────────────────
add("overwatch 2", "proprietary", "Overwatch 2 , maximize FPS for competitive play",
    {
        "AntiAliasingMethod": "FXAA",
        "ShadowQuality": tier("Ultra", "High", "Medium", "Off"),
        "TextureQuality": texq("2", "2", "1", "1"),
        "HighPrecisionRaymarching": tier("On", "On", "Off", "Off"),
        "RenderScale": tier("100", "85", "75", "65"),
        "ReflexEnable": tier("2", "2", "1", "1"),
    })

# ── Warframe ───────────────────────────────────────────────────────────────
add("warframe", "evolution", "Warframe , Evolution engine, smooth performance in missions",
    {
        "volumetricLighting": tier("On", "On", "Off", "Off"),
        "shadowQuality": shadow(),
        "textureQuality": texq(),
        "dynamicLighting": tier("High", "Medium", "Low", "Off"),
        "postProcessing": tier("On", "On", "On", "Off"),
    })

# ── Battlefield ───────────────────────────────────────────────────────────
add("battlefield 2042", "frostbite", "Battlefield 2042 , Frostbite 3.2, DLSS 2 + RT",
    frostbite_base(dlss_s=True, rt=True, extra={
        "GstRender.MotionBlurEnable": "false",
    }))

add("battlefield v", "frostbite", "Battlefield V , Frostbite 3.2, DXR Ray Tracing",
    frostbite_base(dlss_s=True, rt=True, extra={
        "GstRender.DXREnabled": tier("true", "true", "false", "false"),
    }))

add("battlefield 1", "frostbite", "Battlefield 1 , Frostbite 3, classic settings",
    frostbite_base(dlss_s=False))

add("battlefield 4", "frostbite", "Battlefield 4 , Frostbite 3, maximize FPS for CQL",
    frostbite_base(dlss_s=False))

# ── Star Wars ─────────────────────────────────────────────────────────────
add("star wars jedi survivor", "unreal", "Star Wars Jedi Survivor , UE4, DLSS 3 + Frame Gen",
    unreal_base("", dlss_s=True, rt=True, framegen_s=True, extra={
        "DLSS": dlss("Quality", "Balanced", "Performance", "UltraPerformance"),
        "ReflexEnable": tier("2", "2", "1", "1"),
    }))

add("star wars battlefront ii", "frostbite", "Star Wars Battlefront II , Frostbite 3",
    frostbite_base(dlss_s=False))

# ── Need for Speed ────────────────────────────────────────────────────────
add("need for speed unbound", "frostbite", "NFS Unbound , Frostbite, DLSS 3",
    frostbite_base(dlss_s=True, extra={
        "GstRender.FrameGeneration": framegen(),
    }))

# ── EA Sports ─────────────────────────────────────────────────────────────
add("ea sports fc 25", "frostbite", "EA Sports FC 25 , Frostbite 3.3, stable 60+ FPS",
    frostbite_base(dlss_s=True, extra={
        "GstRender.FrameGeneration": framegen("false", "false", "false", "false"),
    }))

add("ea sports fc 24", "frostbite", "EA Sports FC 24 , Frostbite 3.3, DLSS 3 + Frame Gen",
    frostbite_base(dlss_s=True, extra={
        "GstRender.FrameGeneration": framegen(),
    }))

# ── Dying Light ───────────────────────────────────────────────────────────
add("dying light 2 stay human", "chrome", "Dying Light 2 , C-Engine, DLSS 3 + Frame Gen + RT",
    {
        "DLSS": dlss("Quality", "Balanced", "Performance", "UltraPerformance"),
        "FrameGeneration": framegen(),
        "ReflexMode": reflex(),
        "RayTracing": tier("true", "true", "false", "false"),
        "ShadowQuality": shadow(),
        "MotionBlur": "false",
    })

add("dying light", "chrome", "Dying Light , C-Engine, maximize FPS open world parkour",
    {
        "ShadowQuality": shadow("High", "Medium", "Low", "Off"),
        "MotionBlur": "0",
        "TextureQuality": texq(),
    })

# ── Horror / Survival ─────────────────────────────────────────────────────
add("resident evil 4", "re engine", "Resident Evil 4 Remake , RE Engine, DLSS 3.5 + Frame Gen",
    re_engine_base("", dlss_s=True, rt=True, extra={
        "FrameGeneration": framegen(),
        "ReflexMode": reflex("enabled", "enabled", "enabled", "disabled"),
    }))

add("resident evil village", "re engine", "Resident Evil Village , RE Engine, DLSS 2 + RT",
    re_engine_base("", dlss_s=True, rt=True))

add("resident evil 2", "re engine", "Resident Evil 2 Remake , RE Engine, DLSS 2",
    re_engine_base("", dlss_s=True))

add("resident evil 3", "re engine", "Resident Evil 3 Remake , RE Engine, DLSS 2",
    re_engine_base("", dlss_s=True))

add("dead space", "custom", "Dead Space Remake , custom engine, DLSS 3 + RT",
    {
        "DLSS": dlss("Quality", "Balanced", "Performance", "UltraPerformance"),
        "RayTracing": tier("true", "true", "false", "false"),
        "ShadowQuality": shadow(),
        "MotionBlur": "false",
    })

add("sons of the forest", "unity", "Sons of the Forest , Unity, maximize performance in dense forest",
    unity_base("", dlss_s=True, extra={
        "DLSS": dlss("Quality", "Balanced", "Performance", "UltraPerformance"),
        "ShadowDistance": tier("200", "150", "100", "50"),
    }))

add("the forest", "unity", "The Forest , Unity, smooth survival gameplay",
    unity_base("", dlss_s=False))

# ── Open World RPG ────────────────────────────────────────────────────────
add("kingdom come deliverance ii", "cryengine", "Kingdom Come Deliverance II , CryEngine 5.11",
    cryengine_base(extra={
        "DLSS": dlss("Quality", "Balanced", "Performance", "UltraPerformance"),
        "FrameGeneration": framegen(),
        "r_RayTracing": tier("1", "1", "0", "0"),
    }))

add("kingdom come deliverance", "cryengine", "Kingdom Come: Deliverance , CryEngine 5, smooth 60 FPS",
    cryengine_base(extra={
        "r_ShadowBlur": "2",
        "r_SSDO": "1",
    }))

add("mount and blade ii bannerlord", "custom", "Bannerlord , custom engine, large battles optimized",
    {
        "TextureQuality": texq("4", "3", "2", "1"),
        "ShadowQuality": shadow(),
        "PerformancePreset": tier("Ultra", "High", "Medium", "Low"),
    })

add("the outer worlds", "unreal", "The Outer Worlds , UE4, stable performance",
    unreal_base("", dlss_s=False))

# ── Shooter ───────────────────────────────────────────────────────────────
add("doom eternal", "id tech 7", "DOOM Eternal , id Tech 7, maximize FPS uncapped",
    id_tech_base(extra={
        "com_skipIntroVideo": "1",
        "r_renderScale": tier("2", "1", "1", "0"),
        "r_frameratecap": "0",
        "r_antialiasing": "1",
    }))

add("doom 2016", "id tech 6", "DOOM 2016 , id Tech 6, maximize FPS",
    id_tech_base(extra={
        "r_frameratecap": "0",
        "image_lodbias": "-1",
    }))

add("doom the dark ages", "id tech 8", "DOOM: The Dark Ages , id Tech 8, DLSS 4 + Frame Gen",
    id_tech_base(extra={
        "DLSS": dlss("Quality", "Balanced", "Performance", "UltraPerformance"),
        "FrameGeneration": framegen(),
        "ReflexMode": reflex(),
        "r_rayTracing": tier("1", "1", "0", "0"),
    }))

add("quake ii rtx", "custom", "Quake II RTX , full path tracing enabled",
    {
        "pt_enable": "1",
        "pt_num_bounce_rays": tier("4", "2", "1", "1"),
        "pt_fake_roughness_threshold": "0.1",
        "pt_direct_polygon_lights": "1",
    })

add("wolfenstein ii the new colossus", "id tech 6", "Wolfenstein II: The New Colossus , id Tech 6",
    id_tech_base())

add("youngblood wolfenstein", "id tech 6", "Wolfenstein: Youngblood , DLSS 2 integrated",
    id_tech_base(extra={"DLSS": dlss("Quality", "Balanced", "Performance", "UltraPerformance")}))

# ── Multiplayer/Battle Royale ─────────────────────────────────────────────
add("the finals", "unreal", "THE FINALS , UE5, DLSS 3 + Frame Gen + Lumen destruction",
    unreal_base("", dlss_s=True, framegen_s=True, lumen=True, extra={
        "DLSS": dlss("Quality", "Balanced", "Performance", "UltraPerformance"),
        "ReflexEnable": tier("2", "2", "2", "1"),
    }))

add("hunt showdown 1896", "cryengine", "Hunt Showdown 1896 , CryEngine 5.11, DLSS 3 + Frame Gen",
    cryengine_base(extra={
        "DLSS": dlss("Quality", "Balanced", "Performance", "UltraPerformance"),
        "FrameGeneration": framegen(),
        "ReflexMode": reflex(),
    }))

add("escape from tarkov", "unity", "Escape from Tarkov , Unity, maximize FPS for survival",
    unity_base("", dlss_s=True, extra={
        "DLSS": dlss("Balanced", "Performance", "UltraPerformance", "UltraPerformance"),
        "LODBias": tier("-1", "-1", "0", "1"),
    }))

add("battlefield hardline", "frostbite", "Battlefield Hardline , Frostbite 3, legacy",
    frostbite_base(dlss_s=False))

# ── RPG / JRPG ────────────────────────────────────────────────────────────
add("persona 5 royal", "custom", "Persona 5 Royal , 60 FPS stable, upscaling off",
    {
        "AntiAliasing": "FXAA",
        "ShadowQuality": shadow("High", "High", "Medium", "Low"),
        "MotionBlur": "false",
    })

add("persona 3 reload", "unreal", "Persona 3 Reload , UE4, DLSS 3",
    unreal_base("", dlss_s=True, extra={
        "DLSS": dlss("Quality", "Balanced", "Performance", "Performance"),
    }))

add("tales of arise", "unreal", "Tales of Arise , UE4, stable 60 FPS",
    unreal_base("", dlss_s=True))

add("scarlet nexus", "unreal", "Scarlet Nexus , UE4",
    unreal_base("", dlss_s=False))

add("nier automata", "platinum", "NieR: Automata , smooth 60 FPS, FAR mod compatible",
    {
        "ShadowMapResolution": tier("4096", "2048", "1024", "512"),
        "AntiAliasing": "FXAA",
        "ShadowDistance": tier("200", "150", "100", "50"),
    })

add("nier replicant", "platinum", "NieR Replicant , smooth 60 FPS",
    {
        "ShadowQuality": shadow(),
        "MotionBlur": "false",
    })

add("dragon ball z kakarot", "unreal", "Dragon Ball Z: Kakarot , UE4",
    unreal_base("", dlss_s=False))

add("dragon ball sparking zero", "unreal", "Dragon Ball: Sparking! Zero , UE5, DLSS 3",
    unreal_base("", dlss_s=True, framegen_s=True))

add("tekken 8", "unreal", "Tekken 8 , UE5, DLSS 3.5 + Frame Gen",
    unreal_base("", dlss_s=True, framegen_s=True, lumen=True, extra={
        "DLSS": dlss("Quality", "Balanced", "Performance", "UltraPerformance"),
        "ReflexEnable": tier("2", "2", "1", "1"),
    }))

add("street fighter 6", "re engine", "Street Fighter 6 , RE Engine, DLSS 3 + Frame Gen",
    re_engine_base("", dlss_s=True, extra={
        "FrameGeneration": framegen(),
    }))

add("mortal kombat 1", "unreal", "Mortal Kombat 1 , UE4, smooth 60 FPS",
    unreal_base("", dlss_s=True))

# ── Strategy / Simulation ─────────────────────────────────────────────────
add("age of empires iv", "custom", "Age of Empires IV , Essence engine, smooth large battles",
    {
        "HighQualityTextures": tier("true", "true", "false", "false"),
        "UltraQualityTextures": tier("true", "false", "false", "false"),
        "MaxTextureQuality": tier("4", "3", "2", "1"),
        "ReflectionQuality": tier("3", "2", "1", "0"),
        "ShadowQuality": shadow(),
    })

add("age of empires ii definitive edition", "custom", "AoE2 DE , optimize for large multiplayer battles",
    {
        "Resolution": "Native",
        "Shadows": tier("High", "Medium", "Low", "Off"),
        "Water": tier("Full", "Full", "Low", "Off"),
    })

add("total war warhammer iii", "custom", "Total War Warhammer III , battle performance",
    {
        "aa_quality_level": tier("2", "1", "0", "0"),
        "shadows_quality": shadow(),
        "texture_filtering_level": "3",
        "tessellation_quality": tier("2", "1", "0", "0"),
        "ssao_level": tier("1", "1", "0", "0"),
    })

add("total war three kingdoms", "custom", "Total War: Three Kingdoms , battle performance",
    {
        "shadows_quality": shadow(),
        "texture_filtering_level": "3",
        "ssao_level": tier("1", "1", "0", "0"),
    })

add("civilization vi", "custom", "Civilization VI , smooth late-game performance",
    {
        "MaxAnisotropy": "16",
        "ShadowQuality": shadow("High", "Medium", "Low", "Off"),
        "EffectsQuality": tier("3", "2", "1", "0"),
    })

add("stellaris", "clausewitz", "Stellaris , smooth mid/late game performance",
    {
        "shadows": "yes",
        "shadowSize": tier("4096", "2048", "1024", "512"),
        "bloom": tier("yes", "yes", "no", "no"),
    })

add("hearts of iron iv", "clausewitz", "Hearts of Iron IV , smooth campaign performance",
    {
        "shadows": "yes",
        "shadowSize": tier("2048", "1024", "512", "256"),
        "bloom": tier("yes", "yes", "no", "no"),
    })

add("europa universalis iv", "clausewitz", "Europa Universalis IV , late-game optimization",
    {
        "shadows": "yes",
        "shadowSize": tier("2048", "1024", "512", "256"),
    })

add("cities skylines ii", "unity", "Cities: Skylines II , Unity HDRP, DLSS 3 + Frame Gen",
    unity_base("", dlss_s=True, rt=True, extra={
        "DLSS": dlss("Quality", "Balanced", "Performance", "UltraPerformance"),
        "FrameGeneration": framegen(),
        "LodBias": tier("-0.5", "0", "0.5", "1"),
    }))

add("cities skylines", "unity", "Cities: Skylines , Unity, smooth city builder",
    unity_base("", dlss_s=False))

add("planet coaster 2", "custom", "Planet Coaster 2 , smooth simulation",
    {
        "ShadowQuality": shadow(),
        "TextureQuality": texq(),
        "DrawDistance": tier("5000", "3000", "2000", "1000"),
    })

add("satisfactory", "unreal", "Satisfactory , UE5, smooth large factory builds",
    unreal_base("", dlss_s=True, lumen=False, extra={
        "DLSS": dlss("Quality", "Balanced", "Performance", "UltraPerformance"),
        "FrameGeneration": framegen(),
    }))

add("factorio", "custom", "Factorio , CPU-bound, maximize thread utilization",
    {
        "max_threads": "8",
        "compression_threads": "4",
        "worker_threads": "4",
    })

# ── Adventure / Action ────────────────────────────────────────────────────
add("god of war ragnarok", "custom", "God of War Ragnarök , custom engine, DLSS 3 + Frame Gen",
    {
        "DLSS": dlss("Quality", "Balanced", "Performance", "UltraPerformance"),
        "FrameGeneration": framegen(),
        "ReflexMode": reflex(),
        "RayTracedShadows": tier("true", "true", "false", "false"),
        "ShadowQuality": shadow(),
        "MotionBlur": "false",
    })

add("god of war", "custom", "God of War (2018) , custom engine, DLSS 2",
    {
        "DLSS": dlss("Quality", "Balanced", "Performance", "UltraPerformance"),
        "ShadowQuality": shadow(),
        "MotionBlur": "false",
    })

add("horizon forbidden west", "decima", "Horizon Forbidden West , Decima engine, DLSS 3",
    {
        "DLSS": dlss("Quality", "Balanced", "Performance", "UltraPerformance"),
        "ShadowQuality": shadow(),
        "MotionBlur": "false",
        "RayTracing": tier("true", "false", "false", "false"),
    })

add("horizon zero dawn remastered", "decima", "Horizon Zero Dawn Remastered , Decima, DLSS 3.5",
    {
        "DLSS": dlss("Quality", "Balanced", "Performance", "UltraPerformance"),
        "FrameGeneration": framegen(),
        "ReflexMode": reflex(),
        "ShadowQuality": shadow(),
    })

add("spider-man remastered", "insomniac", "Marvel's Spider-Man Remastered , Insomniac, DLSS 3 + Frame Gen",
    {
        "DLSS": dlss("Quality", "Balanced", "Performance", "UltraPerformance"),
        "FrameGeneration": framegen(),
        "ReflexMode": reflex(),
        "RayTracing": tier("true", "true", "false", "false"),
        "ShadowQuality": shadow(),
        "MotionBlur": "false",
    })

add("spider-man miles morales", "insomniac", "Spider-Man: Miles Morales , Insomniac, DLSS 3",
    {
        "DLSS": dlss("Quality", "Balanced", "Performance", "UltraPerformance"),
        "FrameGeneration": framegen(),
        "ReflexMode": reflex(),
        "RayTracing": tier("true", "true", "false", "false"),
        "ShadowQuality": shadow(),
    })

add("ghost of tsushima", "custom", "Ghost of Tsushima , custom engine, DLSS 3",
    {
        "DLSS": dlss("Quality", "Balanced", "Performance", "UltraPerformance"),
        "FrameGeneration": framegen(),
        "ReflexMode": reflex("enabled", "enabled", "enabled", "disabled"),
        "ShadowQuality": shadow(),
        "MotionBlur": "false",
    })

add("detroit become human", "quantic dream", "Detroit: Become Human , Quantic Dream engine",
    {
        "ShadowQuality": shadow(),
        "MotionBlur": "false",
        "TextureQuality": texq(),
    })

# ── Sports ────────────────────────────────────────────────────────────────
add("nba 2k25", "custom", "NBA 2K25 , stable 60 FPS performance",
    {
        "TextureQuality": texq("2", "2", "1", "1"),
        "ShadowQuality": shadow("High", "Medium", "Low", "Off"),
        "MotionBlur": "false",
    })

add("f1 24", "ego", "F1 24 , EGO engine, smooth race performance",
    {
        "DLSS": dlss("Quality", "Balanced", "Performance", "UltraPerformance"),
        "ShadowQuality": shadow(),
        "TextureQuality": texq(),
        "MotionBlur": "false",
    })

add("f1 23", "ego", "F1 23 , EGO engine, DLSS 3",
    {
        "DLSS": dlss("Quality", "Balanced", "Performance", "UltraPerformance"),
        "ShadowQuality": shadow(),
        "MotionBlur": "false",
    })

# ── Horror ────────────────────────────────────────────────────────────────
add("alone in the dark", "unreal", "Alone in the Dark , UE4, stable horror performance",
    unreal_base("", dlss_s=True))

add("the callisto protocol", "unreal", "The Callisto Protocol , UE4, DLSS 3",
    unreal_base("", dlss_s=True, rt=True, extra={
        "DLSS": dlss("Quality", "Balanced", "Performance", "UltraPerformance"),
        "ReflexEnable": tier("2", "2", "1", "1"),
    }))

add("a plague tale requiem", "custom", "A Plague Tale: Requiem , custom engine, DLSS 3 + RT",
    {
        "DLSS": dlss("Quality", "Balanced", "Performance", "UltraPerformance"),
        "RayTracing": tier("true", "true", "false", "false"),
        "ShadowQuality": shadow(),
        "MotionBlur": "false",
    })

add("a plague tale innocence", "custom", "A Plague Tale: Innocence",
    {
        "ShadowQuality": shadow(),
        "MotionBlur": "false",
        "TextureQuality": texq(),
    })

# ── Open World Survival ───────────────────────────────────────────────────
add("no man's sky", "custom", "No Man's Sky , custom engine, DLSS 3 + RT",
    {
        "DLSS": dlss("Quality", "Balanced", "Performance", "UltraPerformance"),
        "RayTracing": tier("true", "true", "false", "false"),
        "ShadowQuality": shadow(),
        "AntiAliasing": tier("DLSS", "DLSS", "DLSS", "FXAA"),
    })

add("subnautica below zero", "unity", "Subnautica: Below Zero , Unity, smooth exploration",
    unity_base("", dlss_s=False))

add("subnautica", "unity", "Subnautica , Unity, smooth underwater exploration",
    unity_base("", dlss_s=False))

add("the long dark", "unity", "The Long Dark , Unity, survival optimization",
    unity_base("", dlss_s=False, extra={"shadowDistance": tier("150", "100", "75", "50")}))

# ── indie / Misc ───────────────────────────────────────────────────────────
add("hades", "custom", "Hades , smooth 60 FPS",
    {"Vsync": "false", "MaxFPS": "240"})

add("hades ii", "custom", "Hades II , smooth 60 FPS",
    {"Vsync": "false", "MaxFPS": "240"})

add("hollow knight silksong", "unity", "Hollow Knight: Silksong , Unity, smooth",
    unity_base("", dlss_s=False))

add("hollow knight", "unity", "Hollow Knight , Unity, smooth",
    unity_base("", dlss_s=False))

add("deep rock galactic", "unreal", "Deep Rock Galactic , UE4, co-op performance",
    unreal_base("", dlss_s=False))

add("deep rock galactic survivor", "unity", "DRG: Survivor , Unity, smooth",
    unity_base("", dlss_s=False))

add("vampire survivors", "custom", "Vampire Survivors , smooth endgame",
    {"MaxParticles": tier("10000", "5000", "2000", "1000")})

add("terraria", "custom", "Terraria , frame rate uncapped",
    {"FrameSkip": "false", "LightingMode": tier("Color", "Color", "White", "Retro")})

add("stardew valley", "custom", "Stardew Valley , smooth 60 FPS",
    {"AlwaysShowToolHitLocation": "true"})

add("celeste", "custom", "Celeste , consistent 60 FPS for precision platforming",
    {"VSync": "false"})

add("dave the diver", "unity", "Dave the Diver , Unity, smooth",
    unity_base("", dlss_s=False))

add("baldur's gate 3 patch 7", "vulkan", "Baldur's Gate 3 (Patch 7) , same as base with photo mode",
    {
        "FSR": fsr("Quality", "Balanced", "Performance", "UltraPerformance"),
        "FrameGeneration": framegen(),
        "ShadowQuality": shadow(),
        "TextureQuality": texq(),
        "MotionBlur": "false",
    })

# ── Simulation ────────────────────────────────────────────────────────────
add("microsoft flight simulator 2024", "custom", "MSFS 2024 , maximize scenery with DLSS 3",
    {
        "DLSS": dlss("Quality", "Balanced", "Performance", "UltraPerformance"),
        "FrameGeneration": framegen(),
        "CloudQuality": tier("Ultra", "High", "Medium", "Low"),
        "TerrainLOD": tier("400", "250", "150", "100"),
        "ObjectLOD": tier("400", "200", "150", "100"),
        "ShadowCasters": tier("1500", "1000", "500", "250"),
        "ShadowQuality": shadow(),
        "MotionBlur": "false",
    })

add("microsoft flight simulator 2020", "custom", "MSFS 2020 , DLSS 2 + smooth open world flight",
    {
        "DLSS": dlss("Quality", "Balanced", "Performance", "UltraPerformance"),
        "CloudQuality": tier("Ultra", "High", "Medium", "Low"),
        "TerrainLOD": tier("400", "200", "150", "100"),
        "ShadowQuality": shadow(),
    })

add("farming simulator 25", "giants", "Farming Simulator 25 , GIANTS engine, DLSS 3",
    {
        "DLSS": dlss("Quality", "Balanced", "Performance", "UltraPerformance"),
        "ShadowQuality": shadow(),
        "TextureQuality": texq(),
        "TerrainQuality": tier("4", "3", "2", "1"),
    })

add("farming simulator 22", "giants", "Farming Simulator 22 , GIANTS engine",
    {
        "ShadowQuality": shadow(),
        "TextureQuality": texq(),
        "TerrainQuality": tier("4", "3", "2", "1"),
    })

# ── RPG ───────────────────────────────────────────────────────────────────
add("dark souls iii", "fromsoftware", "Dark Souls III , smooth 60 FPS",
    {
        "MotionBlur": "0",
        "Vignette": "0",
        "AntiAliasing": "2",
        "TextureQuality": texq("3", "2", "1", "0"),
        "ShadowQuality": shadow("High", "Medium", "Low", "Off"),
    })

add("dark souls remastered", "fromsoftware", "Dark Souls Remastered , smooth 60 FPS",
    {
        "MotionBlur": "0",
        "TextureQuality": texq("3", "2", "1", "0"),
    })

add("sekiro shadows die twice", "fromsoftware", "Sekiro , smooth 60 FPS, disable chromatic aberration",
    {
        "MotionBlur": "0",
        "ChromaticAberration": "0",
        "TextureQuality": texq("3", "2", "1", "0"),
    })

add("armored core vi fires of rubicon", "fromsoftware", "Armored Core VI , smooth 60 FPS mech combat",
    {
        "MotionBlur": "0",
        "TextureQuality": texq("3", "2", "1", "0"),
        "ShadowQuality": shadow("High", "Medium", "Low", "Off"),
    })

add("lies of p", "unreal", "Lies of P , UE4, DLSS 3",
    unreal_base("", dlss_s=True, extra={
        "DLSS": dlss("Quality", "Balanced", "Performance", "UltraPerformance"),
    }))

add("wo long fallen dynasty", "unreal", "Wo Long: Fallen Dynasty , UE4, DLSS 3",
    unreal_base("", dlss_s=True))

add("remnant ii", "unreal", "Remnant II , UE5, DLSS 3 + Frame Gen",
    unreal_base("", dlss_s=True, framegen_s=True, lumen=True, extra={
        "DLSS": dlss("Quality", "Balanced", "Performance", "UltraPerformance"),
    }))

add("remnant from the ashes", "unreal", "Remnant: From the Ashes , UE4",
    unreal_base("", dlss_s=False))

# ── Space ─────────────────────────────────────────────────────────────────
add("elite dangerous", "cobra", "Elite Dangerous , Cobra engine, smooth space flight",
    {
        "SuperSampling": "1",
        "AntiAliasing": tier("SMAA2TX", "SMAA2TX", "SMAA", "FXAA"),
        "ShadowQuality": shadow(),
        "TextureQuality": texq(),
        "AmbientOcclusion": tier("HBAO+", "SSAO", "SSAO", "Off"),
    })

add("star citizen", "cry engine", "Star Citizen , CryEngine, maximize performance in missions",
    {
        "r_texMaxAnisotropy": "16",
        "r_MotionBlur": "0",
        "sys_spec_shaders": tier("4", "3", "2", "1"),
        "sys_spec_shadows": tier("4", "3", "2", "1"),
        "r_VSync": "0",
    })

add("no man's sky vr", "custom", "No Man's Sky VR , VR-optimized settings",
    {
        "VRResolution": tier("1.5", "1.2", "1.0", "0.8"),
        "ShadowQuality": shadow("High", "Medium", "Low", "Off"),
    })

# ── Warhammer ─────────────────────────────────────────────────────────────
add("warhammer 40000 darktide", "unreal", "Warhammer 40,000: Darktide , UE4, DLSS 3 + Frame Gen",
    unreal_base("", dlss_s=True, rt=True, framegen_s=True, extra={
        "DLSS": dlss("Quality", "Balanced", "Performance", "UltraPerformance"),
        "ReflexEnable": tier("2", "2", "1", "1"),
    }))

add("warhammer 40000 space marine ii", "custom", "Warhammer 40K: Space Marine II , DLSS 3 + Frame Gen",
    {
        "DLSS": dlss("Quality", "Balanced", "Performance", "UltraPerformance"),
        "FrameGeneration": framegen(),
        "ReflexMode": reflex(),
        "ShadowQuality": shadow(),
        "MotionBlur": "false",
    })

add("warhammer 40000 boltgun", "unreal", "Warhammer 40,000: Boltgun , UE4, retro shooter",
    unreal_base("", dlss_s=False))

# ── More Action RPG ────────────────────────────────────────────────────────
add("atomic heart", "unreal", "Atomic Heart , UE4, DLSS 3 + Frame Gen + RT",
    unreal_base("", dlss_s=True, rt=True, framegen_s=True, extra={
        "DLSS": dlss("Quality", "Balanced", "Performance", "UltraPerformance"),
        "ReflexEnable": tier("2", "2", "1", "1"),
    }))

add("forspoken", "unreal", "Forspoken , UE5, DLSS 3 + Frame Gen",
    unreal_base("", dlss_s=True, framegen_s=True, lumen=True, extra={
        "DLSS": dlss("Quality", "Balanced", "Performance", "UltraPerformance"),
        "ReflexEnable": tier("2", "2", "1", "1"),
    }))

add("jedi knight fallen order", "unreal", "Jedi: Fallen Order , UE4, smooth adventure",
    unreal_base("", dlss_s=False))

add("control", "northlight", "Control , Northlight engine, DLSS 2 + RT",
    {
        "DLSS": dlss("Quality", "Balanced", "Performance", "UltraPerformance"),
        "RayTracingMode": tier("Ultra", "High", "Off", "Off"),
        "ShadowQuality": shadow(),
        "MotionBlur": "false",
    })

add("deathloop", "custom", "Deathloop , custom engine, DLSS 2",
    {
        "DLSS": dlss("Quality", "Balanced", "Performance", "UltraPerformance"),
        "ShadowQuality": shadow(),
        "MotionBlur": "false",
    })

add("ghostwire tokyo", "unreal", "GhostWire: Tokyo , UE4, DLSS 2 + RT",
    unreal_base("", dlss_s=True, rt=True))

add("prey", "cryengine", "Prey (2017) , CryEngine 5, smooth immersive sim",
    cryengine_base())

add("dishonored 2", "void", "Dishonored 2 , Void engine, smooth immersive sim",
    {
        "r_ssdo": tier("2", "1", "0", "0"),
        "r_MotionBlur": "0",
        "r_shadowDistance": tier("8000", "6000", "4000", "2000"),
    })

# ── Sandbox ────────────────────────────────────────────────────────────────
add("minecraft java edition", "custom", "Minecraft Java , maximize FPS with Sodium/Iris",
    {
        "renderDistance": tier("16", "12", "8", "6"),
        "simulationDistance": tier("12", "10", "8", "6"),
        "maxFps": "260",
        "graphicsMode": tier("Fabulous", "Fancy", "Fancy", "Fast"),
        "entityShadows": tier("true", "true", "false", "false"),
        "clouds": tier("Fancy", "Fast", "Off", "Off"),
    })

add("minecraft dungeons", "unreal", "Minecraft Dungeons , UE4, smooth action dungeon",
    unreal_base("", dlss_s=True))

add("valheim", "unity", "Valheim , Unity, smooth exploration + DLSS",
    unity_base("", dlss_s=True, extra={
        "DLSS": dlss("Quality", "Balanced", "Performance", "UltraPerformance"),
        "ShadowCascades": tier("4", "4", "2", "1"),
    }))

add("v rising", "unity", "V Rising , Unity, smooth vampire survival",
    unity_base("", dlss_s=True, extra={
        "DLSS": dlss("Quality", "Balanced", "Performance", "UltraPerformance"),
    }))

add("grounded", "unreal", "Grounded , UE4, smooth co-op survival",
    unreal_base("", dlss_s=True, extra={
        "DLSS": dlss("Quality", "Balanced", "Performance", "UltraPerformance"),
    }))

add("astroneer", "unreal", "Astroneer , UE4, smooth space exploration",
    unreal_base("", dlss_s=False))

add("7 days to die", "unity", "7 Days to Die , Unity, smooth zombie survival",
    unity_base("", dlss_s=False, extra={
        "shadowDistance": tier("200", "150", "100", "50"),
        "LODBias": tier("2", "1.5", "1", "0.5"),
    }))

# ── More Modern ───────────────────────────────────────────────────────────
add("lords of the fallen", "unreal", "Lords of the Fallen , UE5, DLSS 3.5 + Frame Gen",
    unreal_base("", dlss_s=True, framegen_s=True, lumen=True, extra={
        "DLSS": dlss("Quality", "Balanced", "Performance", "UltraPerformance"),
        "ReflexEnable": tier("2", "2", "1", "1"),
    }))

add("immortals of aveum", "unreal", "Immortals of Aveum , UE5, DLSS 3.5 + Frame Gen",
    unreal_base("", dlss_s=True, framegen_s=True, lumen=True, extra={
        "DLSS": dlss("Quality", "Balanced", "Performance", "UltraPerformance"),
        "r.Nanite.Enabled": "1",
    }))

add("exoprimal", "re engine", "Exoprimal , RE Engine, DLSS 3",
    re_engine_base("", dlss_s=True))

add("devil may cry 5", "re engine", "Devil May Cry 5 , RE Engine, DLSS 2",
    re_engine_base("", dlss_s=True))

add("pragmata", "re engine", "Pragmata , RE Engine (anticipated), DLSS 4",
    re_engine_base("", dlss_s=True, rt=True, extra={"FrameGeneration": framegen()}))

add("like a dragon infinite wealth", "dragon", "Like a Dragon: Infinite Wealth , Dragon engine, DLSS 3",
    {
        "DLSS": dlss("Quality", "Balanced", "Performance", "UltraPerformance"),
        "ShadowQuality": shadow(),
        "MotionBlur": "false",
    })

add("like a dragon ishin", "dragon", "Like a Dragon Ishin , Dragon engine, DLSS 2",
    {
        "DLSS": dlss("Quality", "Balanced", "Performance", "UltraPerformance"),
        "ShadowQuality": shadow(),
    })

add("persona 4 golden", "custom", "Persona 4 Golden , stable 60 FPS",
    {
        "AntiAliasing": "FXAA",
        "ShadowQuality": shadow("High", "High", "Medium", "Low"),
    })

add("trials of mana", "unreal", "Trials of Mana , UE4, smooth action RPG",
    unreal_base("", dlss_s=False))

add("ff14 dawntrail", "custom", "Final Fantasy XIV: Dawntrail , updated engine, DLSS 3",
    {
        "DLSS": dlss("Quality", "Balanced", "Performance", "UltraPerformance"),
        "FrameGeneration": framegen(),
        "ShadowLOD": tier("2", "1", "0", "0"),
        "TextureAnisotropicQuality": "16",
    })

add("the first descendant", "unreal", "The First Descendant , UE5, DLSS 3.5 + Frame Gen",
    unreal_base("", dlss_s=True, framegen_s=True, lumen=True, extra={
        "DLSS": dlss("Quality", "Balanced", "Performance", "UltraPerformance"),
        "ReflexEnable": tier("2", "2", "1", "1"),
    }))

add("nightingale", "unreal", "Nightingale , UE5, DLSS 3 + Frame Gen",
    unreal_base("", dlss_s=True, framegen_s=True, lumen=True, extra={
        "DLSS": dlss("Quality", "Balanced", "Performance", "UltraPerformance"),
    }))

add("once human", "unreal", "Once Human , UE4, DLSS 3",
    unreal_base("", dlss_s=True, extra={
        "DLSS": dlss("Quality", "Balanced", "Performance", "UltraPerformance"),
    }))

add("enshrouded", "custom", "Enshrouded , custom voxel engine, DLSS 3",
    {
        "DLSS": dlss("Quality", "Balanced", "Performance", "UltraPerformance"),
        "ShadowQuality": shadow(),
        "MotionBlur": "false",
    })

add("manor lords", "unreal", "Manor Lords , UE5, DLSS 3 strategy",
    unreal_base("", dlss_s=True, lumen=True, extra={
        "DLSS": dlss("Quality", "Balanced", "Performance", "UltraPerformance"),
        "r.Nanite.Enabled": "1",
    }))

add("skull and bones", "snowdrop", "Skull and Bones , Snowdrop engine, DLSS 3",
    {
        "DLSS": dlss("Quality", "Balanced", "Performance", "UltraPerformance"),
        "ShadowQuality": shadow(),
        "MotionBlur": "false",
    })

add("avatar pandora rising", "snowdrop", "Avatar: Pandora Rising , Snowdrop, DLSS 3",
    {"DLSS": dlss("Quality", "Balanced", "Performance", "UltraPerformance"),
     "ShadowQuality": shadow()})

add("anno 1800", "custom", "Anno 1800 , smooth city builder performance",
    {
        "ShadowQuality": shadow(),
        "TextureQuality": texq(),
        "AmbientOcclusion": tier("HBAO+", "SSAO", "SSAO", "Off"),
    })

add("age of wonders 4", "custom", "Age of Wonders 4 , smooth 4X gameplay",
    {
        "ShadowQuality": shadow(),
        "TextureQuality": texq(),
        "AntiAliasing": tier("TAA", "TAA", "FXAA", "FXAA"),
    })

add("returnal", "unreal", "Returnal , UE5, DLSS 3 + Frame Gen + RT",
    unreal_base("", dlss_s=True, framegen_s=True, rt=True, extra={
        "DLSS": dlss("Quality", "Balanced", "Performance", "UltraPerformance"),
        "ReflexEnable": tier("2", "2", "1", "1"),
    }))

add("nioh 2 complete edition", "custom", "Nioh 2 , smooth 120 FPS action",
    {
        "MaxFrameRate": "120",
        "ShadowQuality": shadow("High", "High", "Medium", "Low"),
        "EffectsQuality": tier("High", "Medium", "Low", "Low"),
    })

add("nioh the complete edition", "custom", "Nioh , smooth 120 FPS action",
    {
        "MaxFrameRate": "120",
        "ShadowQuality": shadow("High", "High", "Medium", "Low"),
    })

add("tales of berseria", "custom", "Tales of Berseria , stable 60 FPS",
    {
        "ShadowQuality": shadow("High", "Medium", "Low", "Off"),
        "MotionBlur": "false",
    })

add("judgment", "dragon", "Judgment , Dragon engine, smooth detective action",
    {
        "ShadowQuality": shadow(),
        "MotionBlur": "false",
    })

add("yakuza like a dragon", "dragon", "Yakuza: Like a Dragon , Dragon engine, smooth",
    {
        "ShadowQuality": shadow(),
        "MotionBlur": "false",
    })

add("yakuza kiwami 2", "dragon", "Yakuza Kiwami 2 , Dragon engine",
    {
        "ShadowQuality": shadow("High", "Medium", "Low", "Off"),
        "MotionBlur": "false",
    })

# ── More Competitive ──────────────────────────────────────────────────────
add("rainbow six extraction", "anvil next", "R6 Extraction , AnvilNext, DLSS 2",
    {"DLSS": dlss("Quality", "Balanced", "Performance", "UltraPerformance"),
     "MotionBlur": "false"})

add("the division", "snowdrop", "The Division , Snowdrop engine, smooth shooter",
    {"ShadowQuality": shadow(), "MotionBlur": "false"})

add("far cry new dawn", "dunia", "Far Cry New Dawn , Dunia engine, smooth open world",
    {"ShadowQuality": shadow(), "MotionBlur": "false"})

add("far cry primal", "dunia", "Far Cry Primal , Dunia engine",
    {"ShadowQuality": shadow(), "MotionBlur": "false"})

add("far cry 4", "dunia", "Far Cry 4 , Dunia engine",
    {"ShadowQuality": shadow(), "MotionBlur": "false"})

add("ghost recon breakpoint", "snowdrop", "Ghost Recon Breakpoint , Snowdrop, DLSS 2",
    {"DLSS": dlss("Quality", "Balanced", "Performance", "UltraPerformance"),
     "ShadowQuality": shadow(), "MotionBlur": "false"})

add("ghost recon wildlands", "snowdrop", "Ghost Recon Wildlands , Snowdrop, smooth open world",
    {"ShadowQuality": shadow(), "MotionBlur": "false"})

add("for honor", "anvil next", "For Honor , AnvilNext, maximize FPS",
    {"ShadowQuality": shadow("High", "Medium", "Low", "Off"), "MotionBlur": "false"})

# ── Visual Novel / Indie ───────────────────────────────────────────────────
add("disco elysium", "custom", "Disco Elysium , smooth 60 FPS",
    {"ShadowQuality": shadow("High", "Medium", "Low", "Off")})

add("cyberpunk 2077 phantom liberty", "custom", "Cyberpunk 2077: Phantom Liberty , DLSS 3.5 + Frame Gen",
    {
        "RayTracing": tier("PathTracing", "Ultra", "Off", "Off"),
        "DLSS": dlss("Quality", "Balanced", "Performance", "UltraPerformance"),
        "DLSSFrameGeneration": framegen(),
        "ReflexMode": reflex(),
        "MotionBlurScale": "0",
    })

add("alan wake", "custom", "Alan Wake Remastered , DLSS 2",
    {
        "DLSS": dlss("Quality", "Balanced", "Performance", "UltraPerformance"),
        "ShadowQuality": shadow(),
        "MotionBlur": "false",
    })

add("hellblade senua's sacrifice", "unreal", "Hellblade: Senua's Sacrifice , UE4",
    unreal_base("", dlss_s=False))

add("the medium", "unreal", "The Medium , UE4, DLSS 2 + RT",
    unreal_base("", dlss_s=True, rt=True))

add("observer system redux", "unreal", "Observer: System Redux , UE4, DLSS 2 + RT",
    unreal_base("", dlss_s=True, rt=True))

add("bright memory infinite", "unreal", "Bright Memory: Infinite , UE4, DLSS 3 + RT",
    unreal_base("", dlss_s=True, rt=True, extra={
        "DLSS": dlss("Quality", "Balanced", "Performance", "UltraPerformance"),
        "ReflexEnable": tier("2", "2", "1", "1"),
    }))

# ── Simulation / Vehicle ───────────────────────────────────────────────────
add("snowrunner", "custom", "SnowRunner , smooth off-road simulation",
    {
        "ShadowQuality": shadow(),
        "TextureQuality": texq(),
        "DrawDistance": tier("3000", "2000", "1500", "1000"),
    })

add("mudrunner", "custom", "MudRunner , smooth off-road simulation",
    {
        "ShadowQuality": shadow("High", "Medium", "Low", "Off"),
        "DrawDistance": tier("2000", "1500", "1000", "750"),
    })

add("beamng drive", "custom", "BeamNG.drive , physics simulation, maximize FPS",
    {
        "Graphics.dynamicReflections": tier("3", "2", "1", "0"),
        "Graphics.shadowsEnabled": "1",
        "Graphics.shadowMapSize": tier("4096", "2048", "1024", "512"),
        "Graphics.lightQuality": tier("2", "1", "0", "0"),
    })

add("assetto corsa competizione", "unreal", "Assetto Corsa Competizione , UE4, smooth racing",
    unreal_base("", dlss_s=True, extra={
        "DLSS": dlss("Quality", "Balanced", "Performance", "UltraPerformance"),
    }))

add("assetto corsa evo", "unreal", "Assetto Corsa EVO , UE5, DLSS 3.5 + Frame Gen",
    unreal_base("", dlss_s=True, framegen_s=True, lumen=True, extra={
        "DLSS": dlss("Quality", "Balanced", "Performance", "UltraPerformance"),
        "ReflexEnable": tier("2", "2", "1", "1"),
    }))

add("wreckfest", "bugbear", "Wreckfest , Bugbear engine, smooth destruction racing",
    {
        "ShadowQuality": shadow(),
        "TextureQuality": texq(),
        "AntiAliasing": tier("TAA", "TAA", "FXAA", "Off"),
    })

# ── MMO / Online ───────────────────────────────────────────────────────────
add("guild wars 2", "proprietary", "Guild Wars 2 , smooth MMO performance",
    {
        "ShadowQuality": shadow("High", "Medium", "Low", "Off"),
        "SamplerAnisotropy": "16",
        "LODBias": tier("-1", "0", "1", "2"),
    })

add("world of warcraft", "proprietary", "World of Warcraft , smooth raid performance",
    {
        "graphicsQuality": tier("7", "6", "5", "4"),
        "shadowQuality": tier("7", "5", "3", "1"),
        "liquidDetail": tier("2", "1", "1", "0"),
        "particleDensity": tier("100", "75", "50", "25"),
        "SSAOType": tier("2", "1", "0", "0"),
    })

add("new world aeternum", "custom", "New World: Aeternum , custom engine, DLSS 3",
    {
        "DLSS": dlss("Quality", "Balanced", "Performance", "UltraPerformance"),
        "FrameGeneration": framegen(),
        "ShadowQuality": shadow(),
        "MotionBlur": "false",
    })

add("lost ark", "unreal", "Lost Ark , UE3, smooth MMO",
    {
        "ShadowQuality": shadow("High", "Medium", "Low", "Off"),
        "TextureQuality": texq("3", "2", "1", "0"),
    })

add("black desert online", "custom", "Black Desert Online , smooth MMO action",
    {
        "ShadowQuality": shadow(),
        "TextureQuality": texq(),
        "DrawDistance": tier("300", "200", "150", "100"),
    })

# ── More titles ───────────────────────────────────────────────────────────
add("chivalry 2", "unreal", "Chivalry 2 , UE4, smooth medieval combat",
    unreal_base("", dlss_s=False))

add("mordhau", "unreal", "Mordhau , UE4, maximize FPS for medieval combat",
    unreal_base("", dlss_s=False, extra={
        "sg.ShadowQuality": tier("1", "1", "0", "0"),
        "sg.PostProcessQuality": tier("1", "0", "0", "0"),
    }))

add("back 4 blood", "unreal", "Back 4 Blood , UE4, smooth co-op shooter",
    unreal_base("", dlss_s=True))

add("outriders worldslayer", "unreal", "Outriders: Worldslayer , UE4, DLSS 2",
    unreal_base("", dlss_s=True))

add("the ascent", "unreal", "The Ascent , UE4, DLSS 2 + RT",
    unreal_base("", dlss_s=True, rt=True))

add("crossfire x", "unreal", "CrossfireX , UE4, maximize FPS",
    unreal_base("", dlss_s=True))

add("naraka bladepoint", "unreal", "Naraka: Bladepoint , UE4, DLSS 2",
    unreal_base("", dlss_s=True))

add("rogue company", "unreal", "Rogue Company , UE4, maximize FPS",
    unreal_base("", dlss_s=True))

add("magic the gathering arena", "unity", "MTG Arena , Unity, smooth card game",
    unity_base("", dlss_s=False))

add("league of legends", "custom", "League of Legends , maximize FPS for competitive",
    {
        "FrameCapType": "BenchmarkTest",
        "ShadowQuality": tier("2", "1", "0", "0"),
        "ParticleQuality": tier("High", "Medium", "Low", "Low"),
        "CharacterQuality": tier("Very High", "High", "Medium", "Low"),
        "EnvironmentQuality": tier("Very High", "High", "Medium", "Low"),
    })

add("wild rift", "custom", "League of Legends: Wild Rift , smooth mobile port",
    {
        "ShadowQuality": shadow("High", "Medium", "Low", "Off"),
        "FrameRate": "60",
    })

add("smite 2", "unreal", "SMITE 2 , UE5, DLSS 3 + Frame Gen",
    unreal_base("", dlss_s=True, framegen_s=True, lumen=True))

# ── Final stretch to approach 300 ─────────────────────────────────────────
add("payday 3", "unreal", "Payday 3 , UE5, smooth heist co-op",
    unreal_base("", dlss_s=True, lumen=False, extra={
        "DLSS": dlss("Quality", "Balanced", "Performance", "UltraPerformance"),
    }))

add("payday 2", "diesel", "Payday 2 , Diesel engine, maximize FPS",
    {
        "msaa_samples": tier("4", "2", "0", "0"),
        "shadow_resolution": tier("2048", "1024", "512", "256"),
        "lod_multiplier": tier("1.0", "0.8", "0.6", "0.4"),
    })

add("phasmophobia", "unity", "Phasmophobia , Unity, smooth ghost hunting",
    unity_base("", dlss_s=False, extra={"shadowDistance": tier("100", "75", "50", "25")}))

add("lethal company", "unity", "Lethal Company , Unity, smooth co-op",
    unity_base("", dlss_s=False))

add("content warning", "unity", "Content Warning , Unity, smooth",
    unity_base("", dlss_s=False))

add("baldur's gate 3 honor mode", "vulkan", "BG3 Honor Mode , same as base, maximize FPS large fights",
    {
        "FSR": fsr("Balanced", "Performance", "Performance", "UltraPerformance"),
        "FrameGeneration": framegen(),
        "ShadowQuality": shadow("High", "Medium", "Low", "Low"),
        "MotionBlur": "false",
    })

add("genshin impact", "unity", "Genshin Impact , Unity, smooth open world gacha",
    unity_base("", dlss_s=False, extra={
        "renderResolution": tier("1.5", "1.1", "0.8", "0.6"),
        "shadowQuality": tier("3", "2", "1", "0"),
        "ambientOcclusion": tier("2", "1", "0", "0"),
        "reflectionQuality": tier("3", "2", "1", "0"),
        "antiAliasing": tier("3", "2", "1", "0"),
    }))

add("honkai star rail", "unity", "Honkai: Star Rail , Unity, smooth turn-based",
    unity_base("", dlss_s=False, extra={
        "shadowQuality": tier("3", "2", "1", "0"),
        "ambientOcclusion": tier("2", "1", "0", "0"),
    }))

add("zenless zone zero", "unity", "Zenless Zone Zero , Unity, smooth action",
    unity_base("", dlss_s=True, extra={
        "DLSS": dlss("Quality", "Balanced", "Performance", "UltraPerformance"),
    }))

add("wuthering waves", "unreal", "Wuthering Waves , UE5, DLSS 3",
    unreal_base("", dlss_s=True, lumen=True, extra={
        "DLSS": dlss("Quality", "Balanced", "Performance", "UltraPerformance"),
    }))

add("tower of fantasy", "unreal", "Tower of Fantasy , UE4, smooth gacha",
    unreal_base("", dlss_s=True))

add("blue protocol", "unreal", "Blue Protocol , UE4",
    unreal_base("", dlss_s=False))

add("palia", "unreal", "Palia , UE5, smooth cozy MMO",
    unreal_base("", dlss_s=True, lumen=True, extra={
        "DLSS": dlss("Quality", "Balanced", "Performance", "UltraPerformance"),
    }))

add("overland", "unity", "Overland , Unity, smooth strategy",
    unity_base("", dlss_s=False))

add("terra nil", "unity", "Terra Nil , Unity, smooth",
    unity_base("", dlss_s=False))

add("dave the diver 2", "unity", "Dave the Diver 2 , Unity",
    unity_base("", dlss_s=False))

add("sea of thieves", "unreal", "Sea of Thieves , UE4, smooth open world naval",
    unreal_base("", dlss_s=True, extra={
        "DLSS": dlss("Quality", "Balanced", "Performance", "UltraPerformance"),
    }))

add("sea of stars", "unity", "Sea of Stars , Unity, smooth turn-based RPG",
    unity_base("", dlss_s=False))

add("coral island", "unreal", "Coral Island , UE4, smooth life sim",
    unreal_base("", dlss_s=False))

add("stationeers", "unity", "Stationeers , Unity, smooth space station sim",
    unity_base("", dlss_s=False))

add("among us", "unity", "Among Us , Unity, smooth social deduction",
    unity_base("", dlss_s=False))

add("pico park", "unity", "Pico Park , Unity, smooth co-op",
    unity_base("", dlss_s=False))

add("it takes two", "unreal", "It Takes Two , UE4, smooth co-op platformer",
    unreal_base("", dlss_s=False))

add("a way out", "unreal", "A Way Out , UE4, smooth split-screen co-op",
    unreal_base("", dlss_s=False))

add("wild hearts", "custom", "Wild Hearts , EA Otter engine, DLSS 3",
    {
        "DLSS": dlss("Quality", "Balanced", "Performance", "UltraPerformance"),
        "FrameGeneration": framegen(),
        "ShadowQuality": shadow(),
        "MotionBlur": "false",
    })

add("stranger of paradise final fantasy origin", "unreal", "Stranger of Paradise , UE4, smooth action",
    unreal_base("", dlss_s=True))

add("forza motorsport", "unreal", "Forza Motorsport , UE4 base, smooth sim racing",
    unreal_base("", dlss_s=True, extra={
        "DLSS": dlss("Quality", "Balanced", "Performance", "UltraPerformance"),
        "FrameGeneration": framegen(),
        "ReflexEnable": tier("2", "2", "1", "1"),
    }))

add("forza horizon 5", "custom", "Forza Horizon 5 , custom engine, DLSS 3 + RT",
    {
        "DLSS": dlss("Quality", "Balanced", "Performance", "UltraPerformance"),
        "RayTracing": tier("true", "true", "false", "false"),
        "ShadowQuality": shadow(),
        "MotionBlur": "false",
    })

add("riders republic", "custom", "Riders Republic , Snowdrop, smooth open world sports",
    {
        "ShadowQuality": shadow(),
        "MotionBlur": "false",
    })

add("steep", "custom", "Steep , Snowdrop, smooth winter sports",
    {
        "ShadowQuality": shadow(),
        "MotionBlur": "false",
    })

add("immortals fenyx rising", "custom", "Immortals Fenyx Rising , AnvilNext, smooth open world",
    {
        "ShadowQuality": shadow(),
        "MotionBlur": "false",
    })

add("riders republic", "snowdrop", "Riders Republic , Snowdrop, smooth sports",
    {"ShadowQuality": shadow()})

add("world of tanks", "bigworld", "World of Tanks , BigWorld engine, maximize FPS",
    {
        "ShadowQuality": shadow("High", "Medium", "Low", "Off"),
        "TextureQuality": texq(),
        "LightingQuality": tier("3", "2", "1", "0"),
        "ReflectionQuality": tier("3", "2", "1", "0"),
    })

add("war thunder", "dagor", "War Thunder , Dagor engine, smooth combat",
    {
        "shadowQuality": shadow(),
        "textureQuality": texq(),
        "DLSS": dlss("Quality", "Balanced", "Performance", "UltraPerformance"),
        "antialiasingMode": tier("DLSS", "DLSS", "DLSS", "TAA"),
    })

add("dragons dogma 2", "re engine", "Dragon's Dogma 2 (alt) , RE Engine 4.0 pawn optimization",
    re_engine_base("", dlss_s=True, rt=True, extra={
        "DLSS": dlss("Quality", "Balanced", "Performance", "UltraPerformance"),
        "FrameGeneration": framegen(),
        "PawnCrowdDensity": tier("High", "Medium", "Low", "Low"),
    }))

add("the talos principle ii", "unreal", "The Talos Principle II , UE5, DLSS 3 + Frame Gen",
    unreal_base("", dlss_s=True, framegen_s=True, lumen=True, extra={
        "DLSS": dlss("Quality", "Balanced", "Performance", "UltraPerformance"),
        "r.Nanite.Enabled": "1",
    }))

add("ghostrunner 2", "unreal", "Ghostrunner 2 , UE5, DLSS 3.5 + Frame Gen",
    unreal_base("", dlss_s=True, framegen_s=True, lumen=True, extra={
        "DLSS": dlss("Quality", "Balanced", "Performance", "UltraPerformance"),
        "ReflexEnable": tier("2", "2", "1", "1"),
    }))

add("ghostrunner", "unreal", "Ghostrunner , UE4, DLSS 2 + RT",
    unreal_base("", dlss_s=True, rt=True, extra={
        "DLSS": dlss("Quality", "Balanced", "Performance", "UltraPerformance"),
        "ReflexEnable": tier("2", "2", "1", "1"),
    }))

add("tiny tina's wonderlands", "unreal", "Tiny Tina's Wonderlands , UE4, DLSS 2",
    unreal_base("", dlss_s=True, extra={
        "DLSS": dlss("Quality", "Balanced", "Performance", "UltraPerformance"),
    }))

add("borderlands 3", "unreal", "Borderlands 3 , UE4, DLSS 2",
    unreal_base("", dlss_s=True, extra={
        "DLSS": dlss("Quality", "Balanced", "Performance", "UltraPerformance"),
    }))

add("the outer worlds spacers choice", "unreal", "Outer Worlds: Spacer's Choice , UE4, DLSS 2",
    unreal_base("", dlss_s=True))

add("greak memories of azur", "unity", "Greak: Memories of Azur , Unity, smooth",
    unity_base("", dlss_s=False))

add("death stranding", "decima", "Death Stranding Director's Cut , Decima, DLSS 2",
    {
        "DLSS": dlss("Quality", "Balanced", "Performance", "UltraPerformance"),
        "ShadowQuality": shadow(),
        "MotionBlur": "false",
        "TextureQuality": texq(),
    })

add("days gone", "unreal", "Days Gone , UE4, DLSS 2",
    unreal_base("", dlss_s=True, extra={
        "DLSS": dlss("Quality", "Balanced", "Performance", "UltraPerformance"),
    }))

add("the last of us part i", "custom", "The Last of Us Part I , custom engine, DLSS 3",
    {
        "DLSS": dlss("Quality", "Balanced", "Performance", "UltraPerformance"),
        "ShadowQuality": shadow(),
        "MotionBlur": "false",
        "TextureQuality": texq(),
    })

add("uncharted legacy of thieves", "custom", "Uncharted: Legacy of Thieves , custom engine, DLSS 3",
    {
        "DLSS": dlss("Quality", "Balanced", "Performance", "UltraPerformance"),
        "ShadowQuality": shadow(),
        "MotionBlur": "false",
    })

add("biomutant", "unreal", "Biomutant , UE4, smooth open world",
    unreal_base("", dlss_s=True))

add("the surge 2", "custom", "The Surge 2 , custom engine, smooth souls-like",
    {"ShadowQuality": shadow(), "MotionBlur": "false"})

add("elex ii", "genome", "ELEX II , Genome engine, smooth open world",
    {"ShadowQuality": shadow(), "TextureQuality": texq()})

add("praey for the gods", "unreal", "Praey for the Gods , UE4, smooth",
    unreal_base("", dlss_s=True))

add("sons of valhalla", "unreal", "Sons of Valhalla , UE5, smooth",
    unreal_base("", dlss_s=True, lumen=True))

add("marvels midnight suns", "unreal", "Marvel's Midnight Suns , UE4, DLSS 2",
    unreal_base("", dlss_s=True, extra={
        "DLSS": dlss("Quality", "Balanced", "Performance", "UltraPerformance"),
    }))

# ─── write profiles ──────────────────────────────────────────────────────────

def write_profile(name, data):
    filepath = OUTPUT_DIR / f"{name}.json"
    with open(filepath, "w") as f:
        json.dump(data, f, indent=2)
    return filepath

if __name__ == "__main__":
    count = 0
    for name, data in GAMES.items():
        p = write_profile(name, data)
        count += 1
        print(f"[{count:03d}] {p.name}")
    print(f"\nGenerated {count} profiles in {OUTPUT_DIR}")
