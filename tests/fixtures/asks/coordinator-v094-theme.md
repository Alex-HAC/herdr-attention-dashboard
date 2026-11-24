The late contrast fix is ready but not installed: it sits on branch `v094-theme` (head `7b8293e`), and the full suite passes there (714 passed, 11 skipped).

It arrived after Grok Bot had already merged 0.9.4 to `main` and removed the build worktree, so I did not integrate or install it. The branch is up to date with `main`, and its only difference from the installed 0.9.4 is that one commit:

- **What it fixes:** text on tinted pills and avatars now meets AA contrast (tint 14% → 8%; muted and faint text shift slightly).
- **What it adds:** `docs/THEME-v0.9.4.md` (cause, palette table, measured contrast pairs) and refreshed fixture screenshots.

Decision for Alex or Grok Bot: merge `v094-theme` into `main` and install it as a small follow-up (0.9.4.1), or leave it. I recommend merging, since the brief asked for good contrast; it has not had an independent review.

The worktree `~/Projects/herdr-attention-dashboard-uiux2-graph` is clean, so it can be removed after that merge. The installed 0.9.4 is unchanged and healthy.