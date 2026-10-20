# Steward

A maintainer + orchestrator for decompilation projects, built around Claude.

- **Gates decide, models explain.** A pure decision function (`steward/decider.py`) merges a PR only when every
  required gate passed on the current head and no rule blocks it (claims, owner rule, protected paths,
  first-time contributor, checklist). Claude agents triage, explain failures, plan the roadmap and help newcomers,
  but their output is never an input to the merge decision.
- **Rules copied from real decomps** (halo2-decompiled, zeldaret, melee, ...): oracle decides, zero-loss,
  claims with lapse, 7-day owner rule, provenance, merge commits keep authorship.
- **Everything is logged** in a hash-chained `audit.jsonl`.
- **Any game:** one `steward.toml` per project (see `examples/recompathon/steward.toml`).

Status: M0 (deterministic core, offline). Nothing is published. See `STATUS.md` and `docs/DESIGN.md`.

```
python -m pytest -q
python -m steward check-config examples/recompathon/steward.toml
python -m steward decide --config steward.toml --pr pr.json --gates gates.json --state state.json --audit audit.jsonl --comment
```
