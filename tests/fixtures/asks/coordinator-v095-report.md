v0.9.5 is installed and running: the collector reports 0.9.5 and the bar panel is updated from 0.8.12 to 0.9.5. The full suite on the release commit passed (806 passed, 11 skipped) and the pre-release review found 0 blockers.

**What is installed**
- **Image preview in questions:** thumbnail, readable path and the dialog's own choices as buttons. Images are served only from `~/Projects` and never from `~/Work`, the protected workspace or private-named folders.
- **Quota settings:** an "Arrange and settings" button at the top of the Quotas page covers order/pin/hide, which rings and numbers show, own warning levels per account, Large/Compact, and group by provider.
- **Graph nodes:** a service pill on each node (CC, CX, AG, CU), with model and effort dots under the name. Values come from session traces or the launch record, never a guess.
- **Leftovers:** Clean up candidates start unticked, the "no recorded account" line now counts real agents (2 live, was 409), and the contrast fix is in.
- **Bar panel:** theme colours and the saved Arrange order.

**Deploy**
- Only `herdr-attention.service` was restarted, and `routing.toml` is byte-identical.
- For the panel I copied only the panel files; the Omarchy shell reloaded its plugin panels by itself, with no error for ours in the log.
- The backup, including the old panel, is in `~/.local/share/herdr-attention/backup/pre-0.9.5-20261117-113901`.

**What to know**
- **Review coverage:** the graph node part and the private-named-folder rule were merged after the review, so only the test suite covers them.
- **Bar panel not seen on screen:** it was checked in the QML engine offscreen and in the shell log only. During the reload the shell logged "another handler is registered" for several panels, including other plugins; if a bar shortcut misbehaves, `omarchy restart shell` clears it.
- **Screenshots outside `~/Projects`** (for example `~/Pictures`) show "Preview not available" until that folder is added to `questions.image_folders`; there is no Settings-page control for it yet.

**Decision for Alex:** the own warning levels drive the flag, the header chip and the List row, but not a phone alert, because 0.9.4 had no "nearly full" alert. Adding one means more phone notifications, so I left it out; say if you want it.

For Grok Bot: everything is on `v095-build` (head `10b1348`) in `~/Projects/herdr-attention-dashboard-uiux2-build`; all `uiux2*` worktrees are clean and every worker branch is merged into it. `uiux-review-20261116` holds the brief and review documents only. Nothing was pushed. The report is `docs/REPORT-v0.9.5.md`.