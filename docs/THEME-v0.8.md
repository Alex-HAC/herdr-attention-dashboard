# Theme v0.8 — spec and mockups

15 Nov 2026 · design by `uiux-coord-fable` · for Alex's "nicer theme" addendum in
`docs/briefs/UIUX-EXECUTE-v0.8.md`. **Spec and mockups. Implementation state is tracked in `REPORT-v0.8-progress.md`.**
Written so that any builder (Claude or Codex) can implement it without further design input.

- Mockup (open in a browser; add `?light` for the light theme): `briefs/mockups/theme-v0.8.html`
- Stills: `briefs/mockups/theme-v0.8-dark.png`, `briefs/mockups/theme-v0.8-light.png`

The mockup shows the three screens Alex asked for at 390 px: phone home, agent page and the
Notifications view. Its `:root` block **is** the token set below; lift it as it stands.

## What makes it look less cheap

1. **Tokyo Night surfaces instead of neutral greys.** Four stacked surface levels in the palette's
   blue-black, so cards sit on the page instead of being flat boxes.
2. **One strong signal.** Red (`--needs`) is the only solid, filled colour, and it is used only for
   "waiting on you". Every other state is a hue on a ring, a dot or a 14% tint.
3. **One interactive accent.** Tokyo Night purple (`--accent`) for primary buttons, the selected tab,
   focus and the focused notification. Status colours are never used for controls.
4. **Real type scale and spacing scale.** Five text styles and a 4 px spacing grid, used everywhere,
   with tabular numerals for counts, times and percentages.
5. **One icon set.** 20 px line icons, 1.75 px stroke, round caps, inline SVG. No emoji as icons.
6. **Quiet depth and motion.** A hairline top highlight and a soft shadow on cards; two durations,
   one easing curve; a pulse only on a node that waits on you.

## Tokens

Dark is Tokyo Night (Night). Light is derived from Tokyo Night Day with body text darkened to indigo
for contrast. The app keeps following `prefers-color-scheme`, with `data-theme` as the override.

> **v0.9.4:** colours replaced. There is no light switch any more; the dashboard uses the live Omarchy
> palette (Tokyo Night by default) on every device. See [`THEME-v0.9.4.md`](THEME-v0.9.4.md).

### Colour roles

| Token | Dark | Light | Use |
|---|---|---|---|
| `--bg` | `#16161e` | `#e6e7ed` | page |
| `--surface` | `#1a1b26` | `#f3f4f8` | cards |
| `--raised` | `#1f2335` | `#ffffff` | buttons, chips, tab bar, sticky bars, sheets |
| `--overlay` | `#24283b` | `#ffffff` | modals, popovers |
| `--pressed` | `#292e42` | `#e1e3ec` | pressed and selected rows |
| `--line` | `#2a2e42` | `#d6d8e3` | card borders, dividers |
| `--line-strong` | `#3b4261` | `#b9bdd0` | button borders, graph edges |
| `--text` | `#c0caf5` | `#2b3050` | body |
| `--text-strong` | `#dfe4fb` | `#1c2038` | titles, numbers, the thing to read first |
| `--muted` | `#9aa5ce` | `#565d85` | secondary text |
| `--faint` | `#8189b3` | `#5f668e` | labels, timestamps |
| `--accent` / `--accent-ink` | `#bb9af7` / `#1a1b26` | `#6a3fc0` / `#ffffff` | primary action, selection, focus, AI marker |
| `--needs` / `--needs-ink` | `#f7768e` / `#1a1b26` | `#c9264f` / `#ffffff` | waiting on you, and nothing else |
| `--working` | `#7aa2f7` | `#2466c8` | state: working |
| `--done` | `#9ece6a` | `#4d7326` | state: done, fresh data dot |
| `--waiting` | `#a9b1d6` | `#565d85` | state: waiting (idle, not on Alex) |
| `--parked`, `--unknown` | `#737aa2` | `#7a81a6` | parked (solid ring), unclear (dashed ring) |
| `--caution` | `#ff9e64` | `#a85400` | possible stall, quota near limit, stale data |

