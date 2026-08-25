# agent-ticket-orchestrator

Pure skill + agents plugin — no binary, no MCP server. The **upper** layer of the Seretos ticket pipeline: it selects, bundles, clarifies, dispatches, moves board columns and merges. The **lower** layer, `agent-autonomous-developer`, turns one work package into one CI-green PR and knows nothing about this plugin. Two skills (`gatekeeper`, `run`), three subagents (`bundler`, `clarifier`, `triage`). README.md covers *what* it does and how to install; the skills and agents document their own rules. This file records only what you cannot reconstruct from any single file.

## Installed per project; the project id comes from the repo

This plugin is enabled in each project's own `.claude/settings.json`, next to `agent-autonomous-developer` and `agent-project-issues`, and is invoked from that project's main checkout. Both skills resolve the `project_id` from `git remote get-url origin` → `owner/repo` → the `list_projects` entry with that `path`; an explicit `project_id=` argument overrides. There is deliberately **no ecosystem-level orchestration here** (release chains, dependency bumps across repos, marketplace publishing) — that is a later, separate layer that would call these skills per project. Keep this plugin ignorant of other projects.

## Contracts an agent won't infer from the tree

### The lower plugin's entry point (the only thing this plugin knows about it)

the `run` skill starts, from its own main turn, with cwd = the prepared worktree:

```
claude -p "/agent-autonomous-developer:process-ticket package=<id> project_id=<project> worktree_path=<abs> base_branch=<default>" \
  --permission-mode bypassPermissions --disallowedTools AskUserQuestion \
  --output-format stream-json --verbose --model <model> > <rundir>/stream.jsonl 2> <rundir>/stderr.txt
```

A *work package* is a single ticket id or an epic id (= all its `list_hierarchy` children, one branch, one PR with one `Closes #<n>` per child). The lower plugin never creates worktrees or branches, never touches columns, never selects tickets, never asks a human. This plugin never writes code. Changing either side's half of that split is a contract change for both repositories.

### The comment-event contract (lower writes, this plugin reads)

The lower plugin posts comments on the **package ticket** (the epic when the package is an epic), each starting with a machine block, parsed as dumb `key: value` lines (empty = unknown, unknown keys ignored):

```
<!-- adev:event v1
event: <name>   package: <id>   attempt: <n>
rounds: plan-critic=1/3(1f,0i) test-critic=0/3 review=2/3(2f,0i) ci=1/3(0f,1i)
pr: <number or empty>   ci_run: <id or empty>
-->
```

`rounds`: `<gate>=<used>/<cap>(<f>f,<i>i)` — `f` rounds ended with real findings, `i` rounds were lost to infrastructure; both count toward the cap. As of the lower plugin's Phase R (rebase-and-repair), `rounds` also carries a `rebase=<u>/3(<f>f,<i>i)` sub-field, `0/3` on a session that never entered Phase R; `run` reads `rounds` as opaque prose and does not need to parse it. Events, exhaustive: `started`, `plan-committed`, `plan-critic-verdict`, `tests-red`, `test-critic-verdict`, `tests-green` (local pre-filter only — never success), `review-verdict`, `pr-opened` (`pr` filled), `ci-red` (`ci_run` filled), and the three **terminal** ones: `ci-green` (the only success signal), `blocked` (needs a human decision; text = question, options, recommendation, what was checked), `failed` (terminal non-decision failure; text distinguishes findings vs infra). A process that ended without a terminal event counts as `failed`. The vocabulary is unchanged by Phase R — see the lower plugin's `AGENTS.md`, "Phase 0 orients on the branch instead of taking a parameter".

