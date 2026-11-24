# Theme v0.9.4: Tokyo Night from the live Omarchy palette

Brief: `THEME-QUOTA-CLEANUP-v0.9.4.md`, section 1. The phone, the desktop window and the bar panel
now use the Omarchy theme's Ghostty palette. This is the palette that Herdr's "terminal" theme
shows, including Herdr's status colours. The palette is read live, so a later Omarchy theme change is
followed without a reinstall.

## Why the v0.8.3 Tokyo Night look no longer showed

1. **Light was the default.** Since v0.8.3 the first `:root` block in `style.css` was a light theme
   (derived from Tokyo Night Day). Tokyo Night applied only under `prefers-color-scheme: dark`. So
   any browser that reports light got the light theme: a light-mode phone, a tailnet browser, or a
   headless check. Nothing in the app ever set `data-theme`. The v0.8.3 build was checked in the
   light theme only (REPORT-v0.8-progress, "light theme only").
2. **The dark block was hand-written and was not the Omarchy palette.** It had bg `#16161e`
   (Ghostty: `#1a1b26`), text `#c0caf5` (Ghostty fg: `#a9b1d6`) and accent `#bb9af7`.
3. **The state colours were the dashboard's own, not Herdr's.** Working was blue and done was green,
   but Herdr shows working yellow, done teal and idle green. v0.8.3 reduced Herdr's colours to one
   small dot.
4. **The bar panel had a fixed copy.** `Tokens.js` was generated at build time and picked light or
   dark from the shell foreground. It could not follow an Omarchy theme change.

On this laptop the desktop window's Chromium reports dark mode (portal `color-scheme` = 1, GTK
`Adwaita-dark`). So it showed point 2 and point 3. A light-mode phone showed point 1.

## How it works now

- `herdr_theme.read_palette()` reads `~/.local/state/omarchy/current/theme/ghostty.conf`, then
  `colors.toml` for missing values. Each value that is still missing comes from `TOKYO_NIGHT`. The
  files are only read, never written.
- `herdr_theme.current()` maps the palette and Herdr's status colours (`resolve()`, which also
  respects a Herdr built-in theme and `[theme.custom]`) to the dashboard colour tokens. It re-reads
  the files when ghostty.conf, colors.toml, theme.name or Herdr's config.toml change. It checks their
  mtimes on each request and never raises.
- The server sends the tokens in two ways:
  - **`/theme.css`**: a `:root` block. No login is needed, so the login page is themed too.
    `Cache-Control: no-store`.
  - **`view.theme`**: `key`, `name`, `mode`, `tokens`, `tint` and `source`.
- `index.html` loads `theme.css` after `style.css`. When `view.theme.key` changes, `app.js`
  (`applyTheme`) loads the new sheet next to the old one, removes the old one when the new one is in,
  and sets `meta[name=theme-color]`.
- The bar panel takes `Tokens.palette(view)`, which is `view.theme.tokens`. Each missing value comes
  from the Tokens.js fallback. The panel's base foreground and accent still come from the Omarchy
  shell theme, which follows the same theme.
- **One hand-written source:** `herdr_theme.TOKYO_NIGHT`. `scripts/gen_tokens.py` writes the
  fallback into the `palette:start/end` block of `style.css`, the theme-color meta in `index.html`
  and `Tokens.js`. `--check` and `tests/test_theme_tokyo_night.py` fail on drift. app.js, graph.js
  and the QML files contain no hex colours.
- **Contrast guard:** text and state colours are the palette's own when they meet WCAG AA. Otherwise
  they move just far enough towards black or white. Tokyo Night passes unchanged; the guard only
  matters for other Omarchy themes, for example light ones.

## Palette comparison