Soft fills are not separate tokens any more. Use
`color-mix(in srgb, var(--c) var(--tint), transparent)` with `--tint: 14%` (dark) or `10%` (light).
`--accent-soft`, `--needs-soft` and `--caution-soft` exist as shorthands for the three most used.

Measured contrast against `--surface` (WCAG; 4.5 is the bar for text, 3 for rings and dots):

| | text | muted | faint | accent | needs | working | done | waiting | caution | parked |
|---|---|---|---|---|---|---|---|---|---|---|
| Dark | 10.6 | 7.0 | 5.0 | 7.4 | 6.5 | 6.8 | 9.4 | 8.1 | 8.4 | 4.1 |
| Light | 11.6 | 5.8 | 5.1 | 6.2 | 4.9 | 5.0 | 5.0 | 5.8 | 4.9 | 3.5 |

Ink on solid fills: dark 6.5 (on `--needs`) and 7.4 (on `--accent`); light 5.4 and 6.8.
`--parked` is for rings and dots only, never for text.

### Elevation

| Level | Surface | Shadow |
|---|---|---|
| 0 page | `--bg` | none |
| 1 card | `--surface` + 1 px `--line` | `--shadow-1` |
| 2 raised (tab bar, sheet, sticky bar, focused card) | `--raised` | `--shadow-2`, bars also `backdrop-filter: blur(14px)` |
| 3 overlay (modal) | `--overlay` | `--shadow-2` over a 55% `--bg` scrim |

- Dark: `--shadow-1: inset 0 1px 0 rgba(255,255,255,.04), 0 1px 2px rgba(0,0,0,.30)`;
  `--shadow-2: inset 0 1px 0 rgba(255,255,255,.04), 0 10px 30px -12px rgba(0,0,0,.65)`.
- Light: `--shadow-1: 0 1px 2px rgba(40,44,80,.06), 0 1px 1px rgba(40,44,80,.04)`;
  `--shadow-2: 0 12px 32px -14px rgba(40,44,80,.28), 0 2px 6px rgba(40,44,80,.06)`.
- No card inside a card. Inside a card, separate with a 1 px `--line` divider.

### Shape, space, type, motion

| Group | Tokens |
|---|---|
| Radius | `--r-xs 6px` (quotes, inputs) · `--r-sm 10px` (buttons) · `--r-md 14px` (cards) · `--r-lg 20px` (sheets) · `--r-full 999px` (pills, chips) |
| Space (4 px grid) | `--s-1 4` · `--s-2 8` · `--s-3 12` · `--s-4 16` (gutter, card padding) · `--s-5 20` · `--s-6 24` · `--s-8 32` |
| Font | `--font: "Inter", "SF Pro Text", system-ui, -apple-system, "Segoe UI", Roboto, sans-serif` · `--mono: "JetBrainsMono Nerd Font", "JetBrains Mono", "CaskaydiaMono Nerd Font", ui-monospace, "SF Mono", Menlo, monospace` |
| Type | `--t-micro 600 11/14`, uppercase, +0.06em (section labels) · `--t-small 450 12.5/17` · `--t-body 450 15/22` · `--t-title 650 17/22` · `--t-head 700 22/28`, −0.015em |
| Numbers | `font-variant-numeric: tabular-nums` on `body` |
| Motion | `--dur-1 120ms` (press, hover) · `--dur-2 200ms` (sheet, expand, route change) · `--ease cubic-bezier(.2,.8,.2,1)` · only `opacity` and `transform` |
| Touch | `--tap 44px` minimum for every control; list rows 56 px |

- Fonts are never loaded from a CDN. Inter is used when installed; optionally bundle
  `InterVariable.woff2` under `herdr_attention/web/` and declare it with `@font-face`.
