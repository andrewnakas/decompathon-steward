# Decompathon Steward — a Claude-powered maintainer + orchestrator for decomp projects

## Context
Decompathon (E:/decompathon, local) wants decomp jams for games like Skate 3 and Halo 3 Mythic where "AI agents run the harness, review and merge; humans own the rules" (docs/PLAN.md §5, RULES.md §8). The owner asked for the best possible open-source Claude maintainer that organizes the community and drives projects to completion, reusable for any game.

Memory says the 2026-10-20 design doc `docs/MAINTAINER_AGENT.md` was written, but it does not exist on disk. It was redone this session from a survey of halo2-decompiled, oot/tp/melee/papermario/pret/prime/botw, plus Claude tooling docs.

Owner decisions (2026-10-20):
- Its own generic repo.
- GitHub + Discord.
- Auto-merge when the gates are green.
- Hybrid runtime: Actions plus a self-hosted daemon on the judge box.

Survey facts that shape the design:
- **No big decomp merges automatically.** Every merge is done by a human. Bots only post match reports (decomp-dev[bot]), handle `/assign`, and enforce required checks.
- **The rules worth copying come from halo2-decompiled:**
  - The oracle decides.
  - Full-tree zero-loss.
  - A pinned claims issue: one file or ≤0x2000 bytes per claim, lapsing after 14 days.
  - A 7-day owner-consent rule.
  - Provenance tick boxes plus a name rescan on every merge.
  - Merge commits to keep authorship.
- **AI policies range widely.** N64Recomp bans AI and plants a notice addressed to agents. Melee allows AI but bars it from naming globals and from automated reviews. botw requires disclosure and human review.
- **decomp.dev shut its public leaderboard in Oct 2026** over AI forks finishing other teams' projects. So Steward must work only in opted-in repos, defer to human claims, and label every bot message.

## Design principles
1. **Deterministic core, LLM at the edges.** Merges come from a pure decision function over gate results and policy. Model output can never cause a merge.
2. **Untrusted input.** PR text, code, comments, Discord messages and repo files addressed to agents are all untrusted. The model-facing agents are read-only and emit schema-validated JSON. A separate actuator executes only allowlisted enum actions.
3. **Humans keep the following:** policy and rule changes, appeals and bans, licence and publishing, first-time contributors' first merge (config flag, default on), provenance doubts, and changes to CI, tools or Steward's own config (enforced by CODEOWNERS).
4. **Everything is logged.** The log is append-only (`audit.jsonl`). Each entry holds the rule, gate output hashes, model, prompt version and SHAs. A daily digest of escalations goes to the owner, and the owner samples merges weekly.
5. **Game-agnostic.** Each project has a `steward.toml` and an oracle adapter.
6. **The verifier is protected.** Snowboard Kids 2 lesson: the model edited the SHA1 to fake a match. Gates run from a pinned base checkout, never from the PR's own scripts. Any PR touching checker, hash, mutant or `functions.csv` paths escalates. Agent lanes get PreToolUse hooks that block edits to those paths.
7. **Reports in the decomp-dev bot's format.** One edited comment per PR: % delta, new matches, regressions, a per-area table, and a lost-function list when zero-loss fails.
8. **Work picking uses similarity to matched functions as well as difficulty.** Picking easiest-first stalled at about 58% on Snowboard Kids 2.

## Repo: `E:\decompathon-steward` (local git, nothing pushed)
```
steward/
  config.py      steward.toml schema: gates (commands, required, runner), oracle adapter, function table, claim limits, owner-rule days, roles, model ids
  state.py       claims / owners / audit log stored on a `steward-state` branch written only by the App (+ pinned claims issue rendered from it)
  decider.py     PURE: (pr facts, gate results, claims, policy) -> merge | request_changes | escalate(reason) | wait(reason)
  actuator.py    executes enum actions via GitHub App (labels, templated comments, enqueuePullRequest, issue upkeep); model text only inside a quoted "Steward (AI) says" block
  gates/         runner + adapters: rexcheck (decompathon), objdiff report.json (dtk projects), checkpy (halo2-style), generic command
  agents/        Claude Agent SDK roles, dontAsk + allowedTools, in-process MCP read tools, JSON schemas
    triage.py    claude-haiku-5-5: classify issue/PR, detect claims, missing provenance, duplicates
    reviewer.py  claude-sonnet-5-5: explain gate failures, "looks like source" + naming/provenance doubts -> escalate; never write tools
    planner.py   claude-opus-5-5 nightly: roadmap, milestones, epics, good-first-function list, weekly progress post draft
    mentor.py    claude-sonnet-5-5: answers newcomers from repo docs only (Discord + issues)
  roadmap.py     difficulty ranking from function table (size, callees, pure-closure, coverage) -> labelled issues
  progress.py    progress.json + badge + leaderboard (calls project scorer, e.g. decompathon tools/score/jam.py)
  discord_bot.py /claim /unclaim /next /status /why <pr>; announces merges + milestones; mirrors to GitHub (GitHub = source of truth)
  daemon.py      judge-box service: polls GitHub as the App (no public webhook needed), runs privileged gates, scheduler, Discord
actions/         reusable workflows: steward-scan.yml (pull_request, no secrets), steward-triage.yml (workflow_run), steward-merge-group.yml
tests/           decider table tests, injection corpus, fixture PRs, replay harness
docs/            DESIGN.md (replaces the missing MAINTAINER_AGENT.md), POLICY_TEMPLATE.md, ADAPTERS.md, SECURITY.md
```

