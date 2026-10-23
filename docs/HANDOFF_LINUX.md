# Handoff: THPS64 decomp + Decompathon Steward → Linux (2026-10-23 evening)

Read this first on Linux. A copy sits at `E:/thps64/work/handoff/HANDOFF_LINUX.md`, together with the queue lists
(`lists/`) and helper scripts (`scripts/`) that were in the Windows session's scratchpad.

## 1. Where things live (Windows paths → mount them on Linux)
| What | Windows path | Notes |
|---|---|---|
| THPS64 repo (public) | `E:/thps64` | github.com/andrewnakas/thps64, branch `master` tracks `origin/main` |
| Steward (local git, **no remote**) | `E:/decompathon-steward` | latest commit 33d612d; copy or clone from the mounted drive |
| Judge workspace | `E:/thps64-judge` | dedicated clone; never edit by hand; holds `work/main.bin` (ROM-derived) |
| Toolchain (Windows .exe) | `E:/thps64-tools` | cc1 / gas built with zig for Windows; **rebuild for Linux** (step 3) |
| Tools worktrees | `E:/thps64-tools-wt` (branch `split-pad` = PR #84), `E:/thps64-tools-wt2` | |
| PR worktree | `E:/thps64-pr` | used by `scripts/open_prs2.sh` |
| Permuter setup | `E:/thps64-tools/perm` | README inside; Windows-specific shims (objdump stand-in, compile.py) |
| ROM (owner's copy, never commit) | `E:/Tony Hawk's Pro Skater (U) (V1.1) [!].z64` | |
| Steward credentials + state | `C:/Users/andre/.decompathon/` | GitHub App key, Discord token, `data/andrewnakas_thps64/daemon.json`; **secrets: copy, never commit or print** |

Copy `C:/Users/andre/.decompathon/` to `~/.decompathon/` on Linux (it is on the Windows C: partition; mount it read-only
or copy from it). The `daemon.json` memo keeps judge results, so the Linux Steward does not re-judge everything.

**Only one Steward may run at a time.** The Windows one stops when the PC boots into Linux; it does not auto-start
on Windows (no scheduled task), so nothing double-acts. If you go back to Windows, stop the Linux one first.

## 2. Steward on Linux
```sh
cp -r /mnt/e/decompathon-steward ~/decompathon-steward     # or git clone /mnt/e/decompathon-steward
cd ~/decompathon-steward && python3 -m venv .venv && . .venv/bin/activate
pip install discord.py pytest pyelftools rabbitizer splat64 spimdisasm toml pycparser
python -m pytest -q            # 75 tests must pass
claude --version               # the AI backend is the claude CLI (backend = "claude-code"): log in as the owner
```
Run it (judge workspace must exist first, step 4):
```sh
cp tools/linux/decompathon-steward.service ~/.config/systemd/user/   # edit paths if not under $HOME
systemctl --user daemon-reload && systemctl --user enable --now decompathon-steward
loginctl enable-linger $USER
tail -f ~/.decompathon/bot.log        # "[watchdog ...] bot started", "discord: logged in", "#N: judge gate ..."
```
The unit sets `PATH` (venv first, so the judge's `python tools/gate.py` finds pyelftools/rabbitizer/splat) and
`THPS_TOOLS` / `THPS_CC1` / `ZIG` for the Linux toolchain. `steward.watchdog` restarts a bot that exits or stops
beating its heartbeat (~82 min, > the 3600 s gate timeout); gates kill their whole process tree on timeout.

Steward features added this session (all in the local repo): AI maintainer (Opus) on escalated PRs, watchdog,
`identities` policy (andrewnakas = nakasmakes), memo saved after every PR, re-review when a PR's title/body
changes, `/status` hex-size crash fix, a failing command reply never aborts a tick.

## 3. Toolchain on Linux (the judge needs it)
`toolchain/build.sh` builds **Windows** binaries (zig `cc32` wrapper targeting 32-bit MinGW, Windows patches,
compat shims). On Linux build the same sources natively for a 32-bit Linux host instead:
- sources/revisions: see `toolchain/build.sh` (pmret/gcc-papermario `GCC281_REV` = the project compiler,
  decompals/mips-binutils-2.6 `BU_REV`; GCC 2.7.2 is reference only).
- skip `*-windows.patch` and `compat/`; use `CC="zig cc -target x86-linux-musl"` (or `gcc -m32`).
- **run `python toolchain/gas_patch.py gas/targ-cpu.c gas/as.h` after configure**: it adds the THPS rules
  (`THPS_AS_NO_LOAD_SWAP`, `NO_STORE_SWAP`, `COP_INTERLOCKS`, `NO_MUL_SWAP`, memmove fix). `match.py` refuses a gas
  without all four markers.
- install as `$THPS_TOOLS/mips-as.exe` and `$THPS_CC1` (match.py appends `.exe` to the gas name; keep it).
- `ZIG` must be a Linux zig (match.py uses `zig cc -E` as the preprocessor).
- **Prove it before the Steward judges anything:** in a checkout of current main, `python tools/split.py`,
  `python tools/gate.py --all` (every file MATCH) and `python tools/build.py` (`OK: build/main.bin is identical`).
  A toolchain that differs by one instruction would silently reject or accept the wrong PRs.

## 4. Judge workspace on Linux
```sh
git clone https://github.com/andrewnakas/thps64 ~/thps64-judge && cd ~/thps64-judge
python tools/build_main.py "/mnt/e/Tony Hawk's Pro Skater (U) (V1.1) [!].z64"   # writes work/main.bin etc.
python tools/split.py && python tools/build.py                                  # must be byte-identical
```
Never edit it by hand afterwards; the Steward resets it per PR.

## 5. PR state at handoff (43 PRs merged on 2026-10-23; main = 841 of 2457 functions matched, ~34%)
Merged today incl. tools: #77 splitter tails, #79 jump-table alignment, #80 per-file cflags (owner), #82 no
multiply in delay slots, #85 identities (owner), #95 local-jump check; batches #75 #76 #78 #83 #94; Codex #53 #87 #88.

Open (Steward works newest first; tools merges force a cold re-judge of everything, 20-30 min each):
| PR | What | State / next |
|---|---|---|
| #96 | 12 post-split functions (batch 47) | judging |
| #91 #92 #93 | cflags batches 43-45 (20/19/20) | waiting to be judged |
| #90 | 3 functions (batch 42) | description fixed; re-review pending |
| #81 | 12 functions (batch 40) | queued |
| #84 | pad_ units (94 inter-file filler words) | rebased onto main, description answers the review; maintainer |
| #50 #48 | Codex batches (fixed today) | queued |
| #86 #58 #51 #35 #38 #21 #26 | Codex PRs, hacks removed today (hard-coded addresses → symbols/literals, no register pins/asm) | re-review on new heads/bodies |

## 6. Matching state and what to do next
- **Per-file flags are the big lever.** Most functions given up at -O1 match with `/* cflags: -O2 -fno-schedule-insns
  -fno-schedule-insns2 */` (+ `-Wa,-no-m4100` for mflo->mult hazard nops, `-ffast-math` for bare sqrt.s). Retry
  every old give-up under those first (yield ~50%). Remaining list: `lists/cf_rest3.txt` (rows not yet done),
  `lists/givenup.txt`.
- **After #84 merges:** re-split, then retry `lists/junktail.txt` + `lists/after84.txt` (func_80041E40 already
  matches once its pad is split off).
- **Post-#77 functions above 0x80070000** (Codex lane; owner = same person now): `lists/wave_after77.txt`.
- **Requeue:** `lists/requeue.txt` (func_80086E18 with its tail; func_80034AA4 blocked by gas, see below).
- **gas gaps still open** (`lists/gas_todo.txt`, `lists/hazardgas.txt`): with `-no-m4100` gas pads an mflo before a
  gcc-filled jal/branch delay slot and a mult after a jal (the game does not); gas places a label after the
  c.lt.s hazard nop (func_80034AA4, func_8002A588). A gas_patch rule could unblock ~8 functions.
- **Batch PR workflow:** `scripts/open_prs2.sh FIRST_BATCH LISTFILE` (LF line endings!; uses worktree
  E:/thps64-pr; `BODY=scripts/pr_body_cf.md` for cflags batches). Check each file with
  `gate.check_one` first. Agents work in `work/q/<LANE>/` with their own zig caches; at most 2-3 at once.
- Memory: the machine runs out of RAM with several builds + agents; Claude Code reaps background shells then.
  Long jobs: launch detached (on Linux `setsid nohup ... &`), poll the log.

## 7. Listings / trackers
ByteMatch live (bytematch.dev/andrewnakas/thps64, report from `.github/workflows/progress-report.yml`).
decompilati.onl live (decompilati.onl/projects/tony-hawk-s-pro-skater); its GitHub App is not installed (owner's
choice) and the API key is in `~/.decompathon/decompilati_onl_thps64.key` (secret). awesome-game-decompilations,
Awesome-REA and Decompedia PRs still need a classic gh token (`gh auth login --web`).
