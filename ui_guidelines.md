# UI Guidelines , GreenBoost Gaming Suite
## Reference: ~/Dev/steam_nvidia_optimizer/screens/

These guidelines are derived by pixel-analysis of the 2026 NVIDIA App screenshots.
All dimensions in px. Font: Inter (Google Fonts).

---

## 1. Color System

```css
--color-sidebar:   #1a1c1e;   /* sidebar + games-list bg */
--color-bg:        #1e2124;   /* main content area */
--color-card:      #252830;   /* cards, selected rows, hover */
--color-hover:     #2a2d35;   /* hover state for rows */
--color-border:    #2d3038;   /* primary borders */
--color-divider:   #333840;   /* table row dividers */
--color-gb:        #76b900;   /* green accent (NVIDIA green equivalent) */
--color-gb-hover:  #85d000;   /* green on hover */
--color-gb-dim:    rgba(118,185,0,0.12);  /* subtle green bg tint */
--color-primary:   #dde1e8;   /* primary text */
--color-secondary: #8a9ab0;   /* secondary text / values */
--color-muted:     #596070;   /* muted text / labels */
--color-amber:     #e8a000;   /* "not optimized" warning */
```

---

## 2. Layout

```
┌────────┬──────────────────────────────────────────────┐
│Sidebar │ Page Header (62px)                           │
│ 72px   ├──────────────────────────────────────────────┤
│        │ [Sub-nav tabs, optional]                     │
│        ├──────────────┬───────────────────────────────┤
│        │ Left Panel   │ Right Panel / Content         │
│        │ (290px,      │ (flex:1, scrollable)          │
│        │ fixed width) │                               │
└────────┴──────────────┴───────────────────────────────┘
```

- `position: fixed; inset: 0` on root , no vh/vw units
- All scrolling via `overflow-y: auto` on specific containers
- `html, body, #root` → `height: 100%; overflow: hidden; margin: 0`

---

## 3. Sidebar (72px)

- Background: `#1a1c1e`
- Border-right: `1px solid #2d3038`
- Logo: 28×28px green box with "GB" text (font-size 12px, font-weight 900)
- Green dot beneath logo: 6×6px, `#76b900`, border-radius 50%

### Nav items
- Width: 100%, padding: 10px 4px
- Layout: flex-column, items centered, gap 4px
- Icon: 20×20px, SVG outlined style
- Label: 10px, font-weight 500
- Default state: color `#596070`
- Hover: color `#8a9ab0`, bg `rgba(255,255,255,0.04)`
- Active: color `#76b900`, bg `rgba(118,185,0,0.08)`, left-border 3px `#76b900`

---

## 4. Page Header (62px)

- Background: `#1e2124`
- Border-bottom: `1px solid #2d3038`
- Title: 20px, font-weight 700, color `#dde1e8`
- Padding: 0 28px
- Right side: small icon + text, color `#596070`, font-size 11px

---

## 5. Sub-navigation Tabs

- Height: ~44px (12px padding top/bottom)
- Background: `#1e2124`, border-bottom `1px solid #2d3038`
- Padding: 0 28px, gap 28px between tabs
- Tab: font-size 13px, font-weight 500
- Default: color `#596070`
- Hover: color `#8a9ab0`
- Active: color `#dde1e8`, border-bottom `2px solid #76b900`

---

## 6. Games View , Left Panel (290px)

Background: `#1a1c1e`, border-right: `1px solid #2d3038`

### Header
- Padding: 14px 16px
- "N/N Programs": font-size 13px, font-weight 600, color `#dde1e8`
- Right: icon buttons (14px icons, color `#596070`, hover `#8a9ab0`)

### Game rows
- min-height: 56px, padding: 10px 16px
- Layout: flex row, gap 10px, align-items center
- Left border: 3px solid transparent (active: `#76b900`)
- Hover bg: `#252830`
- Selected bg: `#252830`

