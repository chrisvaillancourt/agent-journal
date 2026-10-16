#!/usr/bin/env bash
# Offline checks for the agent-journal skill: no model calls, about a second.
# Usage: scripts/check.sh
#   1. Frontmatter is valid YAML (parsed with Ruby's Psych when ruby is present) and follows
#      the Agent Skills spec (name, description, allowed fields).
#   2. Progressive disclosure: SKILL.md size, links resolve, references stay one level deep.
#   3. Portability and privacy: forward-slash paths, and a heuristic scan for personal paths,
#      org names and email addresses (not proof that nothing private remains).
#   4. The skills CLI parses and discovers the skill (skipped when `skills` isn't installed).
#   5. The helper script's tests pass (pytest through uv; skipped when uv isn't installed).
# Run after any edit to the skill, script or docs.
set -uo pipefail

repo=$(CDPATH= cd -- "$(dirname "$0")/.." && pwd)
name=agent-journal
skill="$repo/skills/$name"
failures=0 skipped=0

pass() { printf 'PASS  %s\n' "$1"; }
fail() { printf 'FAIL  %s\n' "$1"; failures=$((failures + 1)); }
skip() { printf 'SKIP  %s\n' "$1"; skipped=$((skipped + 1)); }

# The YAML frontmatter between the first two `---` lines.
frontmatter=$(awk 'NR == 1 && $0 == "---" { inside = 1; next } inside && $0 == "---" { exit } inside { print }' "$skill/SKILL.md")
field() { printf '%s\n' "$frontmatter" | sed -n "s/^$1: *//p" | head -n 1; }

echo "== frontmatter"
keys=$(printf '%s\n' "$frontmatter" | sed -n 's/^\([A-Za-z_-]*\):.*/\1/p')
if command -v ruby > /dev/null 2>&1; then
  if fm_json=$(printf '%s\n' "$frontmatter" | ruby -ryaml -rjson -e 'd = YAML.safe_load(STDIN.read); raise "not a mapping" unless d.is_a?(Hash); puts JSON.generate(d)' 2> /dev/null); then
    pass "frontmatter is valid YAML"
    fm_name=$(printf '%s' "$fm_json" | jq -r '.name // ""')
    fm_desc=$(printf '%s' "$fm_json" | jq -r '.description // ""')
    keys=$(printf '%s' "$fm_json" | jq -r 'keys[]')
  else
    fail "frontmatter is not valid YAML (an unquoted colon in a value is the usual cause)"
    fm_name=$(field name) fm_desc=$(field description)
  fi
else
  skip "YAML parse (ruby not installed); the skills CLI check below still parses it"
  fm_name=$(field name) fm_desc=$(field description)
fi
if [ "$fm_name" = "$name" ] && [ "$(basename "$skill")" = "$fm_name" ]; then
  pass "name matches its directory ($fm_name)"
else
  fail "name '$fm_name' must match the directory '$(basename "$skill")'"
