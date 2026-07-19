# Aware — Finalized Frontend Design Spec

Direction: **Outdoorsy & calm** — muted greens, warm earth neutrals, soft sky-blue for water/map accents; map-forward, understated, trail-guide feel. No neon, no corporate slate/blue.

Synthesized from four consultant specs (brand, layout, components, map). Where they conflicted, this doc is the source of truth. Stack: React 19 + TS + Vite, Tailwind v4 (configless — `@theme` tokens), react-leaflet + Leaflet (keyless tiles).

---

## 1. Design tokens (`src/index.css`)

Replaces the bare `@import "tailwindcss";`. **Token-naming decision:** brand green is `--color-primary`; text colors are `--color-ink` / `--color-ink-muted` (NOT `text-primary`, which would collide with the green `bg-primary`).

```css
@import "tailwindcss";

@theme {
  --font-display: "Fraunces", serif;
  --font-body: "Inter", sans-serif;

  /* surfaces */
  --color-background: #F7F5EF;   /* warm parchment page bg */
  --color-surface: #FFFFFF;      /* cards, panels */
  --color-surface-muted: #EFEBE1;/* recessed / collapsed form bar */

  /* brand */
  --color-primary: #3F6B4F;      /* deep moss green */
  --color-primary-hover: #345A42;
  --color-on-primary: #F7F5EF;   /* text/icon on green */
  --color-secondary: #7A6A53;    /* bark/earth brown */
  --color-accent-sky: #5B8AA6;   /* water / links */

  /* semantic states */
  --color-success: #3F6B4F;      /* "Matched" (reuses moss — a match is the good outcome) */
  --color-warning: #8A6A2E;      /* "Closest available" / tradeoff (muted amber-brown) */
  --color-danger: #9C4A3C;       /* errors (muted brick) */

  /* text + structure */
  --color-ink: #2B2A25;          /* headings, body */
  --color-ink-muted: #6B6759;    /* captions, secondary */
  --color-border: #DDD6C6;
}

body { font-family: var(--font-body); }

/* Leaflet: strip default divIcon chrome + clickable cursor */
.aware-marker { background: transparent; border: none; }
.leaflet-interactive { cursor: pointer; }
```

Utilities produced: `bg-primary` `text-primary` `hover:bg-primary-hover` `text-on-primary` `bg-surface` `bg-background` `text-ink` `text-ink-muted` `border-border` `text-success` `text-warning` `text-danger` `bg-accent-sky`, and opacity variants (`bg-success/15`, `bg-warning/10`, `ring-primary/40`, …). All text-on-bg pairings clear WCAG AA (primary/success clear AAA).

## 2. Typography

**Fraunces** (soft warm serif, opsz variable) for the wordmark + section headings; **Inter** for all body/UI. Add to `index.html <head>`:

```html
<link rel="preconnect" href="https://fonts.googleapis.com" />
<link rel="preconnect" href="https://fonts.gstatic.com" crossorigin />
<link href="https://fonts.googleapis.com/css2?family=Fraunces:opsz,wght@9..144,500;9..144,600&family=Inter:wght@400;500;600&display=swap" rel="stylesheet" />
```

Headings: `font-display` weight 600 (h1/h2), 500 (h3). Body/labels/buttons: `font-body`, 400 body / 500 labels+buttons / 600 emphasis.

## 3. Wordmark

Custom inline SVG: a looping trail line terminating in a location pin ("a route that finds a stop"), moss-green route + sky pin. Header lockup:

```tsx
function AwareMark({ className = "h-7 w-7" }: { className?: string }) {
  return (
    <svg viewBox="0 0 24 24" fill="none" className={className} aria-hidden="true">
      <path d="M4 15c0-4.5 3.5-8 8-8s7 3 7 6.5-3 5-6 3.5-2-5 1-6"
        stroke="var(--color-primary)" strokeWidth="1.75" strokeLinecap="round" />
      <path d="M4 10.5c0-2.2 1.8-4 4-4s4 1.8 4 4c0 2.8-4 6-4 6s-4-3.2-4-6Z"
        fill="var(--color-accent-sky)" />
      <circle cx="8" cy="10.5" r="1.4" fill="var(--color-surface)" />
    </svg>
  );
}
// <div className="flex items-center gap-2"><AwareMark /><h1 className="font-display text-2xl font-semibold text-ink">Aware</h1></div>
```