Reactions (`skills/run/SKILL.md` implements exactly this): `ci-green` → `merge_pr` (verify `merged: true`), → Done, remove worktree · merge fails on a **conflict** (`mergeable_state: dirty`/`behind` or the GitLab equivalent) → one rebase-and-retry dispatch, same script, `attempt+1`; merges after that → Done, still conflicted → Question · merge fails on branch protection / permission / an unresolved state → unchanged: Review, `merge-failed`, human decides · `blocked` → triaged by a read-only subagent before it costs anything (answerable → answer posted, immediate re-dispatch; not answerable → Question right away) · `failed`/no terminal event → checked directly against the PR's actual CI/mergeability state first (a package that only died mid-CI-wait is not `failed`); only if that check does not resolve it, one fresh re-dispatch, still failed → Question with the failure summary. These are **independent** retry budgets (one `failed` retry, one rebase retry, one triage-driven re-dispatch), not a shared counter — see `skills/run/SKILL.md`, "Merge outcomes are classified, and a conflict is a retry". The run succeeds only if every package reached Done; partial is reported as partial; no "not included" list in any PR, ever.

`run` also runs a **Step 0 pre-flight**, before enumerating Todo: any open `pkg/*` PR left over from an earlier run gets the same `ci-green` reaction if that is its latest event, so a package finished by an earlier night's run does not sit unmerged forever, blocking every worktree cut after it. And **Step 2a gates on the previous package** — it verifies with `list_prs` that the previous package actually cleared before cutting the next worktree, rather than assuming the "never two open PRs" guarantee held. Neither check aborts the run when it finds a problem; both record it and let the merge-outcome classification absorb the consequence. This closes the gap a real incident found (2026-08-24, `agent-web-tester`): a stale `Question` card kept a `ci-green` package unmerged, the next package's worktree was cut from the stale base anyway, and the eventual conflict had no path back except a human. Full account in `skills/run/SKILL.md`, "Merge outcomes are classified, and a conflict is a retry".

`run` calls `get_pr` and `list_prs` for the merge-outcome classification **and** for the pre-retry CI check (see below). Both are read-only and need no permission beyond the project's existing read access; `merge_pr` still needs `pulls.merge`, unchanged.

### Waiting on CI is not a decision, and neither is a `blocked` event nobody tried to answer

Two follow-on fixes to the same escalation philosophy, both in `skills/run/SKILL.md` at their
point of use (`agent-ticket-orchestrator#7`, `#8`):

