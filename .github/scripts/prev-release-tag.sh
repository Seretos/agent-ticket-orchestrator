#!/usr/bin/env bash
# Resolve the previous release tag for a plugin, and validate a bare semver
# string against the one grammar this repo uses for both purposes.
#
# Usage:
#   git tag -l | bash prev-release-tag.sh "$TAG"     # highest prior release
#   bash prev-release-tag.sh --validate "$VERSION"   # exit 0/1
#
# Deliberately NOT `sort -V`: it orders "1.0.0" before "1.0.0-rc.1" (the
# wrong way round per semver, which says a release beats any prerelease of
# the same core version) and its availability/behaviour differs between GNU
# and BSD sort. This script implements real semver comparison instead.
set -euo pipefail

# No build metadata allowed (no trailing "+...").
SEMVER_RE='^(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)(-(0|[1-9][0-9]*|[0-9]*[A-Za-z-][0-9A-Za-z-]*)(\.(0|[1-9][0-9]*|[0-9]*[A-Za-z-][0-9A-Za-z-]*))*)?$'

is_valid_semver() {
  [[ "$1" =~ $SEMVER_RE ]]
}

if [[ "${1:-}" == "--validate" ]]; then
  if is_valid_semver "${2:-}"; then
    exit 0
  else
    exit 1
  fi
fi

TAG_BEING_CREATED="${1:-}"
PREFIX="${TAG_BEING_CREATED%%--v*}--v"
TARGET_VERSION="${TAG_BEING_CREATED#"$PREFIX"}"

# --- version core (major.minor.patch) -------------------------------------

version_core() {
  echo "${1%%-*}"
}

version_pre() {
  if [[ "$1" == *-* ]]; then
    echo "${1#*-}"
  else
    echo ""
  fi
}

# Echoes -1, 0 or 1 comparing two non-negative decimal integer strings that
# contain no leading zeros (guaranteed by this script's own SEMVER_RE grammar
# for the version core and for numeric prerelease identifiers). Deliberately
# NOT bash's `((10#$a > 10#$b))` arithmetic: that is fixed-width machine
# arithmetic (typically 64-bit signed) and silently overflows -- producing a
# wrong ordering, not an error -- for a syntactically valid version whose
# numeric field is long enough (the grammar itself puts no digit-count limit
# on it). A longer digit string is always numerically larger, since neither
# input has a leading zero; equal-length strings compare correctly with
# plain lexicographic ASCII comparison, because '0' < '1' < ... < '9' in
# ASCII order matches digit value order. This works for arbitrarily long
# numbers without needing arbitrary-precision arithmetic.
compare_numeric_string() {
  local a="$1" b="$2"
  if ((${#a} > ${#b})); then
    echo 1
  elif ((${#a} < ${#b})); then
    echo -1
  elif [[ "$a" == "$b" ]]; then
    echo 0
  elif [[ "$a" < "$b" ]]; then
    echo -1
  else
    echo 1
  fi
}

# Echoes -1, 0 or 1 comparing two "major.minor.patch" strings numerically.
compare_core() {
  local IFS=.
  local -a a=($1)
  local -a b=($2)
  local i ai bi c
  for i in 0 1 2; do
    ai=${a[$i]:-0}
    bi=${b[$i]:-0}
    c=$(compare_numeric_string "$ai" "$bi")
    if ((c != 0)); then
      echo "$c"
      return
    fi
  done
  echo 0
}

is_numeric_id() {
  [[ "$1" =~ ^[0-9]+$ ]]
}

# Echoes -1, 0 or 1 comparing two dot-separated prerelease identifiers per
# semver precedence: numeric identifiers compare numerically, numeric
# always sorts below alphanumeric, otherwise plain ASCII compare.
compare_identifier() {
  local a="$1" b="$2"
  if is_numeric_id "$a" && is_numeric_id "$b"; then
    compare_numeric_string "$a" "$b"
    return
  fi
  if is_numeric_id "$a" && ! is_numeric_id "$b"; then
    echo -1
    return
  fi
  if ! is_numeric_id "$a" && is_numeric_id "$b"; then
    echo 1
    return
  fi
  if [[ "$a" == "$b" ]]; then
    echo 0
  elif [[ "$a" < "$b" ]]; then
    echo -1
  else
    echo 1
  fi
}

# Echoes -1, 0 or 1 comparing two full prerelease strings (dot-separated
# identifier lists). A longer list wins when all shared identifiers match.
compare_prerelease() {
  local IFS=.
  local -a aids=($1)
  local -a bids=($2)
  local n=${#aids[@]}
  local m=${#bids[@]}
  local max=$n
  ((m > max)) && max=$m
  local i c
  for ((i = 0; i < max; i++)); do
    if ((i >= n)); then
      echo -1
      return
    fi
    if ((i >= m)); then
      echo 1
      return
    fi
    c=$(compare_identifier "${aids[$i]}" "${bids[$i]}")
    if ((c != 0)); then
      echo "$c"
      return
    fi
  done
  echo 0
}

# Returns success (0) iff version $1 is strictly greater than version $2.
semver_gt() {
  local a="$1" b="$2"
  local a_core a_pre b_core b_pre core_cmp pre_cmp
  a_core=$(version_core "$a")
  a_pre=$(version_pre "$a")
  b_core=$(version_core "$b")
  b_pre=$(version_pre "$b")

  core_cmp=$(compare_core "$a_core" "$b_core")
  if ((core_cmp > 0)); then
    return 0
  elif ((core_cmp < 0)); then
    return 1
  fi

  # Cores equal: a release (no prerelease) beats any prerelease.
  if [[ -z "$a_pre" && -n "$b_pre" ]]; then
    return 0
  fi
  if [[ -n "$a_pre" && -z "$b_pre" ]]; then
    return 1
  fi
  if [[ -z "$a_pre" && -z "$b_pre" ]]; then
    return 1 # identical version, not strictly greater
  fi

  pre_cmp=$(compare_prerelease "$a_pre" "$b_pre")
  if ((pre_cmp > 0)); then
    return 0
  fi
  return 1
}

# --- main: pick the highest surviving candidate from stdin -----------------

best=""
best_version=""
while IFS= read -r line || [[ -n "$line" ]]; do
  # Tolerate CRLF-terminated stdin: a Windows-native writer (e.g. Python's
  # subprocess in text mode) feeding this pipe emits "\r\n", and bash's
  # `read` does not strip the trailing "\r" on its own -- left in place it
  # would make every candidate fail the semver check below.
  line="${line%$'\r'}"
  [[ -z "$line" ]] && continue
  [[ "$line" == "$TAG_BEING_CREATED" ]] && continue
  case "$line" in
  "$PREFIX"*) ;;
  *) continue ;;
  esac
  version="${line#"$PREFIX"}"
  is_valid_semver "$version" || continue
  # Exclude not just the exact tag being created, but any candidate whose
  # version is >= the tag being created's version. Without this, an
  # out-of-order/backport release (e.g. creating v0.1.3 when v0.1.4 already
  # exists) would resolve PREV_TAG to a numerically NEWER tag than the one
  # being cut, reversing the notes baseline instead of finding the true
  # predecessor. PREV_TAG must always be strictly less than the tag being
  # created.
  if is_valid_semver "$TARGET_VERSION" && ! semver_gt "$TARGET_VERSION" "$version"; then
    continue
  fi
  if [[ -z "$best" ]] || semver_gt "$version" "$best_version"; then
    best="$line"
    best_version="$version"
  fi
done

echo "$best"