| Role | Omarchy tokyo-night (ghostty.conf) | Herdr ("terminal" theme) | Dashboard v0.9.4 | v0.9.3 dark | v0.9.3 default (light) |
|---|---|---|---|---|---|
| Background | bg `#1a1b26` | bg `#1a1b26` | `--bg` `#1a1b26` | `#16161e` | `#e6e7ed` |
| Text | fg `#a9b1d6` | fg `#a9b1d6` | `--text` `#a9b1d6` | `#c0caf5` | `#2b3050` |
| Strong text | bright white `#c0caf5` | | `--text-strong` `#c0caf5` | `#dfe4fb` | `#1c2038` |
| Strong line | bright black `#414868` | | `--line-strong` `#414868` | `#3b4261` | `#b9bdd0` |
| Selection | `#292e42` | | `--pressed` `#292e42` | `#292e42` | `#e1e3ec` |
| Accent | colors.toml accent `#7aa2f7` (blue) | | `--accent` `#7aa2f7` | `#bb9af7` | `#6a3fc0` |
| Waiting on you | red `#f7768e` / bright red `#ff7a93` | Blocked = ANSI 9 `#ff7a93` | `--needs` `#ff7a93` | `#f7768e` | `#c9264f` |
| Working | yellow `#e0af68` | Working = ANSI 3 `#e0af68` | `--working` `#e0af68` | `#7aa2f7` (blue) | `#2466c8` |
| Done, not reviewed | cyan `#449dab` | Done = ANSI 6 `#449dab` | `--done` `#449dab` | `#9ece6a` (green) | `#4d7326` |
| Waiting (idle) | green `#9ece6a` | Idle = ANSI 2 `#9ece6a` | `--waiting` `#9ece6a` | `#a9b1d6` | `#565d85` |
| Unclear | fg `#a9b1d6` | Unknown = ANSI 7 `#a9b1d6` | `--unknown` `#a9b1d6` | `#737aa2` | `#7a81a6` |
| Caution | bright yellow `#ff9e64` | | `--caution` `#ff9e64` | `#ff9e64` | `#a85400` |
| Magenta | `#ad8ee6` | | not used (accent is blue) | | |

Derived tokens (mixes of the palette's own fg and bg): `--surface` `#1f202c`, `--raised` `#232431`,
`--overlay` `#262835`, `--line` `#2d2e3d`, `--muted` `#8e94b5`, `--faint` `#8c93b3`
(= `--parked`), `--tint` 8%.

## Contrast (WCAG 2), measured by `tests/test_theme_tokyo_night.py`

| Token | Colour | on bg | on surface | on raised | on overlay | on its own soft fill (8%) |
|---|---|---|---|---|---|---|
| `--text` | `#a9b1d6` | 8.1 | 7.6 | 7.3 | 6.9 | |
| `--text-strong` | `#c0caf5` | 10.6 | 10.0 | 9.5 | 9.1 | |
| `--muted` | `#8e94b5` | 5.7 | 5.4 | 5.2 | 4.9 | |
| `--faint` / `--parked` | `#8c93b3` | 5.6 | 5.3 | 5.1 | 4.8 | 4.7 |
| `--accent` | `#7aa2f7` | 6.8 | 6.4 | 6.1 | 5.8 | 5.6 |
| `--needs` | `#ff7a93` | 6.9 | 6.5 | 6.2 | 5.9 | 5.8 |
| `--working` | `#e0af68` | 8.5 | 8.1 | 7.7 | 7.3 | 7.0 |
| `--done` | `#449dab` | 5.4 | 5.1 | 4.9 | 4.6 | 4.6 |
| `--waiting` | `#9ece6a` | 9.4 | 8.8 | 8.4 | 8.0 | 7.5 |
| `--unknown` | `#a9b1d6` | 8.1 | 7.6 | 7.3 | 6.9 | 6.5 |
| `--caution` | `#ff9e64` | 8.4 | 7.9 | 7.5 | 7.2 | 6.9 |

The test also checks the following pairs:
- every text token on every soft state fill (the red status card, notes). The lowest are muted (4.6)
  and faint (4.5), both on the waiting fill.
- ink on solid fills: `--accent-ink` on accent 6.8, `--needs-ink` on needs 6.9.

All pairs are at or above AA (4.5). To keep `--done` at AA on its own pill, `--tint` went from 14%
to 8%. Muted and faint are now almost the same colour; both must stay at AA on the tinted fills.

## Screenshots (fixture data, device in light mode, so the old light switch would have shown)

- `docs/screenshots/v0.9.4/theme-graph-390x844.png`, `theme-list-390x844.png`, `theme-agent-390x844.png`
- `docs/screenshots/v0.9.4/theme-graph-1280x800.png`, `theme-list-1280x800.png`

The live palette on this laptop (Omarchy `tokyo-night`, Herdr `terminal`) gives exactly the built-in
set: `herdr_theme.current()["tokens"] == herdr_theme.fallback_theme()["tokens"]`.

## Limits

- The bar panel's base foreground and background come from the Omarchy shell (`Commons.Color`). Only
  the state colours come from the server.
- When the server is down, the panel and the page use the built-in Tokyo Night set.
- A theme change reaches the page on its next poll, and the bar panel on its next view refresh.