**Columns (left to right):**
1. Status dot (16×16 MinusCircle icon, `#596070`)
2. Thumbnail: 38×38px, border-radius 4px (fallback: `#2a2d35` bg + Gamepad icon)
3. Game name: 13px, color `#8a9ab0` (selected: `#dde1e8`), overflow ellipsis

---

## 7. Games View , Right Panel

Background: `#1e2124`

### Game header
- Padding: 18px 28px, border-bottom `1px solid #2d3038`
- Thumbnail: 40×40px
- Name: 16px, font-weight 700, `#dde1e8`
- Sub-line: 11px, `#596070` (RTX library count)
- **OPTIMIZE button**: padding 7px 18px, bg `#76b900`, text #000, font-size 11px,
  font-weight 700, uppercase, letter-spacing 0.06em, border-radius 3px

### Status bar
- Padding: 10px 28px, border-bottom `1px solid #2d3038`
- Warning: `⊖ Program isn't optimized` , color `#e8a000`, icon + text
- OK: `✓ Program is optimized` , color `#76b900`

### Settings table

**Column headers row** (sticky):
- Background: `#1a1c1e`, padding 10px 28px
- Grid: `1fr 200px 240px`
- Text: 11px, font-weight 600, uppercase, letter-spacing 0.06em, color `#596070`
- Columns: "In-Game Settings" | "Current Value" | "Preview Value (Recommended)"

**Setting rows:**
- Grid: `1fr 200px 240px`, padding 13px 28px
- Border-bottom: `1px solid #2d3038`
- Hover bg: `#252830`
- Setting name: 13px, `#dde1e8`
- Current value: 13px, `#8a9ab0` (italic gray when "Not configured")
- Recommended: 13px, `#8a9ab0` (same as current when equal)
  → When **differs**: `#76b900`, font-weight 600

---

## 8. Section Cards (Home / Settings)

```css
background: #252830;
border: 1px solid #2d3038;
border-radius: 6px;
padding: 20px 24px;
```

- Info rows: grid `auto 1fr`, gap 24px, padding 14px 0, border-bottom `1px solid #2d3038`
- Label: 13px, font-weight 500, `#dde1e8`
- Value: 13px, `#8a9ab0` (ok state: `#76b900`, warn: `#e8a000`)

---

## 9. Controls

### Toggle switch
- Track: 44×24px, border-radius 12px, `#333840` (on: `#76b900`)
- Thumb: 18×18px, bg white, top 3px, left 3px (on: translate 20px)

### Buttons
- Primary (Optimize): bg `#76b900`, text black, 11px bold uppercase
- Ghost/icon: transparent bg, `#596070` color, hover bg `#2a2d35`

---

## 10. Typography

| Element             | Size | Weight | Color      |
|---------------------|------|--------|------------|
| Page title          | 20px | 700    | `#dde1e8`  |
| Section title       | 15px | 600    | `#dde1e8`  |
| Card title          | 14px | 600    | `#dde1e8`  |
| Body / row text     | 13px | 400    | `#dde1e8`  |
| Secondary / values  | 13px | 400    | `#8a9ab0`  |
| Labels / muted      | 11px | 500    | `#596070`  |
| Column headers      | 11px | 600    | `#596070`  |
| Nav labels          | 10px | 500    | `#596070`  |

---

## 11. Licensing

Copyright header in all source files:
```
// Copyright 2026 Ferran Duarri , GPL v2
// GreenBoost is an independent open-source project and is not affiliated with,
// endorsed by, or sponsored by NVIDIA Corporation.
// NVIDIA, CUDA, GeForce, and RTX are trademarks of NVIDIA Corporation.
```

Disclaimer must appear in Settings → About tab.

---

## 12. What to Avoid

- `h-screen` / `100vh` in Tauri WebView (use `position: fixed; inset: 0`)
- Tailwind v3 directives (`@tailwind base`) with Tailwind v4 , use `@import "tailwindcss"` + `@tailwindcss/vite`
- Gradients, shadows, rounded "mobile-style" cards
- Multiple accent colors , green is the only accent
- Proton / SteamLinuxRuntime entries in the games list
- Any references to "NVIDIA" as the developer/sponsor of this app
