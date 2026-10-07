#!/usr/bin/env bash
# Pre-publication privacy check.
#
# The real values are deliberately NOT recorded in this file. It only asserts
# that no personal identifiers are present anywhere in the tree.
#
# If you fork this and add your own hardware, keep your own identifiers in a
# local git-ignored file - never commit them.
#
# Exit 0 = clear, 1 = something needs resolving before publishing.

set -uo pipefail
cd "$(dirname "$0")/.."

# Values that are intentionally fake and safe to ship. Anything matching these
# is skipped (documented placeholders, not real identifiers).
ALLOWLIST=(
    'AA:BB:CC:DD:EE:FF'      # dummy Bluetooth address used in the OBD scripts
    '1N4AL3AP0GC123456'      # fabricated VIN in the mock ECU (not a real car)
)

EXCLUDES=(
    --exclude-dir=.git
    --exclude-dir=.pio
    --exclude-dir=__pycache__
    --exclude-dir=mtkclient
    --exclude-dir=backup
    --exclude-dir=logs
    --exclude="$(basename "$0")"
)

# Shapes that should never appear in a published tree:
#   hardware addresses, private network ranges, developer home paths, VINs
PATTERNS=(
    '([0-9A-Fa-f]{2}:[0-9A-Fa-f]{2}:){5}[0-9A-Fa-f]{2}'
    '\b100\.[0-9]{1,3}\.[0-9]{1,3}\.[0-9]{1,3}\b'
    '\b192\.168\.[0-9]{1,3}\.[0-9]{1,3}\b'
    '\b10\.[0-9]{1,3}\.[0-9]{1,3}\.[0-9]{1,3}\b'
    '/home/[a-z][a-z0-9_-]*/'
    '\b[1-9][A-HJ-NPR-Z0-9]{16}\b'
)

is_allowed() {
    local line="$1"
    for a in "${ALLOWLIST[@]}"; do
        case "$line" in *"$a"*) return 0 ;; esac
    done
    return 1
}

fail=0
for pat in "${PATTERNS[@]}"; do
    hits=$(grep -rInE "$pat" "${EXCLUDES[@]}" . 2>/dev/null || true)
    real=""
    if [ -n "$hits" ]; then
        while IFS= read -r line; do
            is_allowed "$line" || real+="$line"$'\n'
        done <<< "$hits"
    fi
    if [ -n "$real" ]; then
        echo "  HIT [$pat]"
        echo "$real" | sed 's/^/      /'
        fail=1
    else
        echo "  clean: $pat"
    fi
done

echo
if [ "$fail" -eq 0 ]; then
    echo "OK - no personal identifiers found."
    echo "Note: this catches shapes, not context. Read the diff before publishing."
else
    echo "BLOCKED - resolve the hits above before publishing."
fi
exit "$fail"