- `prefers-reduced-motion: reduce` turns all transitions and the pulse off.
- The pulse ring on a "waiting on you" node runs three cycles of 2.4 s, then stays as a static ring.

## Components

| Component | Rule |
|---|---|
| Card | `--surface`, 1 px `--line`, `--r-md`, `--shadow-1`, padding `--s-4`. |
| Status header | A card, 64 px min. Calm: check icon in `--done`, "Nothing needs you", neutral surface. Urgent: `--needs-soft` over the surface, border mixed 45% towards `--needs`, the count in `--t-head` and red, whole row tappable. A one-line summary of the other counts sits under it in `--muted`. |
| Pill (state) | 24 px high, `--r-full`, text in the state hue on its tint. **Only "Waiting on you" is solid** (`--needs` fill, `--needs-ink` text). |
| Button | 44 px high, `--r-sm`. Primary: `--accent` fill, one per card at most. Default: `--raised` with `--line-strong` border. Quiet: text only, `--muted`. No black buttons. |
| Chip (link) | 32 px high, `--r-full`, `--raised`, 15 px icon + label. Used for "go to" links (Agent, Conversation, Output, PR). Chips navigate; buttons act. |
| Quote / evidence | `--mono` 12.5/18 on `--bg`, 3 px left bar in `--line-strong`, `--r-xs`. |
| Section label | `--t-micro`, `--faint`, with the count ("Waiting on you · 1"). |
| List row | 56 px min, 8 px state dot, title in `--text-strong` 600 when unread; read rows drop to 450 and `--muted` with a hollow dot. |
| Focused notification | Border mixed 60% towards `--accent`, 3 px `--accent-soft` halo, `--shadow-2`. |
| Bottom tab bar (under 900 px) | Four tabs, 48 px targets, safe-area padding, `--raised` at 92% with blur. Selected tab in `--accent`. Badge in `--needs` only when something unread is waiting on you, otherwise neutral (`--pressed` fill, `--text`). |
| Caution note | `--caution-soft` fill, icon in `--caution`, text in `--muted`. Used for the terminal hint, stall and stale notes. |
| Focus ring | 2 px `--accent`, 2 px offset, on every focusable element. |
| Graph | Regions: `--raised` at 70% with a `--line` stroke, label inside the top in `--t-micro`. Edges: `--line-strong` 1.5 px; inferred edges dotted. Nodes: 2 px ring in the state hue over a 16% tint; the waiting-on-you node is filled `--needs` with the glyph in `--needs-ink`. Labels 12 px with a 4 px halo in `--bg`. Parked: dashed ring. |

Icons (inline SVG symbols, see the `<defs>` block in the mockup): graph, list, bell, settings
(sliders), check, question, chevron, sparkle (AI), terminal, conversation, agent, eye (watch closely),
pull request, fit, warning. They replace the current ⚠ ▦ 👁 ⇅ ☰ and ✨ glyphs. Node glyphs stay text:
`?` waiting on you · `◐` working · `✓` done · `○` waiting · `–` parked · `·` unclear · `!` caution.

## Phone layout

- 16 px gutters; content respects `env(safe-area-inset-*)` at the top bar and the tab bar.
- Home: top bar (name, short freshness) → status header → graph filling the rest → tab bar. The graph
  fits the area between the header and the tab bar (the mockup's graph is a static drawing and leaves
  more empty space than the real fit should).
- Tab bar: **Graph · List · Notifications · Settings.** This replaces the review's "Needs you" tab:
  the status header already answers "does anything need me" on both Graph and List, and the
  Notifications tab carries the unread badge Alex asked for.
- From 900 px up: no tab bar; the same four destinations sit in the top bar with labels.

## Migration from the current CSS

Current names stay valid, so the change can land in steps:

| Today | Becomes |
|---|---|
| `--bg`, `--surface`, `--text`, `--muted`, `--faint`, `--line` | same names, new values |
| `--surface-2` | alias of `--raised` |
| `--focus` | alias of `--accent` |
| `--radius`, `--radius-sm` | aliases of `--r-md`, `--r-sm` |
| `--needs`, `--working`, `--done`, `--waiting`, `--parked`, `--unknown`, `--caution` | same names, new values |
| `--*-soft` | `color-mix(...)` with `--tint`; keep the old names as aliases for one version |
| `--shadow` | alias of `--shadow-1` |

**Herdr's status colours no longer overwrite the state tokens.** Today `applyStatusColors` replaces
`--working`, `--needs`, `--done`, `--waiting` and `--unknown` with Herdr's own colours, which makes
"working" yellow (the same family as caution) and "waiting" green. With this theme the dashboard owns
its state colours; Herdr's colour is shown only as the small dot next to the "Herdr status" line in
an agent's details. This is the one behaviour change in the theme and is listed under Decisions.

## QML plugin

The plugin keeps taking its base foreground and accent from the Omarchy shell theme, so it still
matches the bar. From this spec it takes: the state colours (`needs`, `working`, `done`, `waiting`,
`parked`, `caution`), the radii, the 4 px spacing steps and the type sizes. Source of truth is the
token block in `web/style.css`; `scripts/gen_tokens.py` writes `plugin/alexvdb.herdr-attention/Tokens.js`
(`.pragma library`) from it, and a test fails when the two drift.

## Implementation slices

In order. T1 to T3 change no layout and can ship alone; T4 to T6 ride with the Notifications view and
Phase 3.

| # | Slice | Files | Effort |
|---|---|---|---|
| T1 | **Tokens.** The token block at the top of `web/style.css`, between `/* tokens:start */` and `/* tokens:end */`, with the aliases above (**done on main, 15 Nov**; a separate `tokens.css` was dropped because static files are an allowlist). Still to do: stop `applyStatusColors` overwriting state tokens, and a test that no six-digit hex colour is left outside the token block in `style.css`, `app.js` or `graph.js`. | `web/style.css`, `web/app.js` | S |
| T2 | **Base and components.** Font stacks, type styles, cards, buttons (no black primaries), pills (only "Waiting on you" solid), chips, switches, inputs, focus ring, 44 px targets, caution note. | `web/style.css` | S–M |
| T3 | **Icons.** One inline SVG sprite and an `icon(name)` helper; replace the emoji and glyph icons. | `web/app.js`, `web/graph.js`, `web/index.html` | S |
| T4 | **Graph styling.** Regions, edges, nodes, halos and the three-cycle pulse from the tokens. | `web/graph.js`, `web/style.css` | S |
| T5 | **Phone shell** (this is Phase 3 slices 3.1 and 3.2): status header and bottom tab bar as specified here. | `web/app.js`, `web/graph.js`, `web/style.css` | M |
| T6 | **Screens.** Notifications view and agent page laid out as in the mockup. | `web/app.js`, `web/style.css` | S |
| T7 | **Plugin tokens.** `scripts/gen_tokens.py`, `Tokens.js`, `Panel.qml` uses them, drift test. | `scripts/`, `plugin/` | S–M |

Acceptance for each slice: full suite and `scripts/qml-check.sh` pass; harness screenshots at 390 px
and 1280 px in both themes look like the mockup; `prefers-reduced-motion` is honoured; the contrast
table above still holds if any value changed.

## Decisions for Alex

1. **Dashboard-owned state colours** (see Migration). Recommended. The alternative keeps Herdr's
   colours for node rings and accepts yellow "working" next to orange "caution".
2. **Purple as the interactive accent.** Recommended, because blue is the "working" state. The
   alternative is Tokyo Night blue for the accent and cyan for "working".
3. **Tab bar without a "Needs you" tab** (Graph · List · Notifications · Settings). Recommended.
4. **Bundle Inter** (about 350 KB, served locally). Optional; without it the phone uses its system
   font, which is fine.
