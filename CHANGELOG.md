# Changelog

## 0.9.11.1 (24 Nov 2026)

The Visuals hub was showing blank tiles for some images; opening one still showed the full picture (Alex 14:46).

- Cached previews are accepted only if they look like a real JPEG or WebP. Short or bogus files (including leftover `jpeg-thumb-bytes` stubs from tests that wrote into the live cache) are deleted and remade.
- If ffmpeg cannot make a preview, the tile **falls back to the original image** instead of a 1×1 placeholder that looks blank. Fallbacks are cached for a minute so a later preview can still replace them. Real 320 px thumbs keep the long cache and ETag.
- The thumbnail URL and ETag include a generation stamp so a phone that cached a bad preview will refetch. The hub also falls back to the full image if the preview request itself fails.


## 0.9.11.0 (24 Nov 2026)

The view you get when you click an agent was an information dump (one coordinator sheet was about 8 desktop screens). It is now calm and in three levels, the same in the graph sheet and on the agent page.

- **One component** (`web/details.js`) draws the graph sheet and the agent page; the old `inspector()` and `renderAgent()` bodies are gone.
- **Status card** first: state stripe and pill with the time in that state, task title, the last thing it said, and one row of chips (account, model with effort dots, context % when the window is known, folder, sandbox only when sandboxed), "reports to" when it reports to someone. Gone and out-of-quota (with the reset time) are a one-line banner. Watch closely is an eye toggle; its explanation is the tooltip.
- **Needs you** only when something is open: the terminal dialog (tap a choice, then Send), each open question once (quote and context only when they add words, the route only when the answer goes to another agent), "may want something" with Reply and Nothing needed, finished (Mark read), stall or failure, out of quota (Got it). A sent answer folds to a tick line and the count goes down; a failed send keeps the row and the draft; a check fills only after the server confirmed. Older answers are under Activity › Your answers.
- **One action bar** (Reply, Terminal, Open in Herdr, Page or Graph) and **four doors** with a live value: Status (coordinator team bar and the report in coloured blocks, one Nudge for all stuck workers; the AI summary in blocks; Why this state folded), Activity (24-hour state ribbon, one timeline with All · Finished · Alerts, Your answers, messages and terminal lines), Usage (runs on with its source, context bar, stacked token bar, age and active with a ratio bar, "How this is measured" folded), Links (each agent once, live workers as chips, gone ones folded behind a count, declared roles only, the editor and history folded).
- New page routes `#/a/<id>/status`, `/activity`, `/usage`, `/links` (UI contract). Old links still land: `/conversation` and `/output` open Activity and load that part, `/report` opens Status, `/terminal` the terminal sheet. Escape on a screen goes back; another node opens on the main screen.
- **Button vocabulary** used app-wide: filled accent only for the one thing to do, a round check for check-offs, a chat icon on Reply, ↗ on Open in Herdr, a spark and magenta tint on token-costing AI buttons, an icon toggle for Watch closely.
- Uses the 0.9.10.0 data: what a quiet agent waits for is one line under the state pill, in its graph ring colour; Links shows each line's last message and when the coordinator waits for a worker; Why this state says why the same screen is expected. Status report has the AI look; the team status is a pill on the report line; token bar segments have distinct tints; check rows stack under 480 px; large token counts read 1.14B.
- List rows: a reason every row of a group shares is said once under the heading; Summarize, Terminal and Open in Herdr are one line of small icon buttons. Workspace agent cards no longer run past the right edge at 390 px. Question routes name agents by their display name, not the task title.

## 0.9.10.3 (24 Nov 2026)

- "Waiting for a worker" ignores workers that have closed: a closed pane keeps its last status ("working"), which made one idle coordinator wait for 48 workers.

## 0.9.10.2 (24 Nov 2026)

- "Waiting for a worker" counts Herdr pane workers only. In-process subagents report "working" through hooks, which can stay stale for days; they made an idle coordinator look as if it waited for 48 workers.

## 0.9.10.1 (24 Nov 2026)

- In-process subagents no longer copy their parent's waiting state (they share its transcript).
- Links rebuilt for in-process subagents get their last action and waiting flag too, so the chevrons and flow dots show on the live graph.

## 0.9.10.0 (24 Nov 2026)

The graph showed quiet agents as if they had stalled, even when they were waiting on purpose (Alex 11:07). It now shows what they wait for, the last message on each line, and calm motion by state.

