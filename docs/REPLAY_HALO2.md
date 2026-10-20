# Replay eval: halo2-decompiled (2026-10-20)

`python evals/replay.py --config examples/halo2/steward.toml` — read-only (`gh api graphql`, 620 PRs),
gates assumed to pass (history has no gate output), so this tests the **rule layer** only.
Full per-PR table: `work/replay_halo2.md` (gitignored).

## Result after tuning

| maintainer vs steward | merge | wait | request_changes | escalate |
|---|---|---|---|---|
| merged (572) | 376 | 62 | 68 | 66 |
| closed (24)  | 6 | 8 | 1 | 9 |
| open (24)    | 2 | 19 | 0 | 3 |

First run agreed on 11 of 572 merges. What the replay taught:
1. Decomp PRs update the function table (`config/functions.csv`) as part of the PR. It is the
   verifier's denominator, so it can't be a free-for-all, but it isn't policy either → new
   `derived_paths`: allowed in PRs, the oracle gate must regenerate and compare it.
2. With file units, new files are normal → `unknown-unit` only applies to address units.
3. File-level ownership is too coarse: files the PR *adds* have no owner; maintainers can be
   exempted per project (`owner_rule_exempt_maintainers`, halo2's maintainer merges by hand).
4. "Closed" ≠ rejected: #28 and #71 landed through squash/batch merges ("with you as the author").
   halo2 squash-merges and keeps authorship in the commit, not merge commits.
5. #424 was closed because it "gains no match, and takes several functions further from retail" →
   new `require_progress` rule (a gate reporting per-unit gains must show at least one).

## Remaining disagreements (expected)
- too-big (69 merged): halo2 merges up to ~80 files; Decompathon's own rule is 20 functions.
- owner-rule (87 merged): file-level owners still over-trigger; per-function ownership needs the
  gate to report which functions changed (judge side, M3).
- first-timer (15 merged): by design a human does a newcomer's first merge.
- no-units / protected (tooling, docs, CI PRs): by design these go to a human.
