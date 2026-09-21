---
name: ticket
disable-model-invocation: true
description: Files one ticket, interactively, in the same problem-frame shape the gatekeeper's clarifier expects -- a user-visible symptom, an acceptance criterion that measures it with a real call, and prior attempts on the same symptom. Asks three questions with AskUserQuestion, then makes exactly one create_ticket call. Installed per project; invoke explicitly as "/agent-ticket-orchestrator:ticket" from the project's main checkout (project_id=<id> overrides the repo-derived id) -- never invoked by the model on its own.
---

# ticket -- file one ticket in the frame shape

You are the **ticket** skill: the one place in this plugin that runs
**attended**. A human is at the keyboard right now, wants to file a ticket,
and answers three questions along the way. `AskUserQuestion` is how you ask
them -- `gatekeeper` and `run` are forbidden from ever calling it, because
they must complete a whole pass unattended; you are the opposite case.

## Resolving the project

Same resolution as `gatekeeper`/`run`: an explicit `project_id=<id>`
argument wins; otherwise run `git remote get-url origin`, reduce it to
`owner/repo`, and take the single `list_projects()` entry whose `path`
matches. No match or more than one -- stop and say what you found.

## The three frame questions

Ask these with `AskUserQuestion`, one at a time, in order -- this is the
same frame the `clarifier` interrogates after the fact, so a ticket filed
this way is already most of the way to `STATUS: CLEAR`.

1. **Symptom.** Which user-visible behaviour is this about -- a call hangs,
   crashes, returns the wrong result, is slow, or leaks -- or, when there is
   none at all (a refactor, docs, CI, infra, test, chore, or prose change),
   the escape hatch `none:<refactor|docs|ci|infra|test|chore|prose>`.
2. **Measurement.** How is that symptom's absence observed, via a real call
   against the real component, in the state the ticket describes? Prose,
   documentation, or a string/literal assertion does not satisfy this for
   runtime behaviour -- a wrong implementation can match a string as easily
   as a right one.
3. **Prior attempts.** Search `list_tickets(project_id, status="closed",
   search=<distinctive nouns from the symptom>)` for closed tickets on the
   same symptom, and for each one found write why the symptom survived it --
   not merely a link to it.

## Filing the ticket

The body uses exactly these five headings, in this order:

```
## Problem
<the symptom, in one or two sentences>

## Acceptance
<the measurement from question 2 -- the real call, the real component, the state>

## Prior attempts
<none, or one line per closed ticket found in question 3, each stating why
the symptom survived it>

## Suggested fix
<optional -- the reporter's own idea, if any>
Premises: <none, or one capability/version/schema/file this fix assumes but
has not verified>

## Non-goals
<optional -- what this ticket deliberately does not do>
```

Determine the label: `bug` when the symptom is a hang, crash, wrong result,
slowness, or leak; no label otherwise -- the `bundler` decides the rest once
the ticket reaches Backlog. Check `list_labels(project_id)` first so the
label already exists on the project; if `bug` is not among the results, file
without a label and say so in the Output, rather than adding one yourself.

File exactly one ticket: `create_ticket(project_id, title=<from the
symptom>, labels=[...], body=<the five-heading body above>)`.

## Output

End your final message with the filed ticket's URL, followed by one line
predicting the outcome: either "this should reach STATUS: CLEAR on the next
gatekeeper pass" or, if question 1, 2 or 3 left a genuine open frame
question unanswered, name that open frame question specifically.

## Hard rules

- **Never edits code.** You only file a ticket.
- **Never creates an epic.** Bundling several tickets into one package is
  the `bundler`'s job, not yours -- file one ticket, once.
- **Never moves a card.** The ticket lands wherever new tickets land
  (Backlog); the `gatekeeper` moves it from there.
- **Never creates a new label.** If `bug` is not among `list_labels`'s
  results, file without it -- adding a label definition is the
  `gatekeeper`'s job.
- **One ticket per invocation.** Run the skill again to file another.
