# STATUS — Decompathon Steward (local only, nothing pushed)

2026-10-20 — M0 core done
- steward/config.py (steward.toml; steward.toml + .github/** always protected), state.py (claims: size/count/overlap
  limits, 10-day warn, 14-day lapse; owners; owner-rule asks/consents), decider.py (pure; escalate > request_changes >
  wait > merge; stale gate results = pending), gates/ (command + decompathon rexcheck gate.py parser: blind mutant gate
  or unreadable output = harness error, never contributor fail), audit.py (hash chain), report.py (PR comment, claims issue),
  CLI (python -m steward ...).
- 31 tests pass. Real run: decompathon jam.py scan gate passes (5 files) -> decide = WAIT on rexcheck (judge.py missing).
- docs/DESIGN.md = approved plan (survey of halo2-decompiled, zeldaret, melee, papermario, pret, botw, Snowboard Kids 2).

2026-10-20 evening — M1, M2, M3 (judge side) done; rebranded to Decompathon Steward (repo E:/decompathon-steward)
- M1: steward/agents (triage haiku / review sonnet / plan opus / mentor sonnet): one structured-output call each
  (messages.parse + pydantic), no tools, untrusted text fenced + escaped, cached system prompt, refusal/error -> None ->
  human. actions.py: model output can only add caution; validate() refuses enqueue_merge without a deterministic merge,
  foreign labels, wrong PR/head. commands.py (/claim /unclaim /consent, actor from the event). roadmap.py + `steward next`.
  Injection corpus tests/injection (7 bodies). evals/injection_eval.py = live version (needs an API key; NOT run yet).
- M2: evals/replay.py over halo2-decompiled (620 PRs, read-only gh graphql): 11 -> 376 of 572 merges agree after
  derived_paths, file units, added-file ownership, maintainers, require_progress. docs/REPLAY_HALO2.md.
- M3: decompathon tools/rexcheck/judge.py + JUDGE FAIL/ERROR parsing here. 55 tests pass.

Next: M4 needs the owner's word: GitHub App + private test repo + Discord bot (actuator, daemon, discord_bot not written).
Live injection eval needs ANTHROPIC_API_KEY. Judge needs a sandbox VM decision + a mutant log + OK to launch the game.

## 2026-10-21: judge gates run by the daemon
- `steward/judge_runner.py` + `python -m steward.daemon ... --judge-workspace <dedicated checkout>`: where=judge command
  gates (game files that never leave the judge box, e.g. THPS64 match/build) run once per PR head from the newest base,
  with only the PR's unit files copied in; protected paths never reach a gate; workspace reset after. Git auth via env
  (installation token never in argv/config/logs). tests/test_judge_runner.py.
- GitHub App "Decompathon Steward" (id 5418450) installed on the private test repo; one live --once pass made the
  claims issue + labels. tools/discord_setup.py lays out the community server (needs the bot token).

## 2026-10-22: AI maintainer (owner decision)
- Owner: escalations ("needs a maintainer") are decided by an Opus-level maintainer, not left for the owner.
- `--ai-maintainer` (bot + daemon): MaintainerRole (claude-opus-5-5, effort high; project RULES/AGENTS/PROVENANCE/
  CONTRIBUTING in the system prompt, PR text + diff fenced as untrusted) runs once per head on escalated, non-draft
  PRs whose gates are done -> approve / request_changes / defer_to_owner, posted in the PR report and audited.
- An approval merges only if actions.maintainer_merge_ok(): every gate passed, every rule is merge or escalate (no
  waiting/failing rule), no hostile content, no `maintainer-hold` label (owner's per-PR veto); validate() re-checks.
  The role is told to defer licence/legal, leaked material, rule-document changes, disputes, low confidence.
- tests/test_agents.py: 6 tests (approve merges green escalations only; gate/hold/hostile block a forged merge;
  request_changes/defer; fencing). 69 pass.
