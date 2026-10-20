# STATUS — Steward (local only, nothing pushed)

2026-10-20 — M0 core done
- steward/config.py (steward.toml; steward.toml + .github/** always protected), state.py (claims: size/count/overlap
  limits, 10-day warn, 14-day lapse; owners; owner-rule asks/consents), decider.py (pure; escalate > request_changes >
  wait > merge; stale gate results = pending), gates/ (command + recompathon rexcheck gate.py parser: blind mutant gate
  or unreadable output = harness error, never contributor fail), audit.py (hash chain), report.py (PR comment, claims issue),
  CLI (python -m steward ...).
- 31 tests pass. Real run: recompathon jam.py scan gate passes (5 files) -> decide = WAIT on rexcheck (judge.py missing).
- docs/DESIGN.md = approved plan (survey of halo2-decompiled, zeldaret, melee, papermario, pret, botw, Snowboard Kids 2).

Next (M1): agents/ (Agent SDK, dontAsk + read-only tools, JSON schemas) triage/reviewer/planner on fixture PRs,
injection corpus. Then M2 replay over halo2-decompiled history (read-only gh), M3 recompathon judge.py.
Owner's word needed before M4 (GitHub App, test repo, Discord bot) and M5 (pilot; ask mchughalex first).
