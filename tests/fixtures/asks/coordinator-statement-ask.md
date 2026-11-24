**Status — 23:43**

**1.2.10**
- **SDK metadata sdk#269 is merged** (merge commit `3d4c3f4`), on the independent READY at its exact head with all three hosted checks green.
- **App metadata app#211 has its final head** (`8ca4bd7`), bound to that SDK merge. It is with the independent reviewer; the release worker is running the App's own gates now. I merge when both are done.
- After that: freeze, 8 image verifications, the VM pretest, then the cut on my line.

**Today's prototype work as PRs** (your request; nothing in the current setup is touched)
- Two Codex workers started, on your personal Codex account as you asked:
  - **`port-runtime`**: three draft PRs for the runtime service: the crash fix on its own first, then Start without a warm cache plus the retry fix, then the worker thread cap.
  - **`port-core`**: three draft PRs for the Core: the Start/heartbeat and retry fixes, the Core load fix, and the admin panel's Core side (status, buttons, snapshot, offset).
- Rules they work under:
  - Read-only on your patched Core, your commands, the App builds and every prototype folder.
  - No contact with the staging server or the build server.
  - Fresh clones only, and draft PRs only: nothing merges before the 1.2.10 cut and the demo.
  - No test suites or builds on this laptop between 08:30 and 18:00 tomorrow.
- **Still to start:** the App (builds 20–25) and the deploy tools (the laptop `deploy-*` commands and the scripts on the build server). I am holding these two back for now because the Codex limit stalled the release for two hours tonight when four workers shared it, and the App port needs the same build lock the release is using. I start them when the release worker is past its App build.

**One thing for you to decide tomorrow, not now:** the deploy scripts on the build server are in Sam's workspace. I plan to open that port as a draft PR against his `deploy-tools` repo if the files live there, clearly marked as a proposal. Say if you would rather I only hand him a patch.