#!/usr/bin/env python3
"""
Render and parse the `<!-- ato:event v1 -->` machine-readable comment block
(#63) -- the `ato:event` counterpart to `adev:event v1` (see AGENTS.md, "The
comment-event contract").

Closes the symptom (verbatim from the ticket): a tool reading this plugin's
ticket comments (ecosystem-statistics#11) can only detect an escalation or a
merge by matching free-text "Escalated:" lines and column moves, so its
counts break when the skill's wording changes. This script gives `run` a
block to render next to that prose, and gives a reader a single place to
parse it back -- independent of whatever wording surrounds it.

Usage
-----

    ato-event.py render --event <e> --package <id> [--reason <r>]
                         [--pr <n>] [--merge-sha <s>]

Prints the block to stdout, exactly, with all five keys in fixed order
(`event`, `package`, `reason`, `pr`, `merge_sha`) always emitted -- an
omitted `--reason`/`--pr`/`--merge-sha` renders as an empty value, never a
dropped key -- and exits 0. Rejects invalid input: exit 1, `error: <what>`
on stderr naming the offending value, empty stdout. Argparse's own usage
errors (missing `--event`/`--package`, unknown flag) stay exit 2.

    ato-event.py parse

Reads stdin (any text -- a whole ticket comment, prose and all), finds the
first `<!-- ato:event v1 -->` block via a dumb `key: value` reader (split on
first `:`, strip whitespace including a trailing `\\r` from CRLF input,
unknown keys ignored, first block wins), applies the same validation, and
prints JSON `{"event", "package", "reason", "pr", "merge_sha"}` (exit 0). No
block found, or the found block fails validation: exit 1, `error: <what>` on
stderr, empty stdout.

Vocabulary
----------

Events: `escalated`, `triage-answered`, `merged`. `reason` is required for
`escalated`, optional (but vocabulary-checked if given) on the other two.

Reasons, mapped to the `skills/run/SKILL.md` Question sites they will be
wired to in #67 (copied here for that ticket's reference; this script does
not read SKILL.md):

    failed             -- SKILL.md l.366; l.555
    blocked            -- l.450
    triage-reblocked   -- l.453-457
    split-failed       -- l.446
    merge-failed       -- l.482-484
    merge-conflict     -- l.545
    rebase-decision    -- l.552

`pr`, `merge_sha` and `package` are free strings, but must not contain a
newline or the literal `-->` -- either would split or prematurely close the
rendered block.
"""

import argparse
import json
import sys

EVENTS = ("escalated", "triage-answered", "merged")

REASONS = (
    "failed",
    "blocked",
    "triage-reblocked",
    "split-failed",
    "merge-failed",
    "merge-conflict",
    "rebase-decision",
)

# Fixed order the block is always rendered (and read) in.
KEYS = ("event", "package", "reason", "pr", "merge_sha")

BLOCK_OPEN = "<!-- ato:event v1"
BLOCK_CLOSE = "-->"

# Values that would split or prematurely close the block if embedded raw.
_UNSAFE_MARKERS = ("\n", "\r", BLOCK_CLOSE)


def _contains_unsafe_marker(value):
    return any(marker in value for marker in _UNSAFE_MARKERS)


def validate(event, package, reason, pr, merge_sha):
    """Return an `error: ...` message (already forbidding embedded markers,
    naming the offending field/value) if the fields are invalid, else None."""
    if event not in EVENTS:
        return f"error: unknown event: {event!r} (must be one of {', '.join(EVENTS)})"

    for name, value in (("package", package), ("pr", pr), ("merge_sha", merge_sha)):
        if value and _contains_unsafe_marker(value):
            return (
                f"error: invalid {name}: value contains a newline or '-->', "
                f"which would split the rendered block: {value!r}"
            )

    if not package:
        return "error: empty package: --package is required and must not be empty"

    if reason:
        if reason not in REASONS:
            return f"error: unknown reason: {reason!r} (must be one of {', '.join(REASONS)})"
    elif event == "escalated":
        return "error: escalated without reason: --reason is required for event 'escalated'"

    return None


def render_block(event, package, reason, pr, merge_sha):
    """Build the block text, fixed key order, all five keys always present."""
    lines = [BLOCK_OPEN]
    values = {"event": event, "package": package, "reason": reason,
              "pr": pr, "merge_sha": merge_sha}
    for key in KEYS:
        lines.append(f"{key}: {values[key]}")
    lines.append(BLOCK_CLOSE)
    return "\n".join(lines) + "\n"


def find_first_block(text):
    """Return the list of raw lines between the first `BLOCK_OPEN` and its
    matching `BLOCK_CLOSE`, or None if no complete block is present.

    Splits with `splitlines()` (which already treats a `\\r\\n` pair as one
    line break) and additionally strips any trailing `\\r` per line, so a
    block read back after CRLF normalisation elsewhere still parses."""
    lines = text.splitlines()
    for i, line in enumerate(lines):
        if line.rstrip("\r").strip() == BLOCK_OPEN:
            body = []
            for later in lines[i + 1:]:
                if later.rstrip("\r").strip() == BLOCK_CLOSE:
                    return body
                body.append(later)
            return None  # opened but never closed
    return None


def read_kv_lines(body_lines):
    """Dumb `key: value` reader: split on the first `:`, strip whitespace
    (including a trailing `\\r`) from key and value, unknown keys ignored.
    Returns a dict containing only the known keys actually present."""
    result = {}
    for entry in body_lines:
        entry = entry.rstrip("\r")
        if ":" not in entry:
            continue
        key, _, value = entry.partition(":")
        key = key.strip()
        value = value.strip()
        if key in KEYS:
            result[key] = value
    return result


def build_parser():
    parser = argparse.ArgumentParser(prog="ato-event.py")
    sub = parser.add_subparsers(dest="command", required=True)

    render_p = sub.add_parser("render", help="print an ato:event v1 block")
    render_p.add_argument("--event", required=True)
    render_p.add_argument("--package", required=True)
    render_p.add_argument("--reason", default="")
    render_p.add_argument("--pr", default="")
    render_p.add_argument("--merge-sha", dest="merge_sha", default="")

    sub.add_parser("parse", help="read stdin, print the first block as JSON")

    return parser


def cmd_render(args):
    error = validate(args.event, args.package, args.reason, args.pr, args.merge_sha)
    if error:
        print(error, file=sys.stderr)
        return 1
    sys.stdout.write(render_block(args.event, args.package, args.reason,
                                   args.pr, args.merge_sha))
    return 0


def cmd_parse():
    text = sys.stdin.read()
    body = find_first_block(text)
    if body is None:
        print("error: no ato:event v1 block found in input", file=sys.stderr)
        return 1

    parsed = read_kv_lines(body)
    event = parsed.get("event", "")
    package = parsed.get("package", "")
    reason = parsed.get("reason", "")
    pr = parsed.get("pr", "")
    merge_sha = parsed.get("merge_sha", "")

    error = validate(event, package, reason, pr, merge_sha)
    if error:
        print(error, file=sys.stderr)
        return 1

    print(json.dumps({
        "event": event,
        "package": package,
        "reason": reason,
        "pr": pr,
        "merge_sha": merge_sha,
    }))
    return 0


def main(argv=None):
    args = build_parser().parse_args(argv)
    if args.command == "render":
        return cmd_render(args)
    if args.command == "parse":
        return cmd_parse()
    return 2  # unreachable: subparsers(required=True) rejects anything else


if __name__ == "__main__":
    sys.exit(main())
