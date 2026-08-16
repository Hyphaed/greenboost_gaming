#!/usr/bin/env python3
"""Generate docs/FEATURES.md from the app's own feature data.

The catalogue of "things GreenBoost adds that a stock Linux/NVIDIA install
doesn't have" lives in two TypeScript modules, because the app renders it:

  src/src/gsHelp.ts     GS_ADDED_BY_GB  , which settings are GreenBoost's,
                                          and which group each belongs to
                        GS_BENEFIT      , the one-line benefit
                        GS_INFO         , the trade-off / gotcha note
  src/src/gbFeatures.ts GB_DETAIL       , what it does / why it's a Linux gap
                                          / how to verify it yourself
                        GB_AUTOMATIC    , the always-on entries with no switch

Writing that a second time by hand in markdown guarantees the two drift, and
the README's feature count goes stale the first time a setting is added. So
the doc is generated instead. Re-run after changing either module:

    python3 scripts/gen-features-doc.py

It also prints the feature count, which is the number quoted in README.md.
"""

from __future__ import annotations
import re
import sys
from pathlib import Path
from collections import OrderedDict

ROOT = Path(__file__).resolve().parent.parent
GS_HELP = ROOT / "src/src/gsHelp.ts"
GB_FEATURES = ROOT / "src/src/gbFeatures.ts"
OUT = ROOT / "docs/FEATURES.md"

# Order groups the way the app's All Games tab orders its sections, so the
# doc reads in the same sequence as the UI.
GROUP_ORDER = [
    "Performance & stutter",
    "Image quality & upscaling",
    "Latency & frame pacing",
    "Memory & VRAM overflow",
    "Overlays & visibility",
    "Display & session",
    "Gaming alongside local AI",
    "Advanced & diagnostics",
    "Always on , nothing to switch",
]


def strip_line_comments(src: str) -> str:
    """Drop whole-line `//` comments.

    Needed because the values are recovered by scanning for quoted strings,
    and a comment can legitimately contain one , gsHelp.ts has a comment
    reading `row("<label>"`, whose quoted fragment was being concatenated
    onto the value of the entry above it. Only lines that are entirely a
    comment are removed, so `https://` inside a string literal is untouched.
    """
    return "\n".join(
        "" if line.lstrip().startswith("//") else line
        for line in src.split("\n")
    )


def unescape(s: str) -> str:
    return s.replace('\\"', '"').replace("\\'", "'").replace("\\n", "\n")


def join_ts_string(expr: str) -> str:
    """Collapse a TS string-concatenation expression into one string.

    Handles the `"a "\n + "b "\n + "c"` style used throughout both modules.
    """
    parts = re.findall(r'"((?:[^"\\]|\\.)*)"', expr)
    return unescape("".join(parts)).strip()


def parse_record(src: str, name: str) -> "OrderedDict[str, str]":
    """Parse `export const NAME: Record<string, string> = { ... }`."""
    start = src.index(f"export const {name}")
    body = src[start:].split("= {", 1)[1]
    body = body.split("\n};", 1)[0]
    out: "OrderedDict[str, str]" = OrderedDict()
    # key, then everything up to the next top-level `",\n  "` boundary
    for m in re.finditer(
        r'^  "((?:[^"\\]|\\.)*)":\s*((?:.|\n)*?)(?=^  "|\Z)', body, re.M
    ):
        out[unescape(m.group(1))] = join_ts_string(m.group(2))
    return out


def parse_detail(src: str) -> "OrderedDict[str, dict]":
    """Parse GB_DETAIL: Record<string, GbDetail>."""
    start = src.index("export const GB_DETAIL")
    body = src[start:].split("= {", 1)[1]
    out: "OrderedDict[str, dict]" = OrderedDict()
    for m in re.finditer(
        r'^  "((?:[^"\\]|\\.)*)": \{((?:.|\n)*?)^  \},', body, re.M
    ):
        key, block = unescape(m.group(1)), m.group(2)
        entry = {}
        for field in ("what", "why", "verify"):
            fm = re.search(
                rf"^    {field}:\s*((?:.|\n)*?)(?=^    (?:what|why|verify):|\Z)",
                block, re.M,
            )
            entry[field] = join_ts_string(fm.group(1)) if fm else ""
        out[key] = entry
    return out


def parse_automatic(src: str) -> list[dict]:
    """Parse the GB_AUTOMATIC array of objects."""
    start = src.index("export const GB_AUTOMATIC")
    body = src[start:].split("= [", 1)[1].split("\n];", 1)[0]
    out = []
    for m in re.finditer(r"^  \{((?:.|\n)*?)^  \},", body, re.M):
        block = m.group(1)
        entry = {}
        fields = ("title", "tagline", "what", "why", "verify", "noSwitch")
        for field in fields:
            fm = re.search(
                rf"^    {field}:\s*((?:.|\n)*?)(?=^    (?:{'|'.join(fields)}):|\Z)",
                block, re.M,
            )
            entry[field] = join_ts_string(fm.group(1)) if fm else ""
        out.append(entry)
    return out


