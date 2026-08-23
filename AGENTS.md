# agent-ticket-orchestrator

Pure skill + agents plugin — no binary, no MCP server. The **upper** layer of the Seretos ticket pipeline: it selects, bundles, clarifies, dispatches, moves board columns and merges. The **lower** layer, `agent-autonomous-developer`, turns one work package into one CI-green PR and knows nothing about this plugin. Two skills (`gatekeeper`, `run`), two subagents (`bundler`, `clarifier`). README.md covers *what* it does and how to install; the skills and agents document their own rules. This file records only what you cannot reconstruct from any single file.

## Contracts an agent won't infer from the tree

### The lower plugin's entry point (the only thing this plugin knows about it)

the `run` skill starts, from its own main turn, with cwd = the prepared worktree:

```
claude -p "/agent-autonomous-developer:process-ticket package=<id> project_id=<project> worktree_path=<abs> base_branch=<default>" \
  --permission-mode bypassPermissions --disallowedTools AskUserQuestion \
  --output-format stream-json --verbose --max-budget-usd <cap> > <rundir>/stream.jsonl 2> <rundir>/stderr.txt
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

`rounds`: `<gate>=<used>/<cap>(<f>f,<i>i)` — `f` rounds ended with real findings, `i` rounds were lost to infrastructure; both count toward the cap. Events, exhaustive: `started`, `plan-committed`, `plan-critic-verdict`, `tests-red`, `test-critic-verdict`, `tests-green` (local pre-filter only — never success), `review-verdict`, `pr-opened` (`pr` filled), `ci-red` (`ci_run` filled), and the three **terminal** ones: `ci-green` (the only success signal), `blocked` (needs a human decision; text = question, options, recommendation, what was checked), `failed` (terminal non-decision failure; text distinguishes findings vs infra). A process that ended without a terminal event counts as `failed`.

Reactions (`skills/run/SKILL.md` implements exactly this): `ci-green` → `merge_pr`, → Done, remove worktree · `blocked` → set aside, re-dispatch once at end of run, still blocked → Question · `failed`/none → one fresh re-dispatch, still failed → Question with the failure summary. The run succeeds only if every package reached Done; partial is reported as partial; no "not included" list in any PR, ever.

### Why state lives in the ticket, not in the return value

A headless `claude -p` returns "process ended" plus prose. Reconstructing state from that prose — or from a subagent's reply — is how silent report loss happened in the lower plugin's fleet era (#60, #88). So the **ticket is the state store**: the process exit code is a courtesy, and `run` re-reads `list_comments(order="desc")` for the latest `adev:event` before every decision. A crash anywhere in the chain loses nothing that matters; re-running `run` picks the card up from its column.

### Why the package session is a CLI process started by the skill itself — not an `Agent` subagent, not a wrapper

An `Agent` subagent inherits the **parent session's** MCP connections — this plugin's Serena project, this session's tool set — not the target project's. The lower plugin needs the target worktree's own `CLAUDE.md`, `.serena`, `.claude/settings.json`, plugin set and MCPs, which only a fresh `claude` process with cwd = worktree gets. And there is deliberately **no wrapper subagent** around that process either: a task-notification for a backgrounded Bash command reaches the **main** session, while a subagent that ends its turn "to wait" is terminated, not suspended (lower plugin #83/#88) — a wrapper that has to stay in-turn for hours is the exact shape that went silent before. So `run` starts the process with `Bash(run_in_background: true)` in its own turn — via `scripts/start-package-session.sh`, which owns the launch lock, the stream files and the exit marker so the skill never reproduces those mechanics from prose — and is woken by the harness when it ends. The orchestrator never reads `stream.jsonl`; state comes from the ticket. Retry = a fresh start with `attempt+1`, never a resume.

### The launch lock

Two `claude` processes starting at the same moment race on `~/.claude.json` and can corrupt it (upstream anthropics/claude-code #28813, #28847; observed here as `fix: serialize Claude session launches` in the meta-repo). `scripts/start-package-session.sh` takes `mkdir "$HOME/.claude/.launch-lock"` (atomic on NTFS and POSIX), writes its PID inside, breaks a lock whose owner is dead or older than 60 s, and holds it **only across the start** — until the stream shows the first `system` record or 25 s elapsed — never across the run. Cross-project `run … project_id=all` is sequential for the same reason (and because parallel worktrees race on shared services).

### Two skills, two supervision modes

| skill | human | may `AskUserQuestion` | writes |
|---|---|---|---|
| `gatekeeper` | present, interactive | **yes — the only place in the ecosystem** | epics, `parent` relations, `epic` label, clarification comments, Backlog → Planned |
| `run` | absent, may run all night | no (tool not granted) | Todo → Doing → Done/Question, `merge_pr`, worktrees, the few comments the skill names |

Order inside `gatekeeper` is mandatory: **bundle, then clarify** — answers are posted on the package ticket, and a ticket that becomes an epic child afterwards would carry answers the run never reads. `Planned → Todo` is human-only; `Question → anywhere` is human-only. No skill here ever moves a card into Todo.

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