fi
if printf '%s' "$fm_name" | grep -Eq '^[a-z0-9]+(-[a-z0-9]+)*$' && [ ${#fm_name} -le 64 ]; then
  pass "name uses 1-64 lowercase letters, digits and single hyphens"
else
  fail "name breaks the spec's character rules"
fi
if [ -n "$fm_desc" ] && [ ${#fm_desc} -le 1024 ]; then
  pass "description is present and ${#fm_desc} <= 1024 characters"
else
  fail "description is missing or longer than 1024 characters (${#fm_desc})"
fi
# omp shows only the description's first sentence (or a short cached hint), so it must stand alone.
first_sentence=$(printf '%s' "$fm_desc" | sed 's/\. .*/./')
if [ ${#first_sentence} -ge 40 ] && [ ${#first_sentence} -le 99 ]; then
  pass "description's first sentence is ${#first_sentence} characters (40-99)"
else
  fail "description's first sentence is ${#first_sentence} characters; keep it 40-99"
fi
if printf '%s' "$fm_desc" | grep -q 'Use when'; then
  pass "description says when to use the skill"
else
  fail "description needs a 'Use when' clause"
fi
extra=$(printf '%s\n' "$keys" | grep -Ev '^(name|description|license|compatibility|metadata|allowed-tools)$' | grep -v '^$' || true)
if [ -z "$extra" ]; then
  pass "frontmatter uses only fields from the open spec"
else
  fail "harness-specific frontmatter fields: $(printf '%s' "$extra" | tr '\n' ' ')"
fi

echo "== progressive disclosure"
body_lines=$(awk 'BEGIN { n = 0 } /^---$/ { n++; next } n >= 2 { c++ } END { print c + 0 }' "$skill/SKILL.md")
body_words=$(awk 'BEGIN { n = 0 } /^---$/ { n++; next } n >= 2 { print }' "$skill/SKILL.md" | wc -w | tr -d ' ')
if [ "$body_lines" -lt 500 ] && [ "$body_words" -lt 3500 ]; then
  pass "SKILL.md body is $body_lines lines and $body_words words (limits 500 lines, ~5000 tokens)"
else
  fail "SKILL.md body is too long: $body_lines lines, $body_words words"
fi

# links <file>: the relative Markdown link targets in a file, without anchors.
links() { grep -o '](\([^)#:]*\)[)#]' "$1" | sed 's/^](//; s/[)#]$//' | grep -v '^$' || true; }

for file in "$skill"/SKILL.md "$skill"/references/*.md; do
  rel=${file#"$repo"/}
  for target in $(links "$file"); do
    if [ -f "$(dirname "$file")/$target" ]; then
      pass "$rel links to $target"
    else
      fail "$rel links to missing $target"
    fi
  done
done

for ref in "$skill"/references/*.md; do
  base=$(basename "$ref")
  if grep -q "references/$base" "$skill/SKILL.md"; then
    pass "SKILL.md points to references/$base"
  else
    fail "references/$base has no pointer in SKILL.md"
  fi
  if [ -z "$(links "$ref" | grep '\.md$')" ]; then
    pass "references/$base links to no other skill file (one level deep)"
  else
    fail "references/$base links onward; keep references one level deep"
  fi
  lines=$(wc -l < "$ref" | tr -d ' ')
  if [ "$lines" -le 100 ] || grep -qi '^## contents' "$ref"; then
    pass "references/$base is $lines lines (a contents list is required above 100)"
  else
    fail "references/$base is $lines lines with no '## Contents' list"
  fi
done

echo "== portability and privacy"
scan_paths=("$skill" "$repo/docs" "$repo/tests" "$repo/README.md" "$repo/AGENTS.md")
if grep -rEn --include='*.md' '[A-Za-z]:\\|\\[A-Za-z_]+\\' "$skill" > /dev/null; then
  fail "Windows-style backslash paths in the skill"
else
  pass "the skill uses forward-slash paths only"
fi
# Only generic patterns live here. Personal terms (employer, colleagues, private paths, tool names)
# go one regex per line in .privacy-patterns.local at the repo root. It's gitignored, so the scan
# never publishes the very terms it guards against.
generic='/Users/[A-Za-z]|/home/[A-Za-z]|[A-Za-z0-9._%+-]+@[a-z0-9-]+\.(com|io|org|net)\b'
local_terms=""
if [ -f "$repo/.privacy-patterns.local" ]; then
  local_terms=$(grep -Ev '^[[:space:]]*(#|$)' "$repo/.privacy-patterns.local" | paste -sd '|' -)
  pass "loaded personal patterns from .privacy-patterns.local"
else
  skip "no .privacy-patterns.local; scanning generic patterns only"
fi
private=$(grep -rEnI -i --exclude-dir=__pycache__ "$generic${local_terms:+|$local_terms}" \
  "${scan_paths[@]}" "$repo/scripts" "$repo/.gitignore" "$repo/LICENSE" 2> /dev/null \
  | grep -v "^$repo/scripts/check.sh:.*generic=" || true)
if [ -z "$private" ]; then
  pass "no personal paths, private terms or email addresses in the repo's files"
else
  fail "private details found:"
  printf '%s\n' "$private" | sed 's/^/      /'
fi

echo "== skills CLI"
if command -v skills > /dev/null 2>&1; then
  listed=$(skills add "$repo" --list 2>&1)
  rc=$?
  if [ "$rc" -eq 0 ] && printf '%s\n' "$listed" | grep -q 'Found 1 skill' \
    && printf '%s\n' "$listed" | grep -Eq "^[^A-Za-z0-9/]*$name[[:space:]]*$"; then
    pass "skills add --list parses and discovers $name"
  else
    fail "skills add --list did not discover $name (exit $rc)"
  fi
else
  skip "skills CLI not installed"
fi

echo "== script tests"
if command -v uv > /dev/null 2>&1; then
  if test_out=$(PYTHONDONTWRITEBYTECODE=1 uv run --no-project --with pytest pytest -q -p no:cacheprovider "$repo/tests" 2>&1); then
    pass "pytest: $(printf '%s\n' "$test_out" | tail -n 1)"
  else
    fail "pytest failed:"
    printf '%s\n' "$test_out" | tail -n 20 | sed 's/^/      /'
  fi
else
  skip "uv not installed"
fi

echo
echo "failures=$failures skipped=$skipped"
[ "$failures" -eq 0 ]