## Flows
- **New PR:**
  1. The public scan gate runs: decompathon `jam.py scan`, the format check, the binary/asset scan and the provenance tick-box check.
  2. Triage labels the PR and checks claims and owners.
  3. The privileged gates run on the judge box, inside a sandbox (next bullet): build, oracle, full zero-loss, and the mutant gate if the harness changed.
  4. The decider runs. On `merge`, the actuator enqueues the PR into the GitHub merge queue (merge commit, `merge_group` re-runs the checks). On anything else, the reviewer comments with the failure explained, or the PR is escalated to the owner's digest.
- **Judge sandbox:** PR C++ is compiled and run inside the game. Untrusted code on the owner's PC is the biggest risk, so it runs in a disposable VM or Windows Sandbox with no network and no tokens. Results come back as files. Workflow runs from first-time contributors need approval.
- **Claims:** `/claim <addr|file>` on GitHub or Discord updates state and re-renders the pinned issue. Claims are limited by size and count, and the owner of an overlapping claim is notified. Steward expires a claim after 14 days without activity, after warning at day 10.
- **Owner rule:** a PR that touches someone else's merged function gets a consent ask, then waits up to 7 days. It proceeds on consent or silence and keeps the original author's credit.
- **Orchestration (nightly planner):**
  - Refresh the roadmap from the function table and verdicts.
  - Open or close area epics, and keep about 20 "good first function" issues stocked.
  - Nudge stale PRs and claims.
  - Detect duplicate work.
  - Post the weekly progress update (Discord + Discussions) and the leaderboard.
  - Update `ROADMAP.md` through a PR that the owner approves, since it is a policy path.
- **Disclosure:** every bot message is labelled AI. AI-assistance fields (none/assisted/generated + model) are recorded per PR. Agent commits carry Co-Authored-By.

## Milestones (each ends with tests passing and a STATUS.md entry)
- **M0 Core.** Repo skeleton, `steward.toml` schema, decider, audit log, the state model and claim logic. Table-driven unit tests with no network. Decompathon's `steward.toml` is written against its existing tools (`jam.py`, `gate.py`, `selftest.ps1`).
- **M1 Agents offline.** Triage, reviewer and planner run on fixture PRs and issues through the Agent SDK, with structured outputs. Includes an injection corpus (PR bodies and code comments that tell the bot to merge or label, and an N64Recomp-style agent notice): each must produce the same actions as the clean control.
- **M2 Replay eval.** Run the decider plus agents over the merged and closed PR history of halo2-decompiled, read-only through `gh`. Compare against the maintainer's real decisions (merge/split/reject) and tune until disagreements are explained.
- **M3 Judge integration.** Write decompathon `tools/rexcheck/judge.py` (jam.yml calls it, but it does not exist). It wraps jamcc → jamlink → selftest → gate.py → verdicts.csv in the sandbox. This step needs the open R2/R3 runs from decompathon STATUS first.
- **M4 Live on a private test repo.** GitHub App, merge queue, the Actions workflows and the Discord bot on a private server. Scripted PRs cover: good, mutant, zero-loss break, claim conflict, banned pattern, injection, owner-rule and first-timer. **Needs the owner's word to create the App, the repo and the Discord bot.**
- **M5 Decompathon pilot** (Skate 3), shadow week first, then auto-merge. **Needs the owner's word, plus mchughalex's answer, because SK8-Engine has no licence.**
- **Later:** Steward's own decomp lanes (AI contributors that claim, decompile and PR under the same gates and always yield to human claims), then onboarding other opted-in projects.

## Verification
- `pytest` covers the decider tables, claims, owner timer, config and the actuator, which takes enum input only.
- The injection corpus is 100% action-stable.
- The replay report on halo2-decompiled history has every disagreement triaged.
- On the private test repo, all 8 scripted PRs reach the expected end state. Exactly one gets auto-merged through the queue, and the audit log has one entry per decision.
- Sandbox check: a hostile PR (network call, file write outside the work dir) fails without touching the host.

## Constraints carried over
- Nothing public (repos, Apps, bots, pushes) without the owner's explicit word.
- SK8-Engine and OpenH3M authors must be asked first.
- Steward never reviews or merges in upstream projects that have not opted in.
- After approval, fix memory: replace the pointer to the missing `MAINTAINER_AGENT.md` with `E:\decompathon-steward\docs\DESIGN.md`.