def md_escape(s: str) -> str:
    """The prose uses backticks for commands , keep those. Only guard pipes,
    which would otherwise break a markdown table."""
    return s.replace("|", "\\|")


def main() -> int:
    gs_src = strip_line_comments(GS_HELP.read_text())
    gb_src = strip_line_comments(GB_FEATURES.read_text())

    groups = parse_record(gs_src, "GS_ADDED_BY_GB")
    benefit = parse_record(gs_src, "GS_BENEFIT")
    info = parse_record(gs_src, "GS_INFO")
    detail = parse_detail(gb_src)
    automatic = {a["title"]: a for a in parse_automatic(gb_src)}

    by_group: "OrderedDict[str, list[str]]" = OrderedDict()
    for label, group in groups.items():
        by_group.setdefault(group, []).append(label)

    unknown = [g for g in by_group if g not in GROUP_ORDER]
    if unknown:
        print(f"error: group(s) not in GROUP_ORDER: {unknown}", file=sys.stderr)
        print("Add them to scripts/gen-features-doc.py.", file=sys.stderr)
        return 1

    total = len(groups)
    n_auto = len(automatic)
    n_switch = total - n_auto

    L: list[str] = []
    add = L.append

    add("# What GreenBoost Gaming Suite adds")
    add("")
    add(f"**{total} features** that a stock Linux + NVIDIA install does not give "
        "you. For most of them there is no NVIDIA-provided equivalent on Linux at "
        "all , NVIDIA App / GeForce Experience, the closest Windows counterpart "
        "for several, has never shipped for Linux.")
    add("")
    add(f"{n_switch} are settings you control; {n_auto} are always-on behavior, a "
        "manual action, or automatic bookkeeping, and say so rather than "
        "pretending to have an on/off state.")
    add("")
    add("Every entry below appears in the app itself, under **Games → All "
        "Games**, marked with a green `GreenBoost` badge. Click **GreenBoost "
        "extras only** to filter the list down to exactly this set, or open any "
        "row's ⓘ for the same text you're reading here.")
    add("")
    add("Back to [README.md](../README.md).")
    add("")
    add("## How to read an entry")
    add("")
    add("- **What it does** , the mechanism, plainly.")
    add("- **Why this is a Linux/NVIDIA gap** , why you don't already have it.")
    add("- **How to see it yourself** , a real check you can run on your own "
        "machine. Where an effect can't be measured without hardware most "
        "people don't own, or won't show up at all on an idle system, the entry "
        "says so instead of quoting an invented number.")
    add("")
    add("Nothing here has been benchmarked across a fleet. It is built and "
        "tested on one machine (RTX 5070, GNOME/Wayland), which is why every "
        "entry tells you how to check it rather than asking you to take a "
        "figure on faith.")
    add("")
    add("---")
    add("")

    # Contents
    add("## Contents")
    add("")
    for group in GROUP_ORDER:
        if group not in by_group:
            continue
        anchor = group.lower().replace(" ", "-").replace(",", "").replace("&", "")
        anchor = re.sub(r"-+", "-", anchor).strip("-")
        add(f"- [{group}](#{anchor}) , {len(by_group[group])}")
    add("")
    add("---")
    add("")

    for group in GROUP_ORDER:
        labels = by_group.get(group)
        if not labels:
            continue
        add(f"## {group}")
        add("")
        for label in labels:
            auto = automatic.get(label)
            add(f"### {label}")
            add("")
            tag = auto["tagline"] if auto else benefit.get(label, "")
            if tag:
                add(f"**{md_escape(tag)}**")
                add("")
            if auto:
                add(f"*No switch , {md_escape(auto['noSwitch'])}*")
                add("")

            d = detail.get(label)
            what = (auto or {}).get("what") or (d or {}).get("what") or ""
            if what:
                add("**What it does**")
                add("")
                add(md_escape(what))
                add("")
            if d and d.get("why"):
                add("**Why this is a Linux/NVIDIA gap**")
                add("")
                add(md_escape(d["why"]))
                add("")
            if d and d.get("verify"):
                add("**How to see it yourself**")
                add("")
                add(md_escape(d["verify"]))
                add("")
            note = info.get(label)
            if note:
                add("**Worth knowing**")
                add("")
                add(md_escape(note))
                add("")
        add("---")
        add("")

    add("<sub>Generated from `src/src/gsHelp.ts` and `src/src/gbFeatures.ts` by "
        "`scripts/gen-features-doc.py` , edit those, not this file, then re-run "
        "the script.</sub>")
    add("")

    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text("\n".join(L))
    print(f"wrote {OUT.relative_to(ROOT)}")
    print(f"features: {total} ({n_switch} with a control, {n_auto} always-on)")
    for group in GROUP_ORDER:
        if group in by_group:
            print(f"  {len(by_group[group]):>2}  {group}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