## 4. Voice & microcopy

Knowledgeable running-buddy tone: direct, warm, practical. No exclamation points, no "Oops!". On failure, explain the why + the next action. Rewrites:
- Tagline → **"Routes that know where you can stop."**
- No-route → **"No loop in that range passes a restroom near this start. Try a wider mileage range, or drop the pin somewhere else."**
- Loading → **"Scouting routes…"**

---

## 5. Layout & IA

**App shell** (`flex h-screen flex-col`): header (flex-none) + body row (`flex flex-1 overflow-hidden`). **Remove the `mx-auto max-w-7xl` on the body row** so the map fills the viewport (header may keep a capped width).

**Desktop sidebar** = 3 vertical regions, only the middle scrolls:
1. **Context strip** (flex-none, only after a start is picked): `Start: 40.781, -73.969  [Change ×]`
2. **Scroll body** (flex-1 overflow-y-auto): form → (collapsed form summary + Edit after submit) → transient state → results list
3. **Pinned GPX footer** (flex-none, only when a route is selected): full-width Download GPX, with a `Route 2 · Smoothest` sub-line so it stays bound to the scrolled-away card.

**Form ↔ results:** single scroll stream, no tabs. After submit the full form collapses to a one-line summary bar (`5.0 mi · 1–4 mi · Mixed  [Edit]`) on `bg-surface-muted`; Edit re-expands in place.

**Mobile (<768px):** full-bleed map + a draggable bottom sheet with 3 snap states (peek ~12% / half ~50% / full ~92% of `100dvh`), reusing the same inner components. Plain React state + a CSS `height`/`transform` transition (no physics lib); drag handle also tap-cycles for a11y. Results arriving → auto-snap to **half** (map + list both visible). Header is compact, `bg-surface/95 backdrop-blur`, absolute top.

**Onboarding (no start yet):** a `pointer-events-none` overlay centered on the map with a `pointer-events-auto` card: "Click anywhere on the map to set your start / We'll build routes that loop back here, passing a restroom or fountain in the range you choose." Sidebar keeps the disabled-submit + hint as a second cue.

**Transient states** (loading / 422 / 429 / error) render in one shared slot below the form, each an icon + heading + body(+action) card, not a bare `<p>`:
- Loading: spinner glyph + "Scouting routes…" + reassurance; form summary visually locked.
- 422: heading + copy + inline actions **Widen restroom range** (focus max-mile) / **Choose a new start**.
- 429: "Demo limit reached" + copy, no action, keep inputs.
- Error: "Something went wrong" heading + `error` as detail + **Try again** (resubmits last values).
`role="alert"` (or `status` for loading) on each.

---

## 6. Component classes (paste-ready)

Assumes tokens from §1. Icons from `lucide-react`, all `aria-hidden`.

**Input/select (`controlClass`):**
```
w-full rounded-lg border border-border bg-surface px-3 py-2 text-sm text-ink placeholder:text-ink-muted transition-colors focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-primary/40 focus-visible:border-primary
```
**Label:** `block text-sm font-medium text-ink mb-1.5`  •  **Helper/hint:** `text-sm text-ink-muted`

**Restroom range** = one group label spanning both columns + a 2-col inner grid of Min mile / Max mile (`text-xs text-ink-muted` sub-labels).

**Primary button (submit):**
```
w-full rounded-lg bg-primary px-4 py-2.5 text-sm font-semibold text-on-primary transition-colors hover:bg-primary-hover focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-primary/40 focus-visible:ring-offset-2 focus-visible:ring-offset-background active:bg-primary/95 disabled:cursor-not-allowed disabled:bg-ink-muted/30 disabled:text-ink-muted
```
**Secondary button (Download GPX):**
```
w-full rounded-lg border border-primary/40 bg-surface px-4 py-2.5 text-sm font-semibold text-primary transition-colors hover:bg-primary/5 hover:border-primary focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-primary/40 focus-visible:ring-offset-2 focus-visible:ring-offset-background active:bg-primary/10
```

