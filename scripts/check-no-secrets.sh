#!/usr/bin/env bash
#
# Fail the build if a credential, or a page carrying one, is tracked in git.
#
# This exists because it already happened. The at-grade review packs embed
# VITE_LINZ_API_KEY in every tile URL; generated pages were committed twice and
# purged twice by force-push. A force-push does NOT remove a blob from the
# remote - it only makes it unreachable, and the object stays retrievable by
# its sha until the host garbage-collects it. So "we were careful" is not a
# control, and "we rewrote history" is not a fix.
#
# Two independent guards, because either alone is easy to slip past:
#
#   1. SHAPE   - a LINZ Basemaps key looks like c01 + 24 lowercase base32
#                characters. Catches a key pasted anywhere, in any file type,
#                whether or not it sits in a URL.
#   2. CONTEXT - a basemaps.linz.govt.nz tile URL with a real-looking api=
#                value. Catches a key format we have not seen yet, and catches
#                the specific artefact that leaked before.
#
# Plus a structural guard: the generated pack directories must never be
# tracked at all.
#
# Scans only files git actually tracks. Untracked working files, .env and the
# gitignored packs are the developer's business; what ships is ours.
#
# Usage:  scripts/check-no-secrets.sh
# Exit:   0 clean, 1 something is tracked that must not be.

set -uo pipefail

fail=0
note() { printf '%s\n' "$*" >&2; }

# Files git tracks, NUL-separated so paths with spaces survive.
mapfile -d '' -t tracked < <(git ls-files -z)
if [ "${#tracked[@]}" -eq 0 ]; then
  note "no tracked files found - is this a git repository?"
  exit 1
fi

# --- 1. shape ------------------------------------------------------------
# c01 then 24 chars of Crockford-ish base32. Anchored on word boundaries so a
# long hex blob does not trip it.
KEY_SHAPE='\bc01[0-9a-hjkmnp-tv-z]{24}\b'
if hits=$(printf '%s\0' "${tracked[@]}" \
            | xargs -0 grep -InE "$KEY_SHAPE" -- 2>/dev/null); then
  if [ -n "$hits" ]; then
    note "SECRET SHAPE: something matching a LINZ Basemaps API key is tracked."
    note "Files and line numbers only - the value is deliberately NOT printed:"
    printf '%s\n' "$hits" | sed -E 's/:(.*)$/:  <redacted>/' >&2
    fail=1
  fi
fi

# --- 2. context ----------------------------------------------------------
# A tile URL carrying an api= value that is not an obvious placeholder.
# Allowed: ${VAR}, {z}/{x}/{y} style braces, the literal KEY, empty, or a JS
# expression. Anything else is treated as real.
URL_CTX='basemaps\.linz\.govt\.nz[^"'"'"' ]*[?&]api='
if hits=$(printf '%s\0' "${tracked[@]}" \
            | xargs -0 grep -InE "$URL_CTX" -- 2>/dev/null \
            | grep -vE '[?&]api=(\$\{|\{|KEY\b|"|'"'"'|\)|$|\\\$)' \
            | grep -vE '[?&]api=\$\{?[A-Za-z_]' ); then
  if [ -n "$hits" ]; then
    note "BAKED KEY: a tracked file has a LINZ tile URL with a literal api= value."
    note "Generated review pages must inject the key at runtime from a"
    note "gitignored sidecar - see scratch/holdout_review.py write_key_sidecar."
    printf '%s\n' "$hits" | sed -E 's/:(.*)$/:  <redacted>/' >&2
    fail=1
  fi
fi

# --- 3. structure --------------------------------------------------------
# The generated packs are gitignored. If one is tracked, the ignore rule was
# bypassed and the pages are in history whatever their contents.
if packs=$(printf '%s\n' "${tracked[@]}" \
             | grep -E '^scratch/(review|blind|holdout)[^/]*/'); then
  if [ -n "$packs" ]; then
    note "TRACKED REVIEW PACK: these are generated and must stay gitignored."
    printf '%s\n' "$packs" >&2
    fail=1
  fi
fi

if [ "$fail" -eq 0 ]; then
  echo "no tracked credentials found (${#tracked[@]} files scanned)"
else
  note ""
  note "Do NOT fix this by rewriting history alone. Anything already pushed"
  note "must be treated as disclosed and the credential rotated."
fi
exit "$fail"