- **The pre-retry CI check.** A process that ends `failed` or on a non-terminal event can simply
  have died while its PR's gating CI was still running, or even after it had already finished
  green — two independent incidents (`agent-worktree` #165, `agent-project-issues` #268) escalated
  to Question on exactly that, one of them with both gating runs already green *before* the session
  exited. `run` now checks `get_pr`/`list_pipeline_runs` directly before treating `failed`/no
  terminal event as retry-worthy, and resolves the package (or waits one bounded extra look) instead
  of spending the retry on a package that was never actually failed.
- **`blocked` events are triaged, not always retried.** The old contract set every `blocked`
  package aside and gave it exactly one full retry session at the very end of the run, unconditional
  on whether a retry could plausibly change anything. A recorded incident (`agent-project-issues`
  package #265) showed the `blocked` event's own text predicting the retry would be wasted, and a
  human answered it from the ticket in seconds once they happened to look. The new `triage`
  subagent (read-only, same escalation test the `clarifier` applies before a run even starts) reads
  the question against ticket, code and comments: `ANSWERED` → the answer is posted as a comment
  and the package is re-dispatched immediately (no waiting for other packages first); `ESCALATE` →
  straight to Question, no retry spent. At most one triage attempt per package per run.

### Why state lives in the ticket, not in the return value

A headless `claude -p` returns "process ended" plus prose. Reconstructing state from that prose — or from a subagent's reply — is how silent report loss happened in the lower plugin's fleet era (#60, #88). So the **ticket is the state store**: the process exit code is a courtesy, and `run` re-reads `list_comments(order="desc")` for the latest `adev:event` before every decision. A crash anywhere in the chain loses nothing that matters; re-running `run` picks the card up from its column.

### Why the package session is a CLI process started by the skill itself — not an `Agent` subagent, not a wrapper

An `Agent` subagent inherits the **parent session's** MCP connections — this plugin's Serena project, this session's tool set — not the target project's. The lower plugin needs the target worktree's own `CLAUDE.md`, `.serena`, `.claude/settings.json`, plugin set and MCPs, which only a fresh `claude` process with cwd = worktree gets. And there is deliberately **no wrapper subagent** around that process either: a task-notification for a backgrounded Bash command reaches the **main** session, while a subagent that ends its turn "to wait" is terminated, not suspended (lower plugin #83/#88) — a wrapper that has to stay in-turn for hours is the exact shape that went silent before. So `run` starts the process with `Bash(run_in_background: true)` in its own turn — via `scripts/start-package-session.sh`, which owns the launch lock, the stream files and the exit marker so the skill never reproduces those mechanics from prose — and is woken by the harness when it ends. The orchestrator never reads `stream.jsonl`; state comes from the ticket. Retry = a fresh start with `attempt+1`, never a resume.

### No dollar budget on the package session; the model is pinned instead

`--max-budget-usd 15` was removed, and the two facts behind that removal are the kind that get re-litigated by anyone who sees an unbounded `claude -p` and reaches for a cap.

**The dollar figure is not the money.** `total_cost_usd` and `--max-budget-usd` are API *list price*, computed from token counts against the published price list, regardless of whether the account is billed per-token or by subscription. Over the 2026-08-23/24 runs — 32 sessions, 14 packages, five projects — the batch came to $251 list and consumed roughly 23 percentage points of a weekly subscription limit; the $15 cap was therefore ~1.4% of that week, not the third the number suggests. The cap also did not hold (one package finished at $18.40 against it, because the check only lands between turns) and it fired at the worst possible point: spend is front-loaded into context, planning and the critic gates, so aborting after the implementation and before the PR discards all of it. 61% of that $251 went on attempts that never reached `ci-green`. Cost control lives in the pipeline's shape — fewer dead attempts, work pushed so a retry resumes (lower plugin `#95`), the right model per role — not in a mid-flight ceiling. The platform's session limits are the runaway stop, and `skills/run/SKILL.md` → *Why there is no dollar budget* is the long form.

**`--model` is a correctness property, not a preference.** Without it the headless session inherits whatever `/model` the human last left set in an unrelated interactive session, so identical packages cost different amounts for reasons invisible to everyone in the run. In the same batch the main turn was **33% of a package's cost on Sonnet and 52% on Opus** — the split falls exactly on the day the human forgot to switch back. The lower plugin's six subagents already pin their models in frontmatter (`planner: opus`, the rest `sonnet`); the session's own turn was the last unpinned one. It sequences phases, counts rounds, posts events and drives git/PR/CI, delegating every judgement that needs a bigger model to a subagent that asks for one — hence the `sonnet` default, overridable per run with `ADEV_SESSION_MODEL`.

### The launch lock

Two `claude` processes starting at the same moment race on `~/.claude.json` and can corrupt it (upstream anthropics/claude-code #28813, #28847; observed here as `fix: serialize Claude session launches` in the meta-repo). `scripts/start-package-session.sh` takes `mkdir "$HOME/.claude/.launch-lock"` (atomic on NTFS and POSIX), writes its PID inside, breaks a lock whose owner is dead or older than 60 s, and holds it **only across the start** — until the stream shows the first `system` record or 25 s elapsed — never across the run. Inside a project everything is sequential for the same reason (and because parallel worktrees race on shared services).

### Two skills, neither blocks on `AskUserQuestion`

| skill | human | may `AskUserQuestion` | how it surfaces a question | writes |
|---|---|---|---|---|
| `gatekeeper` | starts the session, not needed at the keyboard while it runs | **no — never granted, never used** | posts `## Clarification needed (gatekeeper)` on the package ticket, leaves it in Backlog, moves to the next package | epics, `parent` relations, `epic` label, clarification-needed comments, Backlog → Planned |
| `run` | absent, may run all night | no (tool not granted) | posts the question as a ticket comment, moves the card to Question | Todo → Doing → Done/Question, `merge_pr`, worktrees, the few comments the skill names |

`AskUserQuestion` is not part of this plugin at all — the last remaining use (confirming the
`bundler`'s package cut, and asking the `clarifier`'s open questions in chat) was removed: a
bundling decision is applied and reported rather than confirmed, and a clarification question is
posted on the ticket rather than asked live, so `gatekeeper` never blocks a run on somebody being
present to answer (`skills/gatekeeper/SKILL.md`, "Nothing in this skill blocks on a chat answer").
A human still has to start the `gatekeeper` session by hand — that has not changed, and there is
no cron/headless trigger for it in this plugin yet — but once started it runs every candidate to
completion in one pass instead of stalling on the first one that needs input.

Order inside `gatekeeper` is mandatory: **bundle, then clarify** — clarification comments are posted on the package ticket, and a ticket that becomes an epic child afterwards would carry comments the run never reads. `Planned → Todo` is human-only; `Question → anywhere` is human-only. No skill here ever moves a card into Todo.

### Board model (shared GitHub Projects v2 board, logical names from `projects.yml`)

| column | meaning | who moves in |
|---|---|---|
| Backlog | everything new | anyone |
| Planned | bundled + clarified, no open questions | gatekeeper |
| Todo | released for the run — the only column `run` reads | **human only** |
| Doing | package dispatched | run |
| Review | PR open, CI running / red / awaiting merge | lower plugin |
| Done | merged, CI green | run |
| Question | escalated; the question is a ticket comment | run in; **human only** out |

Logical names are the contract; native names (`"Frage offen"` vs `"Question"`) are resolved per call via `list_board_columns` and never hardcoded. Writes: `update_ticket(custom_fields={"Status": <native>})`. Reads: `list_tickets(column=<logical>)`. Comments are the log, columns are the signal; an empty Question column means no open questions.

### Escalation rule (ecosystem-wide, root `AGENTS.md`)

Escalate one level up until a level can answer; the human only when no level is left, and only for a **decision**, never a **retry**. Each level states what it checked and why that was not enough. A Question card whose only sensible reaction is "kick it again" is a bug in whichever level forwarded instead of trying — `clarifier` applies this before the run, the lower plugin's three-round caps during it, `run`'s second attempt after it.

### What a project needs in `~/.seretos/projects.yml`

- `permissions.issues.create` + `issues.modify` — epics, relations, comments, column writes (both skills).
- `permissions.pulls.create` + `pulls.modify` — the lower plugin's PR; `pulls.merge` — `run`'s `merge_pr` (without it `run` still works but leaves packages in Review with a note).
- `permissions.board.manage` — only for the one-time `ensure_board_column` when `Planned`/`Question` do not exist on the board yet.
- `board.columns` must list the logical names `Backlog`, `Planned`, `Todo`, `Doing`, `Review`, `Done`, `Question` — `gatekeeper` STOPs without `Planned`/`Question`, `run` STOPs without `Todo`/`Doing`/`Review`/`Done`/`Question`.
- `local_path` must point at a git checkout: `run` passes it as `repo_root` to `worktree_create`; both read-only subagents read code under it.

### Release mechanics

- **Release is orphan-branch + marketplace dispatch.** `release.yml` (manual: Actions → release → `version=X.Y.Z`) stamps the version into both manifests, force-pushes an orphan `release` branch holding only install-ready files and POSTs a dispatch (`category: skill`) to `Seretos/agent-marketplace`. `main` and `release` share no history. Clients install at the tag `agent-ticket-orchestrator--vX.Y.Z`.
- **`agents/` is a release artifact.** The stage step copies `agents/` next to `skills/`; drop that line and the released skills dispatch undefined subagent types.
- **Required secret:** `MARKETPLACE_DISPATCH_TOKEN` — fine-grained PAT, `Contents: RW` + `Pull requests: RW` on `Seretos/agent-marketplace` only.
- **`assets/icon.png` and `description.md` are release artifacts.** The dispatch payload points at `raw.githubusercontent.com/${repo}/${TAG}/assets/icon.png` and `…/description.md`, so both must live on the orphan `release` branch at the tagged commit — the stage step copies them for exactly that reason.
- **Dependencies** are declared in `.claude-plugin/plugin.json` (`agent-autonomous-developer`, `agent-project-issues`, `agent-worktree`); Claude Code installs/loads them with this plugin. The Codex manifest (`.codex-plugin/plugin.json`) carries no dependency field — Codex hosts install the MCPs separately.
- **LF only.** Claude Code silently ignores a `SKILL.md` or `agents/*.md` with CRLF line endings; `.gitattributes` forces LF and `lint.yml` fails on CRLF.