**Result card (button):**
```
block w-full cursor-pointer rounded-xl border p-4 text-left transition-all focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-primary/40 ${
  isSelected ? "border-primary bg-primary/5 ring-2 ring-primary shadow-sm"
             : "border-border bg-surface hover:border-primary/50 hover:shadow-sm"}
```
**Badges** (pill, icon + text):
- Matched: `inline-flex items-center gap-1 rounded-full bg-success/15 px-2.5 py-0.5 text-xs font-bold text-success` + `<CheckCircle2 className="h-3.5 w-3.5"/>`
- Closest available: same with `bg-warning/15 text-warning` + `<AlertCircle/>`
- Archetype: outlined — `inline-flex items-center gap-1 rounded-full border border-primary/30 bg-primary/10 px-2.5 py-0.5 text-xs font-bold text-primary` + `<Star/>`
- Facts list: `my-2 list-disc space-y-0.5 pl-5 text-sm text-ink-muted` • Tradeoff: `text-sm italic text-ink-muted`

**State banner** (generic): `flex items-start gap-2 rounded-lg border p-3 text-sm` with tone = `border-warning/30 bg-warning/10 text-warning` (info/rate-limit), `border-danger/30 bg-danger/10 text-danger` (error); icon `h-4 w-4 mt-0.5 shrink-0`. Icons: `Info` (422), `Clock` (429), `CircleAlert` (error), `Loader2 animate-spin` (loading). Extract to a small `StatusMessage` component.

---

## 7. Map (`Map.tsx`)

**Tiles — CARTO Positron** (keyless, muted, lets routes pop). Keep BOTH attributions:
```tsx
<TileLayer
  url="https://{s}.basemaps.cartocdn.com/light_all/{z}/{x}/{y}{r}.png"
  subdomains="abcd" maxZoom={20}
  attribution='&copy; <a href="https://www.openstreetmap.org/copyright">OpenStreetMap</a> contributors &copy; <a href="https://carto.com/attributions">CARTO</a>'
/>
```
(Free CDN, not SLA-backed — fine for portfolio; Stadia Maps free key is the upgrade path.)

**Route polylines — casing + top line**, two `<Polyline>`s per route (`flatMap`, not a wrapper div), selected route painted **last** so it's never occluded:
- Casing (`interactive={false}`): unselected `#f4f1ea` w6 op.55 / selected `#dfead9` w9 op.9
- Top line: unselected `#6b7a70` w3 op.75 / selected `#3f6b4f` w5 op1; `dashArray:"1,10"` + `lineCap:"round"` when `matched===false` (dotted = "closest available", no warning color needed)
- `eventHandlers.click → onSelectRoute(index)`; hover bumps unselected op→1/weight→4.

**Markers** — three self-contained `divIcon` SVGs (full SVG in the consultant file, and inlined at build time):
- Start: moss-green pin (`#3f6b4f`) w/ parchment core, `iconAnchor:[15,37]`
- Restroom: clay roundel (`#b8894f`) + WC glyph, `iconAnchor:[13,13]`
- Fountain: sky roundel (`#4a7fa5`) + droplet glyph
Kind resolution: **stopgap** `/fountain/i.test(facility_name)` now; **preferred** thread `kind: "restroom"|"fountain"` through `/routes/with-restroom` (backend already has `AmenityKind`). Selected amenity marker: `opacity 1 / zIndexOffset 1000`; others `opacity 0.55`.

**Legend** — static HTML overlay bottom-left (`bg-[#f4f1ea]/90 backdrop-blur`), rows: selected route / other routes / closest-available (dotted) / restroom + fountain dots.

**Optional stretch (skippable):** warm-tint the unselected casing by `elevation_gain_m` bucket as a cheap climb cue.

---

## 8. Implementation scope

**Core visual upgrade (this pass):** tokens + fonts + wordmark; restyle form, cards, badges, buttons, state banners; CARTO tiles; 3 custom markers (facility_name stopgap); polyline casing + selected emphasis + click/hover-to-select; onboarding overlay; pinned GPX footer; remove `max-w-7xl`; map legend; StatusMessage component.

**Heavier / behavioral (scope explicitly):** mobile draggable bottom sheet; form collapse-to-summary + Edit; 422/error recovery actions + retry-resubmit; threading `kind` through the API (backend touch).