- **Waiting states** (`flow.py`, from each agent's own transcript, `activity.py`):
  - *Waiting for output* (magenta, turning dashes): a command has run for 20 s or more, or an idle agent still has a background task (Claude `run_in_background` / `Monitor`, until its task notification).
  - *Scheduled* (blue, a dot that ticks once a second): a sleep is running (`sleep N`, Codex `clock.sleep`), or an idle Claude agent set a `ScheduleWakeup`.
  - *Waiting for a worker* (green, still ring): it runs `herdr agent wait` / `herdr agent prompt --wait`, or it is an idle coordinator while a worker that reports to it (or that it spawned) is working. Dots flow along the line to that worker.
  - The node tooltip says why and for how long (or when the wake-up is due). A running command, a sleep or a wait is never flagged as a possible stall.
- **Last action on a line**: a chevron in the middle points from sender to receiver. Yellow = the coordinator sent a task (`herdr agent prompt`); teal = the worker reported back or finished its turn after that task. Bright under 2 min, then fading out over 2 h. A dot travels along the line while it is fresh. The line's tooltip and the selected-line label say "sent task 4m ago" and when the output was last checked.
- **Motion**: working nodes breathe slowly. Stalled and needs-you nodes stay still. Only opacity and transforms animate; `prefers-reduced-motion` stops all of it.
- Only short descriptions are kept from the transcript (the agent's own description of a command, or the program name and first argument), redacted with `output.redact`.
## 0.9.9.2 (24 Nov 2026)

The header freshness slot (bold Herdr, status pip, "3s ago") now shows more at a glance (Alex 10:56).

- Overall status is **colour only**: the pip uses the existing palette (working, idle, needs you, or stale/disconnected).
- A compact **segmented bar plus working/total** (for example `3/8`) shows how many live agents are working.
- The **"N to look at" count is gone** from this first-glance cluster (phone notifications still cover it). The "waiting on you" title is unchanged. Freshness ("3s ago") stays, quieter, beside the meter.
- The same slot is used in the list top bar and in the graph status header (the top bar is hidden on the graph). On a narrow phone the meter stays next to Herdr and the quota chips wrap underneath. The Omarchy bar plugin is unchanged (collector-only deploy).


## 0.9.9.1 (24 Nov 2026)

The Visuals hub was leaving tiles blank for a long time when many pictures were new (Alex 08:34). The image viewer also could not pan once zoomed.

- Hub tiles request **320 px WebP/JPEG previews** (video poster via ffmpeg, otherwise a placeholder). Previews are cached on disk by media id and mtime, served with a long `Cache-Control` and `ETag` (304 on revalidate). A failed preview never falls back to the full original.
- Thumbnail and hub API reads reuse the **cached dashboard view** and do not run git on every tile, so the first images appear quickly even on a large catalog.
- The client loads only what is on screen (`IntersectionObserver`, `loading=lazy`, `decoding=async`, 5 at a time), first page is 24, with a skeleton that shows the agent name, and a "Couldn't load" error state. The viewer shows the preview first, then swaps in the full image, and preloads only the next and previous items.
- When zoomed (pinch, double-tap, wheel, or +), drag pans the image and stays inside the edges. Swipe still goes to the next item only when you are not zoomed, or when you are already at the edge. Double-tap zooms on the tap point. The image stage uses `touch-action: none`.

## 0.9.9.0 (23 Nov 2026)

Global visuals: new images, videos, and gifs from any watched agent appear app-wide, not only on an agent page.

- In-app toast (thumbnail, agent, time) on whatever page you are on; stacks or batches when several arrive, auto-dismisses, tap opens the viewer. First load does not toast the unseen backlog.
- Header icon (and graph toolbar icon) with an unseen count badge opens the Visuals hub. Seen/unseen is stored on the server so phone and desktop stay in sync.
- Visuals hub (`#/visuals`) lists recent visuals newest-first, grouped by day, with agent and workspace filters and paged scroll. Uses the 0.9.8.11 dedupe.
- Fullscreen viewer: swipe or arrows between items, pinch/zoom for images, video playback, metadata, Download (attachment Content-Disposition, id-only path), and Go to agent (the producing agent, including team-strip items).
- Phone (ntfy) "Visuals" alerts exist in settings but are off by default. Existing notification settings still apply.
- Downloads only serve discovered visuals (no arbitrary file read; a `path=` query is rejected). `/api/focus` stays desktop-only.

## 0.9.8.11 (23 Nov 2026)

Latest visuals was showing the same screenshot twice (Alex 22:20: "The herdr dashboard shows latest visuals twice for some reason").

- The List and team galleries listed the same file twice when two agents had copies of one tree (for example `ui/web-app` and `app-base/web-app`, both `docs/images/robot-129/colouring-narrow-fixture.png`, same bytes and size, different full paths). Discovery keys items by the full path, so each copy got its own media id. **Copies that share a repo-relative path and size are now shown once**, keeping the newest reference. Files with the same name at a different path or a different size stay separate (the setup and onboarding `ready-720.png` pair is kept).
- On a coordinator's page, **Latest visuals appeared twice as a heading**: once inside the coordinator status report (the team strip) and again as the agent's own strip just below, often empty after the folder cap. The agent strip is no longer added when the team strip is already there.

## 0.9.8.10 (22 Nov 2026)

The same "out of quota" card kept coming back (Alex 15:15: "It keeps throwing me this notification but Ive seen it many times and we havent hit limit so wth?").

What was going on, from state.db and the pane (read-only, w4:p5T, never prompted):

- The card at 15:10 was a **Finished** event, not a quota-stop. The Placement coordinator handover (claude:4bf1c2ec…, Opus 5.5, pane w4:p5T) has had Claude's own banner on screen since **21 Nov 13:20 CET**: "You've hit your weekly limit · resets Nov 23, 8pm (Europe/Amsterdam)".
- **That limit is real.** The reset time is Claude Code Personal's weekly window, which is at 100% (Fable Weekly on Personal is also 100%; this agent is Opus, so it is the account weekly). The trace has no new work since 21 Nov 13:20. Claude Code Main/Work weekly is also at 100% (resets Wed 05:59); its 5-hour window is only 2% and Fable on Main is 89%.
- Herdr kept flipping the pane done → working → done (the "done until the pane is viewed" status, plus this agent is watched closely so its screen is polled often). Each flip **resolved the last Finished and opened a new one**. The AI gate then titled it "Weekly limit reached…", the Notifications page showed an **Out of quota** badge (from that title), and it **pushed the phone**. Alex acked them; they came back. **20 such pushes in 48 hours**, all sent; the latest (event 7871, 22 Nov 15:12) was still open.
- A quota-stop was recorded once at 13:21 on the 21st, but as `claude:unknown` with no reset time. A brief "working" flicker **forgot the screen hash**, so the stop was not reused; later "finishes" went out as new completions instead.
- The two 100% header rings at 390 px are **Claude Personal weekly** and **Claude Main weekly**. Both are true account windows, not model-only. Codex Personal is 67% wk, Codex Work about 38% wk (36% on the screenshot). The 0.9.8.8 rule (Fable / named models off the rings) is doing its job.
- The leftover line "You replied 'Sure' to Finished, not reviewed yesterday at 14:05" is a bug. Those "Sure" replies (21 Nov 14:05) answered earlier questions (the 1.2.11 sign-off and the shrink-metrics baseline). They were stored with agent name "Finished, not reviewed", and the receipt stayed on later cards because the last assistant message was frozen at the 13:20 limit banner.

Fixes:

- **One card per agent per limit/reset.** A working flicker no longer forgets the banner. A read, sent or acked quota card (including those AI-titled "Weekly limit reached" finishes) is not raised again until that reset passes.
- **A quota stop is only flagged when the limit is real**: the provider banner, and when we have usage data, a window that is actually at 100%. The account is matched from the launch record, or from the reset time on the banner (Nov 23 8pm → Claude Personal).
- **Quota cards never push the phone**, even if the AI writes a limit title on a Finished event, and even if quota alerts were switched on. They are listed on the dashboard only.
- A new Finished is not opened while the same limit still holds.
- The "You replied Sure" line stays on the question it answered; it is not shown on a later Finished or quota card, and a kind label is no longer used as the "to" name.

## 0.9.8.9 (19 Nov 2026)

Phone alerts you can tune (Alex 22:55: "make the notification to the phone easier to adapt and tune how i like, now theyre quite intense haha").

What was pushing, the 24 hours before (state.db): **137 pushes**, up to 7 in 10 minutes and 32 in the 22:00 hour. 118 were finished turns (111 "Finished", 7 "Might want to look"), 6 quota stops, 4 approvals, 3 failures, 3 milestones, 1 question. **97 came from one coordinator watched closely**, because watching closely skipped the finish guard and quiet hours. Since the 0.9.8.7 guard it was still 27 in 48 minutes. Phone mode was "Always" (pushes even at the laptop) and quiet hours were off.

- **Settings > Phone alerts**, first in Settings and made for the phone:
  - **Phone**: When I'm away / Always / Paused, with what each means.
  - **Snooze**: for 1 hour or until 08:00 (the end of quiet hours), and Resume now.
  - **Send test notification**.
  - **What comes to your phone**: one row per kind (Questions and approvals, Problems, Finished turns, Milestones, Worker reports, Quota stops). Each has on/off, a loudness (Quiet = ntfy low, no sound; Normal = ntfy default; Urgent = ntfy high, sound and pop-up) and "Only when I'm away from the laptop".
  - **When**: quiet hours (with times) and a rate limit, "At most 3 alerts per 30 min"; more wait and come together as one summary.
  - **Workspaces**: phone alerts on/off per workspace (the same setting as Watch / Alerts).
  - How many alerts were sent in the last 24 hours.
- **Blocking asks always push at once**: questions and approvals skip the snooze, quiet hours, the rate limit and "only when I'm away". Only Paused or a muted workspace holds them. For the ask protocol, `notify_prefs.is_blocking()` is the hook: an event whose evidence says `"blocking": true/false` decides for itself (`notify.blocking_at_once`, config only, default on).
- **Watching an agent closely** still turns all its alerts on, but no longer skips quiet hours, the snooze, the rate limit or the finish guard.
- Dashboard-only listings no longer use up the phone budget.

New defaults vs old:

| | Before | Now |
|---|---|---|
| Questions and approvals | high | Urgent (high), at once, always |
| Problems (failures, stalls) | high / default | Normal (default), only when away |
| Finished turns | default, even at the laptop | Quiet (low, no sound), only when away |
| Milestones, worker reports | off | off (Quiet, only when away if switched on) |
| Quota stops | on | **off** (no quota alerts on the phone) |
| Quiet hours | off, 22:00–08:00 | **on, 23:00–08:00** |
| Rate | at most 8 per 10 min | **at most 3 per 30 min**, then one summary (8 per 10 min stays as a hard cap) |
| Same agent's finishes | once per 20 min | once per 30 min |
| Watched closely | skipped guard and quiet hours | same rules as others |

Your saved "Always" phone mode is kept as you set it. Tap **When I'm away** under Phone alerts to let finished turns and problems wait while you use the laptop.

## 0.9.8.8 (19 Nov 2026)

Quotas (Alex 21:17: "Claude Code Personal shows 100% and Agents stopped, but new Opus agents run fine" and "can we somehow choose whats the % inside the radial?"):

- **A limit for one model no longer passes for the whole account.** Claude reports "Fable Weekly" next to "Weekly"; at 100 % it took the outer ring and the number, so Claude Code Personal read 100 % while its real weekly limit was 60 % and Opus agents ran fine. A window named after a model (`Fable Weekly`, Cursor's named-model limits) is now a line under the gauge ("Fable: 100% · resets Mon 20:00") and in the details ("Fable Weekly: for one model only ..."); it never sets the rings, the number, the level or "out of quota". Only the account's own windows do.
- **The flag says who stopped.** "Agents stopped" was one agent (a Fable worker). It now counts them: "1 agent stopped" / "3 agents stopped"; a full model limit with nobody on it says "Fable limit reached".
- **Choose the number in the middle**: Quotas > Settings > "Number in the middle": Fuller (default, as before), Weekly, 5-hour, or Both ("62 · 21"). It applies to the big gauges, the header mini dials and the bar panel, with a small "wk" / "5h" / "mo" under the number so you can see which limit it is. Saved as `quotas.centre` like the other quota settings.
- The header mini dials stack the unit under the number, and on a phone they are 2 px narrower each so four still fit at 390 px.

File viewer (Alex's brief):

- **Paths agents write are tappable.** A path like `team-registry/docs/working-styles.md` or `~/Work/x/NOTES.txt` (markdown, plain text, rst, org, adoc, log, csv/tsv) in a question card, a report, the conversation (assistant and your own messages), the agent page, the terminal lines and the live terminal opens the viewer.
- **The viewer**: a sheet (full screen on a phone, a large panel on the desktop) with the file rendered as safe markdown (the same DOM-only renderer as agent text: no HTML, scripts or links run), the resolved path and when it changed, Copy path (works over plain http too), Plain text, and **Diff vs original / Diff vs draft** when the same text names two related files (one name extends the other, e.g. working-styles.md and working-styles-agents-section.md, or one says draft). Missing files say "File not found".
- **Read-only and narrow by design**: `GET /api/file`, behind the login and the tailnet allow list. Relative paths start from the agent's folder and its git repo, then the other allowed folders. Only files under the watched agents' folders and repos and ~/Projects, ~/Work, ~/Documents (an agent sitting in home adds nothing); the real path after symlinks must stay inside; no `..`; no hidden files or folders (.env, .ssh, .claude, .codex, .git ...); nothing named like a secret (credentials, tokens, passwords, API or private keys); UTF-8 text up to 1 MB. The text is sent to that browser only (no-store), never stored, logged or given to the summary agent.

## 0.9.8.7 (19 Nov 2026)

Load speed. Measured on the laptop before this release (phone width, load average ~16): a cold load moved 4.6 MB and took 4.1 s to show the graph (8.8 s on a 4G-like link); a reload moved the same 4.6 MB again. /api/view alone was 3.8 MB, took ~3.3 s to build with ~700 subagent cards, and every open page fetched it every 10 s.

- **Gzip** for the page, scripts, stylesheet and JSON answers when the browser accepts it (the view is about 13x smaller on the wire).
- **Files are cached**: index.html names each file with its content hash (`app.js?v=…`), and those URLs are kept for a year; index.html, theme.css and the API stay no-store, so a new version still shows at once and the auto-reload keeps working.
- **Smaller font**: Inter subset to Latin, Latin Extended-A, Greek, punctuation, arrows and symbols, all weights (344 KB to 175 KB), preloaded with the stylesheet. Other scripts fall back to the system font. `scripts/subset_font.sh` rebuilds it from `third_party/inter/`.
- **Smaller view**: agent fields no screen reads (the last message text, a duplicate id, a revision key) stay on the server: about a third of the view.
- **One view build for many readers**: the browser polls, agent pages and the Herdr sidebar share a build for up to 10 s while nothing changed (2 s regardless); anything you do from the dashboard shows at once. The json and gzip of a build are made once too.
- **Less work per build**: the notification list and its unread count share one pass over the agents (was three), and the Herdr sidebar no longer builds a view every 5 s when it is off or passive.
- A view build slower than 1.5 s is logged (at most every 5 minutes) to find the next hot spot.

Notifications you could not find (19 Nov 21:49):

- **A busy coordinator no longer pings the phone every few minutes.** A release coordinator ended a turn every one to five minutes while reading worker reports, and each turn end went out as "Finished, not reviewed" (8 alerts in 12 minutes). A plain finish now waits 90 s first (a turn that resumes by then never pushes), and after one finish alert the same agent's next plain finishes within 20 minutes are listed, not pushed. Questions, approvals, milestones and agents you watch closely are not held back. Settings: `notify.finish_settle_seconds`, `notify.finish_repeat_minutes`.
- **Every alert has its row, and the badge counts only rows you can see.** Turn endings were hidden from Notifications even when they had gone to the phone, and the unread badge counted any unread item from the last 1000, including old ones far below the list. A pushed finish is now listed (grouped per agent), and the badge counts unread among the 50 rows the Notifications screen shows.

## 0.9.8.6 (19 Nov 2026)

- **Claude and Codex models look the same.** The model came from the same data for both (session trace or launch record), but the graph drew it two ways depending on whether the agent's *account* was recognised. A node with an account mark (all running Codex agents) showed "Sol 6.2 ●●●○○" under its name; a node without one (the Claude workers, whose launch account `claude-work` does not match a quota account) put the model in the small grey corner pill and left only the effort dots under the name. Now the corner shows who (the account mark, else the service by name: "Claude", "Codex") and the model and effort are always on the name's second line, for every service.
- **One chip text everywhere:** List rows, questions, replies, the terminal header, the bar panel and the workspace chips show "Opus 5.5 · high" / "Sol 6.2 · high" (short effort words: low, med, high, xhigh, max). Before, the chip had the model only. A model without a recorded effort shows the model alone (the hover says the effort is not recorded).
- **Unknown model, same chip:** in-process Claude subagents (and any agent without a trace or launch record) show the service by name in the same chip, dashed, with "model not recorded" on hover, instead of a different-looking label.
- **Answering one question no longer hides the agent's other questions.** A card answer reaches the agent as a turn like `Answer to "May it go in as a PR?": yes`; the 0.9.8.1 rule ("asks stay until Alex writes to the agent") counted that as writing to the agent and dropped every earlier message with an ask, so the agent's other open card vanished too. A card answer now resolves only its own card; a free message from Alex still clears the agent's earlier asks as before. (The answer itself went out by the normal "auto" rule: sent at once because the agent was idle; the `interrupt` column records every send-now, it did not interrupt a busy agent.)
- **Workspace bubble shrinks back.** Opening a coordinator's fold of finished subagents grew its workspace bubble, and closing it (or hiding subagents) left it huge around a small cluster: regions only ever grew. Now, when many nodes leave at once, the ones that stay are packed again around the centre and the bubble shrinks to fit; one agent leaving still moves nobody. After showing or hiding subagents the camera fits the new size, unless you have moved or zoomed the graph yourself.
- **An open fold draws at most 40 subagents** (the newest); the fold says "40 newest of 102 drawn". The list under the fold still names them all.
- **Graph "To look at" rows fixed.** Each row was a flex line with the "Clears when …" hint beside it, which squeezed the agent's name and summary into a one-word-wide column (~250 px tall rows, the state icon over the hint). Rows are now compact: state, name with its account mark and model chip, the reason on two lines at most, workspace and age; tapping opens the agent. The hint shows once under the section heading when all its rows share it (also in the List's attention card).

## 0.9.8.5 (19 Nov 2026)

- **Better service icons.** The account marks (quota dials and rings, the header pills, List rows, graph nodes, the agent page's "Runs on" line, the terminal header, questions and replies, Settings › Account icons) now use Omarchy's own agent icons from its built-in Agents bar plugin (MIT; copied into `third_party/omarchy-agents/` with the licence, credited in the README) instead of our hand-drawn letter and shape marks: Claude, Codex, Cursor, Antigravity, Grok, Grok Bot and GitHub Copilot, plus OpenCode, Kimi, Qwen, DeepSeek, Ollama, Z.ai, OpenRouter, MiniMax, Muse and Devin for accounts from those services. Antigravity and Grok Bot accounts get their own mark (they had Gemini's and Grok's).
- They stay in the account's theme colour on the same tile, with the same Personal/Work badges, so two accounts of one service still differ at a glance, and they follow the theme (Tokyo Night and light). At 16 px and less the mark fills more of the tile so it still reads. The bar panel draws the same marks.
- Unchanged: the text label when an account has no mark, uploaded images, and your Settings overrides (every mark id saved before still exists; Gemini and the plain shapes are still ours).

## 0.9.8.4 (19 Nov 2026)

- **Super + Shift + H opens the dashboard, wherever it is.** The shortcut looked for a window with class `herdr-overview` and launched with `--class=herdr-overview`. When Chromium was not running yet, that launch started the whole browser with that class, so every later browser window (a Google Drive tab, say) got it too, and the shortcut focused that window on its workspace instead of the dashboard. When Chromium was already running, the class was ignored and the dashboard window got Chromium's app id (`chrome-127.0.0.1__-Default`), so the shortcut did not find it and focused the wrong window or opened another one. Now the shortcut finds the dashboard by its title ("Herdr overview") or by its app id plus a "Herdr" title, never by class alone:
  - not open: opens it (no `--class`); a second press within 8 s does not open a second window;
  - open on another workspace: focuses it there;
  - hidden on a special workspace: moves it to the current workspace and focuses it.
  Only the dashboard window is moved or focused. Same key and the same `~/.local/bin/herdr-attention-overview` wrapper; no Hyprland config change.

## 0.9.8.3 (19 Nov 2026)

- **Refresh workspaces.** A "↻ Refresh" button in the Settings › Workspaces header asks Herdr for its workspaces, tabs and agents now (`POST /api/workspaces/refresh`; reads only, never creates, focuses, moves, renames or closes anything), then says what it found ("Found 9 workspaces, 1 new: …"). At most once per 3 s (429 otherwise); same login, CSRF and tailnet rules as every change. New workspaces still stay off until you switch them on.
- **New workspaces show up sooner by themselves.** The collector already re-read Herdr on its workspace events (and every 45 s), but an open Settings page kept the workspace list it loaded first, so a new workspace appeared only after leaving and reopening Settings. The view now carries a revision of the workspace list, and an open list reloads when it changes. A cheap `workspace.list` every 5 s also catches a missed event.
- **One row per workspace.** Settings › Workspaces had two rows per workspace (watch, then mute). Now each workspace is one row: its name, agent count and last activity, and one control **Off · Watch · Alerts** (Watch = shown here, no phone alerts; Alerts = shown here and sent to the phone). Same saved settings as before (`watch.enabled_workspaces`, `watch.muted_workspaces`); Off keeps the saved mute. Watched workspaces first, then the rest (dimmed); a filter box when there are more than 8.

## 0.9.8.2 (19 Nov 2026)

- **206 repeated questions → one card per ask.** An in-process subagent card is a copy of its parent's card, and since 0.9.6 that copy also carried the parent's question items. A coordinator with 102 finished subagents and a message with two items gave 2 × 103 = 206 open questions. Subagent cards now carry no items, and an item id is shown on one card only.
- **Same ask, one card.** Asks that repeat in later messages or come back reworded (a reminder, the report model's wording) are one card: same words, one inside the other, or nearly all words shared. The newest wording is kept, and an answer on any copy keeps it answered.
- **Fewer false asks.** A handover that lists jobs given to another agent no longer counts as asking you: "prepare one install for you to choose from" is not "for you to choose", and a question after "check …" ("Check its contract first. Is it the same family as X?") is something to look into, unless it says "you".
- Faster rules on long messages (the sentence before is kept, not re-split).

## 0.9.8.1 (19 Nov 2026)

- **Asks written as statements are found.** A coordinator's 23:43 status report ended with "One thing for you to decide tomorrow, not now: … I plan to open that port as a draft PR against his repo … Say if you would rather I only hand him a patch." No question mark, so 0.9.8 showed no question. The rules now also count "one thing for you to decide/confirm …:", "say / tell me / let me know if you would rather / prefer / want …", "your call", "up to you", "for you to decide", "needs your go / OK / approval / sign-off"; "I plan to X … say if you would rather I Y" gives the two choices as buttons (plus free text). Worker lines such as "rel-1210: question: …" are not asks for you.
- **Asks in earlier messages stay.** Only the agent's last message was read; a coordinator answers its workers seconds after a report, so the ask disappeared. Now every assistant message since your own last turn to that agent is read (120 entries deep after a start; a message with an ask is kept when it scrolls out, until you write to the agent) (rules only; workers' "name: …" messages to a coordinator do not count as yours). Same text twice = one card.
- **Coordinator reports ask the model for your questions.** The report prompt has a required `asks_for_owner` field (text, exact quote, offered options, urgency), with the rule hits as candidates. A model ask becomes a card only when its quote is found word for word in the agent's messages; one the model drops is still a card from the rules.
- **"For later".** An ask that says it can wait ("tomorrow, not now", "no rush", "when you're back") has a quiet "For later" badge, does not send a "Waiting on you" phone notification (it is "Finished" instead), and does not make the report "waiting on you".
- A failed Codex summary says why (for example "the CLI failed: You've hit your usage limit … try again at 4:31 AM") instead of "the CLI did not return JSON".

## 0.9.8 (18 Nov 2026)

- **Live terminal view.** A `>_ Terminal` button on every agent surface (List attention rows and notification rows, workspace agent cards, question cards and question groups, the agent page, the graph inspector; double-click / double-tap a graph node; "Live terminal" in an expanded bar panel row) opens the agent's screen as Herdr shows it: colours and styles from the CLI (truecolor, 256 colours, bold, dim, italic, underline, inverse), Tokyo Night for the 16 basic colours, a terminal font, box drawing and wide characters. Follows the newest output; scroll up to pause, "↓ Live" to return. Wrap toggle and A− / A+ text size (remembered); full screen on a phone, a large panel on the desktop. Earlier output (plain text) is shown above when the agent is not busy; lines that scroll off while the view is open are kept (up to 2000). Link: `#/a/<id>/terminal`; bar panel: `herdr-attention-overview --terminal <id>`.
- **Read-only and cheap.** `GET /api/terminal` uses Herdr's passive `agent.read` (`format: "ansi"`, visible screen) and never sends keys. One Herdr read per pane per 0.75 s however many viewers, at most 2 at a time; the page polls every second only while the view is open and the tab is visible, and sends no text when the screen did not change. Only colour codes are passed on (titles, links, cursor moves and other escapes are dropped); the `output.redact` patterns apply. Same login, tailnet allow list and watched-workspace rule as all agent data (unwatched agents: 404). Nothing is logged or stored.

## 0.9.7.5 (18 Nov 2026)

- **Open pages reload after an upgrade.** The laptop window (and a phone tab) kept running the code it was loaded with, so after the 0.9.7 install it still drew CC / CX pills on every node instead of account icons, although the server sent the icons. The page now reloads once when the collector reports a new version (not while typing or with a reply sheet open; it waits for the next refresh). Pages opened before 0.9.7.5 need one manual reload.
- The tailnet host name is no longer written in tests and docs (example names instead).

## 0.9.7.4 (18 Nov 2026)

- **Model names instead of CC / CX.** Wherever an agent is named (graph node pill and second line, List cards, attention rows, questions, replies, the Quotas page's "agents on this account", the bar panel rows) a small badge says the model in use: `Sol 6.2`, `Astra 6`, `Fable 5.2`, `Opus 5.5`, `Grok 4.7`. Effort and speed suffixes of Cursor ids are dropped (effort keeps its dots). Without a recorded model the badge names the service (`Claude`, `Codex`). Account icons are unchanged. Server: `agent_runtime.compact_label()` and `runtime.badge`.
- **Mini quota dials in the header** instead of the "Now: Cursor 100% Named models Monthly" chip: the first 3 accounts in the Arrange order (pins first, hidden left out) as small dials (outer weekly, inner 5-hour). A nearly-full account not among them takes the last place and is flagged. Tap a dial to open that account's details. Quotas > Settings: "Mini dials in the header" on/off and how many (1-6). The "Now:" line on the Quotas page is gone.
- **Tailnet allow list** (Settings > Tailnet access, on the laptop only), with an on/off switch that is **off by default** (off keeps the list and filters nothing): pick the tailnet devices (from `tailscale status`) or type a name / 100.x IP. When it is on and the list is not empty, any other device gets a bare 403 before the login page; the login still applies. Off or empty = as before. The list and the device list are only shown and changed on the laptop (the phone listener answers 403). Devices that signed in recently are pre-selected, and saving a list that leaves one out asks first. Stored in `state.db`.
- README: two real screenshots (laptop and phone, 18 Nov).

## 0.9.7.3 (17 Nov 2026)

- Codex Sol is now GPT-6.2 Sol (`gpt-6.2-sol`): the default for summaries and the notification gate (still effort low), the `codex-sol` routing agent (`-m gpt-6.2-sol`, effort high) and the model list in Settings. `gpt-6.1-sol` stays in the list so an older saved choice still works. Running agents keep their model. Cursor has no Sol 6.2 yet, so `cursor-sol` stays `gpt-5.6-sol-high`.

## 0.9.7.2 (17 Nov 2026)

- Account icons also for agents not started with `herdr-spawn`, when the account is proven: the session's trace file lies in one account's config folder, or the agent's own process names that folder (`CLAUDE_CONFIG_DIR`, `CODEX_HOME`). The folder map comes only from `routing.toml`. A launch record wins; two proofs that disagree, or a folder two accounts share, give no icon. "Runs on" says the source. The Quotas "agents on this account" count follows.

## 0.9.7.1 (17 Nov 2026)

- 0.9.7 with hotfix 0.9.6.2 (false "Out of quota" on working agents), see below.

## 0.9.7 (17 Nov 2026)

- Account icons: each subscription or account has an icon (a provider mark, a theme colour and an optional 1-2 character badge, so Personal and Work differ at a glance). Shown on graph nodes (in the place of the 0.9.5 service pill), List rows, the agent page ("Runs on"), question rows and cards, the Quotas gauges and details, and the bar panel. An account without an icon shows its text label; an agent without a recorded account shows nothing new.
- Settings > Account icons (also in the Quotas page "Arrange and settings"): pick a mark, a colour, a badge, or upload a small image; Reset to default. Saved on the server.
- The built-in marks are our own simple shapes, bundled locally; colours follow the live theme. Uploaded images: PNG, JPEG or WebP only, checked from the content, at most 60 KB, served only under the dashboard login.
## 0.9.6.2 (17 Nov 2026)

- Hotfix: agents were shown as "Out of quota" while working. The check read the whole screen for words like "usage limit", and agents building quota features write those. Now only the provider's own limit banner in the last 15 lines of the screen counts (Claude Code, Codex, Cursor, API limit errors), a working agent is never out of quota, and stops of agents that are gone end. A team is "blocked on quota" only when its lead or every member still at work is stopped; one stopped member is listed under stuck. One-time cleanup of the false stops and walls at start, logged.

## 0.9.6.1 (17 Nov 2026)

- Image previews in questions also work for `~/Pictures` (default `questions.image_folders` is now `~/Projects` and `~/Pictures`). Same checks: real paths, no symlink escape, image type and size, never private work folders or w4.

## 0.9.6 (17 Nov 2026)

- Questions for Alex are separate, plain questions. Each question or decision in an agent's message is its own row with one line of context, who asked and where the answer goes, its own choice buttons (only the choices the agent offered), a short text box and Send. Each answer goes to the agent on its own (`Answer to "<question>": <answer>`) and is marked answered; the others stay open. Every question quotes the agent's exact words; none is invented. "Show original" gives the full message.
- A report with no question is "Done, not reviewed" with a short summary, not "Waiting on you". When it is unclear: "The agent may want something", with the text and no red label. This only lowers a tier; a direct ask is never lowered.
- Agent text is rendered as markdown (no literal `**`) and is cut only at a word, with More.
- The "Sent to ... at" line is gone. A row shows "You replied "yes" at 11:42" for your own recent reply, or nothing.
- The alert rule reads the whole message, so a question at the end of a long report is found.

## 0.9.5 (17 Nov 2026)

- Questions: when an agent asks to read an image, the question card shows a thumbnail (tap for full size in the Latest visuals viewer), the path (full path on tap) and the dialog's own choices as buttons. Images are served only from the folders in `questions.image_folders` (default `~/Projects`): real paths, no symlink escape, type checked from the content, 20 MB cap. Never from `~/Work`, private-named folders or the protected workspace w4; otherwise "Preview not available" with the reason.
- Quotas page: "Arrange and settings" at the top. Order, pin and hide accounts there; choose which rings and numbers a gauge shows (all accounts or one); own warning levels per account; Large or Compact gauges; optional grouping by provider. Saved on the server. A full account is flagged, never moved. The "no recorded account" line counts each running pane once and names the agents.
- Graph: each node shows its service (CC, CX, AG, CU), and under the name the model and effort (five dots) when there is room. Values come from session traces or the launch record, never a guess; unknown shows nothing. The default "worker" role is hidden.
- Bar panel: all colours from the live theme; accounts and blocks in the saved Arrange order; labels from the contract.
- Clean up: candidates start unticked.
- Theme: text on tinted pills and avatars meets AA contrast.

## 0.9.4 (17 Nov 2026)

- Tokyo Night everywhere: the phone, the desktop window and the bar panel take their colours from the live Omarchy theme (the same terminal palette Herdr shows) and Herdr's status colours, served as `/theme.css` and `view.theme`. A later Omarchy theme change is followed without a reinstall. The page no longer switches to a light set on the device setting; that is why the v0.8.3 look had gone. Built-in Tokyo Night is the only fallback (`herdr_theme.py`).
- Quotas: one overview, one tap from the header. A grid of radial gauges, one per account: outer ring weekly, inner ring 5-hour, % in the middle, reset time and agent count below, in the theme's green, yellow and red. A tap opens the details (agents on it, stopped agents). Stale data says how old it is.
- Arrange: pin, reorder and hide accounts and the main blocks (Needs action, Quotas, Status reports, Latest visuals, Other updates). Saved in settings, the same on phone and desktop. A full or stopped account is flagged but never moves.
- Clean up: a preview of panes that can be closed (done or idle agents with nothing unread, no question, no running job and no uncommitted work; ended sessions; leftover shells), each with its reason. Nothing closes without ticking and Confirm. Workspaces are off until opted in (`cleanup.workspaces`); w4 is always refused; the focused pane is never listed. What was closed is logged. Also `herdr-attention cleanup --preview`.
- Notifications: the row is readable at phone width (headline no longer squeezed by the pill and buttons).
- Includes hotfix 0.9.3.1.

## 0.9.3.1 (17 Nov 2026)

- Hotfix: the dashboard looked offline because each view took 1.5 s of CPU (20 s+ under concurrent polling). The relationship registry was rebuilt once per hook-only subagent; it is now built once per view. Reply recipients, coordinator flags and quota walls no longer rescan every link per agent. A view now takes about 0.17 s on the same data.
- Hotfix: `Store._exec` now fetches rows while it holds the database lock. Fetching later on the shared connection let threads mix results ("not enough values to unpack (expected 8, got 0)").

## 0.9.3 (16 Nov 2026)

- One tier everywhere: the final tier of an event is decided once (`notify_tiers.final_tier`), stored on the event, and used by the phone alert, the Notifications view, List, the status header and the graph. An unread alert that is "Waiting on you" makes its agent "Waiting on you" in the app, with the phone title as headline, Open agent and Read. Read, or viewing the pane, clears it and it does not come back.
- Notifications: an alert whose episode has ended is no longer shown with a live "Waiting on you" pill. It reads "Was waiting on you" and is not listed under Needs action.
- Reporting links never name an agent "Idle" or show a raw ID.

## 0.9.2 (16 Nov 2026)

- Reply directly from attention, question, notification and agent rows, with an inline, focused phone composer and one Send action. Steer retains draft review.
- Show the coordinator recipient with workspace/tab, keep drafts across navigation, and show queued/sent/failed delivery state as it changes.
- Offer actual question choices plus Yes, No and Other…; send a quick answer in one tap or edit with ✎. Verify the current terminal menu before mapping an explicit choice to keys: Codex uses number-only submission (Shift+Left opens queued questions), Claude uses arrows plus Enter, and free-text questions use literal text plus Enter. Never infer Alex's approval from agent text.
- Resolve recipients on the server and queue replies to busy agents without interruption. Preserve all v0.9.1 watched-report and UI/UX changes.

## 0.9.1 (16 Nov 2026)

- Watch closely refreshes coordinator Status reports after new output settles. Skip unchanged output and keep the last report visible with its update time and Out of date marker while a refresh runs.
- Keep watched coordinators’ open questions visible and expanded, including beyond the overview limit. Preserve reply drafts and focus during live refresh.
- Keep report refresh and question wording in the shared UI contract. Watching alone does not change attention state.

## 0.9.0 (16 Nov 2026)

- Phone and app agree more often: when an agent asks the owner to act and its coordinator link is only inferred, the phone alert tier is "Waiting on you". "Please:" is only added to alerts that are yours to act on. A coordinator is never named "Idle" or shown as a raw ID. The batched phone title uses the app words ("1 waiting on you · 1 to look at").
- Attention rows lead with the phone notification title and one muted reason line. On the phone, a live completion says "Clears when you open this pane on the laptop."
- The empty "Questions for you · 0" card is hidden on List, Notifications and the graph. Status report buttons sit outside the workspace card link and name their coordinator when there is more than one.
- Notifications: tier pills, accent only on rows that need action, one "Read" / "Read all" wording from the contract.
- Graph on the phone: View and Fit sit at the bottom in thumb reach, the top chrome is shorter (fold rows and the empty-workspace note moved into the sheets), the status header is only replaced when it changes, nodes that need a look keep their label, cut region titles keep their last word. Quotas has its own icon.
- Reply sheet on the phone: Send stays in view above the keyboard and a tap outside keeps the typed draft.
- Quotas: one server summary drives the header chip, the List row and the Quotas page. Fixed "ago ago", weekday names for far resets, and two accounts both called "Personal".
- Latest visuals: the scan is bounded (time budget, file and folder caps, cache) and no longer freezes the dashboard; the page shows progress, stops waiting after 20 s and says when a scan was partial.
- Settings: the gate log leads with the alert text. "Open in Herdr" is hidden for agents without a pane.
- Bar panel (source only; not installed by a collector-only install): heading "To look at", counts skip folded and absent agents.

## 0.8.16 (16 Nov 2026)

- Fold finished in-process subagents under their parent, keep working children visible, and add Hide subagents to the graph View panel. Exclude folded children from workspace counts and show the finished count on the native parent decoration.
- Read acknowledges the exact quota event, continues past missing group members, and retries once with fresh CSRF after a restart. Live completion rows explain when they clear instead of offering Read. Read episodes survive idle status flicker until new work or a changed question.
- Preserve hook history and fixed positional sidebar ranks.

## 0.8.15 (16 Nov 2026)

- Keep the Herdr Agents tree in fixed positional order across agent state changes. Roots and siblings use Herdr positions, display numbers and stable IDs.
- Preserve coordinator/worker indentation and badges, metadata refreshes and the dashboard's attention-first order.

## 0.8.14 (16 Nov 2026)

- Mark unread notifications read from their list rows, including whole groups.
- Add the same quiet Read control to the overview attention strip and graph attention panel. Keep the row closed, preserve in-flight actions during refresh, and show errors on failure.
- Keep one main row action and a separate secondary Read control with a 44px tap target.

## 0.8.13 (16 Nov 2026)

- Show read items in a visible Read section; count grouped rows.
- Hide skipped, routine and old closed-agent updates. Group pane/task repeats and shared AI messages; open a row to see its updates.
- Use the gate's action tier, keep coordinator requests with the coordinator, and label quota stops correctly. Expired requests remain in history as No longer needed.
- Show AI check failures in technical details and gate account health in Settings and Quotas.
- Preserve installed account routing during upgrades, save new defaults and a private diff, and refuse version downgrades. Add a collector-only install mode.

## 0.8.12 (15 Nov 2026)

- Merge the v0.8.5–v0.8.11 finisher work (graph readability, typing that
  survives refresh, shared notification text, summaries that say what to do,
  acknowledgeable "Out of quota" rows, "Questions for you" routed to
  coordinators, coordinator status reports with steering, "Latest visuals",
  the Quotas tab and the false "Needs you" fix) with the v0.8.4 desktop
  overview window.
- The new routes (Quotas, Questions, status report, visuals, quota refresh and
  acknowledgement) now live in `ui_contract.json`, so the drift check covers
  them.
- "Open in Herdr" in the visual viewer now uses the shared desktop-only focus
  action. The separate visual focus route is gone. It trusted the peer
  address, and Tailscale Serve proxies phone traffic from 127.0.0.1, so a
  tailnet client could focus a laptop pane.

## 0.8.4 (15 Nov 2026)

- Add a loopback desktop overview with CSRF-protected pane focus, isolated
  from phone and tailnet access. One action component serves all web views.
- Add a launch-or-focus overview wrapper, Super+Shift+H installation, local
  notification navigation from the Omarchy plugin, and rollback cleanup.
- Centralize routes, listener defaults, capability names and desktop wording;
  add drift checks and HTTP, browser, focus and installer regression tests.

## 0.7.6 (15 Nov 2026)

- Notification settings now use the same groups as the attention card, with
  presets: *Quiet* (Waiting on you only), *Normal* (the defaults), *Close
  tabs* (tiers 1–5), and *Everything*. Changing a toggle becomes *Custom*.
  Defaults: Waiting on you, failures/stalls, and finished turns without a
  coordinator are on; waiting on a coordinator, coordinator milestones
  (PR opened/merged, plan done, when we can tell), and verbose status
  changes are off.
- Waiting on you always bypasses an AI skip (the gate may only rewrite the
  wording). Other on tiers default to “check first”. Each tier can be sent
  to the phone (ntfy), the desktop (`notify-send`), or the dashboard only.
  Optional quiet hours (off by default) still let Waiting on you through.
  A “Watch closely” pin on an agent notifies all of its tiers.
- Settings (phone and desktop) and the desktop plugin show the presets and
  toggles. The decisions log includes the tier and the send/skip reason.
  Each preset shows an estimated number of AI checks from recent events.
  CLI: `herdr-attention notify config` and `notify preset <name>`.
  Event-driven only; rate limits and fallback to plain text on gate
  failure are unchanged. Tailnet-only stays.

## 0.7.5 (15 Nov 2026)

- The overview attention card now splits items into clear tiers: *Waiting on
  you*, *Waiting on &lt;coordinator&gt;*, *FYI / finished*, and *Might want a
  look*. Each item shows a one-line reason and a short evidence snippet (the
  question or last message). Agents that report to someone else no longer
  count as waiting on you, except for a permission/approval prompt that only
  a human can dismiss on that pane.
- Discrimination uses deterministic signals: Herdr status, `agent.explain`
  rule (permission vs question), last assistant text (a question aimed at
  you), existing summary “ask” fields, and declared or registered
  `reports_to` links. Idle agents that asked you something and stopped now
  appear under *Waiting on you*. The AI notification gate is not used for
  this classification. A summary-only “ask” is ignored once the agent has
  been idle longer than *Parked after* (`ui.parked_after_minutes`, default
  60 minutes), unless the last message itself is a question to you — so an
  old summary does not keep a parked agent in *Waiting on you*.
- Select text in a summary, conversation or terminal view (phone or desktop
  web dashboard) and a small button offers “Ask summary agent” (on-demand
  question, using your summary settings; nothing is sent to the coding
  agent) and “Reply to agent…”. Every agent detail page also has a Reply
  button.
- Replies always go through an explicit confirm step that shows the target
  name, pane and exact text. If the agent is working, the default is to
  queue until it is idle or finished; sending now requires an interrupt
  acknowledgement. Herdr rejects prompts while an agent is at a blocked
  approval screen, so those are queued. Every sent reply is logged on the
  agent timeline. The endpoint needs dashboard login and CSRF; nothing is
  sent automatically. The read-only Herdr client is unchanged.

## 0.7.4 (15 Nov 2026)

- AI summaries default to on-demand only. Automatic/background summaries stay
  off unless you turn them on. Existing installs pick up this default (there
  was no separate auto setting before).
- Settings: a short “AI summaries” panel — Only when I ask (default) / Also
  automatically when an agent finishes / Off — plus account, model and effort
  dropdowns, and a one-line hint that automatic mode uses tokens. Default
  account is Codex · Personal, model Sol (`gpt-6.1-sol`), effort low.
- A clearly labelled ✨ Summarize button on the phone overview and agent
  details, with a loading state.
- `herdr-attention summary config` shows or changes these settings.
- Notification gate, on by default and separate from summaries: when an alert
  would already be sent, a cheap check (same default account/model/effort)
  decides whether it is worth sending and writes a short plain-English message
  with dashboard deep links. If the check fails or times out, the existing
  plain notification is sent. Decisions are listed in Settings. Bursts are
  deduped and rate-limited.
- Phone alerts via ntfy are turned on for this install (tailnet only, no
  Funnel). Settings has an on/off toggle, a “Set up phone notifications”
  button (login required; the topic is only shown after login) and a
  “Send test notification” button. CLI: `herdr-attention ntfy qr` and
  `herdr-attention ntfy test` (also `notify-test`). The topic is never logged.
- Quota strip: work Codex is labelled “Codex · Work” next to “Codex · Personal”.


## 0.7.3 (15 Nov 2026)

- The phone overview mirrors agent quotas from the same Omarchy Agents panel
  records (`~/.local/state/omarchy/agents/usage/`), read-only: plan, usage
  percent per window, reset time in Europe/Amsterdam, and stale/age hints.
  Emails, tokens and local token-count history are not copied into the API.
- Multi-account Claude subscriptions appear as one card per account; providers
  with a plan but no meters (Antigravity on Gemini Enterprise) still show.
- The leftover `claude-personal` collector record is ignored once the official
  Claude record already lists that account.


## 0.7.2 (14 Nov 2026)

- Passive reporting fallback uses first-prompt briefs, explicit coordinator
  references and nearby declared siblings sharing a common brief. Specific,
  unambiguous evidence and confidence are stored separately from declarations.
- Declarations always take precedence; team link promotes inferred links and
  team unlink permanently suppresses inference for that worker identity.
  Inference never closes cycles or uses shared-session attribution.
- Phone graph and desktop relationship lines distinguish inference with dashed,
  fainter strokes, evidence, confidence and a legend. Team list exposes provenance.
- Local `inference check --ignore-declared AGENT` evaluates evidence without
  changing declarations. Trace/brief reads are bounded and watched-session only.
- Every provider launched by herdr-spawn receives a first-prompt pointer to the
  single global rules source at ~/.config/agents/AGENTS.md, including Cursor.

## 0.7.1 (14 Nov 2026)

- Workspaces with no live agent panes collapse into one quiet expandable group
  at the bottom of the phone/list/graph UI and desktop panel. Live graph nodes
  and regions are pruned when their agents leave; history remains accessible.
- Duplicate Herdr session claims no longer overwrite another live pane. The
  known session owner retains its identity; additional panes use stable terminal
  qualifiers across snapshots, pane moves and collector restarts.
- Shared native traces carry an attribution warning and cannot infer spawn
  relationships or generate misleading summaries. Pane-specific output and
  manual reporting declarations remain independent.
- Evidence-backed reporting lines for the existing w4 workers are documented in
  `docs/REPORT-v0.7.1.md`. Registry declarations send no input to agents.

## 0.7.0 (13 Nov 2026)

- `herdr-spawn` alias and extended team launcher: project accounts, role/model/
  effort routing, yolo defaults, sandbox reasons, briefs and visible splits/tabs.
- Child-only launch environment and session-validated parent/self pane tags;
  durable caller-attributed spawn registration, explicit reporting lines only.
- Local lifecycle hook ingest for wrapper-launched Claude/Codex, in-process graph
  children, and a generated coordinator skill referenced per launch.
- Provider quota walls detected from watched output, persistent expiry/clear,
  premium-aware fallback and quota labels on cards; no automatic restarts.
- Four-pane layout cap, provider/role group tabs, named overflow/coordinator tabs,
  generated names when omitted, and early naming/title/task tags on new panes.
- Step 3b: repaired/de-duplicated `--name` overrides, explicit focus opt-in, and
  dry runs showing the chosen name/title/tab/placement.
- Quiet watcher layout hints and `layout check [--workspace]`: unnamed agents,
  crowded/default-label tabs, generic titles, and verified finished-pane cleanup
  suggestions. Exact repair commands are printed; none are run. Report/PR merge
  checks are bounded, cached and read-only, with no model calls or attention alerts.
- Read-only Herdr sidebar tree projection with role badges, foreign-owner
  protection and configurable disable. Credits: Algorant/herdr-agent-tree (MIT).
- Installer packages routing/guidance and alias, runs JS checks, backs up unit;
  Quickshell heartbeat is guarded during component teardown.

## 0.6.0 (7 Nov 2026)

- The graph is the home screen and the whole interface: full-screen canvas,
  tool palette (to look at, workspaces, view, settings, list), one
  contextual inspector/panel (side panel on wide screens, bottom sheet on
  phones), list views kept as the fallback.
- Herdr's own status colours and symbols, verified in Herdr 0.9.1 source and
  resolved from the current Herdr/Omarchy theme at runtime.
- Glitch fixes: pinch zoom no longer snaps back (baseline reset on finger
  changes), cached stable positions, absolute node sizes, in-place SVG
  updates, no automatic refits, canvas-only touch handling, request races.
- Bar panel uses Herdr's status colours.

## 0.5.0 (7 Nov 2026)

- Summary-first cards: AI summaries (Claude Sonnet, effort medium, personal
  account, isolated headless CLI, no tools/hooks) of each new item that
  needs a look, with exact quoted options, labelled claims, git check for
  commit claims, deterministic fallback, refresh / retry / summarise longer,
  audit, rate limit and cache. The only model calls the tool makes.
- Readable terminal output by default; raw lines on request.
- Mark read closes the item and returns to the list (scroll kept); a failure
  keeps it open with the error. Panel rows close on mark read.

## 0.4.0 (7 Nov 2026)

- Declared relationships: append-only registry keyed by session id with
  provenance (registered / manual / observed-spawn), reports_to, roles, team
  and task labels, handover, revocation, cycle warnings, self-link refusal.
- `herdr-attention team ...` CLI and a launch helper (`team start`) that
  starts an agent next to the caller with an explicit account and registers it.
- Graph and inspector show reporting lines, roles and provenance; manual
  editing with audit history (login + CSRF).
- Herdr metadata tokens tested on a throwaway pane (docs/relationships.md).

## 0.3.0 (7 Nov 2026)

- Graph view: one shared space with workspaces as regions, verified Codex
  spawn/fork links (may cross workspaces), hidden/unknown endpoints, legend,
  touch pan/zoom, tap-to-inspect inspector.
- Trace metrics, read passively from Claude/Codex session files (personal and
  private work folders, watched workspaces only): context, session tokens by type,
  age, observed active time, each with source, exactness and freshness.
- On-demand "last N" conversation excerpts from traces, paged, cleaned and
  redacted; terminal lines shown separately.
- Stall fix: busy agents are read from the visible screen; stalls need
  unchanged content over the whole threshold; read errors mean "unknown";
  status alone never flags. The output view no longer fails for busy agents.
- Panel rows show metrics and verified links.

## 0.2.0 (7 Nov 2026)

Workspace-centred oversight instead of a notification feed. See docs/design.md.

- Overview of watched workspaces with state bar and counts (needs you,
  working, done, waiting, parked); attention card only when something needs a look.
- Workspace page: agents by state with task, tab, last activity, last finished;
  arrangement by Herdr tab; relationships shown as unknown.
- Agent page: facts with explicit "unknown", evidence, timeline of status and
  title changes, finished work with titles, recent alerts, same-tab peers,
  recent output on request.
- Workspace chooser and empty state on the phone (labels only for unwatched
  workspaces; switching needs login and CSRF).
- Bar panel redesigned to match: workspace cards that open in place,
  agent rows with facts and timeline.
- Collector: `tab.list` and tab events, activity table, last-activity tracking,
  event titles (database schema 2, migrated in place), `/api/agent`,
  `/api/chooser`, `herdr-attention agent <id>`, `ui.parked_after_minutes`.
- Installer backs up the state database before restarting the service.
- Tests: workspace states/grouping/history, responsive browser tests at
  390/1280 px, live tab and workspace-view checks.

## 0.1.0 (7 Nov 2026)

First version.

- Collector: Herdr event subscription with snapshot on (re)connect, session-id
  identities, one event per state episode, dedupe across restarts using
  `state_change_seq`, rules with evidence and confidence, SQLite history,
  config file plus runtime overrides, local Unix-socket API.
- Desktop plugin `alexvdb.herdr-attention`: bar icon with count, panel with
  needs-you / working / other, evidence, output on request, mark read,
  settings; idle/active reporting through Quickshell `IdleMonitor`.
- Phone page and ntfy sender (off by default): login, CSRF and Origin checks,
  metadata-only alerts gated on laptop idle, summaries, rate limit, retries.
- Install, rollback and uninstall scripts; QML duplicate scan and real-engine
  load check; tests against fixtures and (opt-in) the live Herdr.
