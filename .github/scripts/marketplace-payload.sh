#!/usr/bin/env bash
# Build the agent-marketplace dispatch JSON payload.
#
# Reads NAME, DESC, REPO, VERSION, TAG, CHANGELOG from the environment.
# CHANGELOG is read directly as "$CHANGELOG" -- never through a $(...)
# command substitution, which would silently strip a trailing newline -- so
# a hostile or newline-terminated changelog round-trips byte-for-byte.
#
# Built with a single jq -n invocation, never a heredoc: the changelog is
# multi-line markdown that can contain backticks, quotes and newlines, any
# of which would break an unquoted `curl -d @- <<EOF` pattern and silently
# drop the whole dispatch (agent-marketplace#235's contract note; the same
# thing already happened once with `tags`, see agent-marketplace@89aa850).
set -euo pipefail

jq -n \
  --arg name "$NAME" \
  --arg desc "$DESC" \
  --arg repo "$REPO" \
  --arg version "$VERSION" \
  --arg ref "$TAG" \
  --arg icon "https://raw.githubusercontent.com/${REPO}/${TAG}/assets/icon.png" \
  --arg description_url "https://raw.githubusercontent.com/${REPO}/${TAG}/description.md" \
  --arg changelog "$CHANGELOG" \
  '{event_type: "plugin-release", client_payload: (
      {
        name: $name,
        description: $desc,
        repo: $repo,
        category: "skill",
        version: $version,
        ref: $ref,
        icon: $icon,
        description_url: $description_url
      }
      + (if $changelog == "" then {} else {changelog: $changelog} end)
   )}'
