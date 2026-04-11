#!/usr/bin/env bash
# Download official IRS fillable PDFs from irs.gov.
# Writes into tax_dashboard/filing/templates/.
# Run from any directory; paths resolved relative to this script.

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
DEST="$SCRIPT_DIR/../filing/templates"
mkdir -p "$DEST"

FORMS=(
  "f1040.pdf"       # Form 1040 — U.S. Individual Income Tax Return
  "f1040sc.pdf"     # Schedule C — Profit or Loss from Business
  "f1040sse.pdf"    # Schedule SE — Self-Employment Tax
  "f1040s1.pdf"     # Schedule 1 — Additional Income and Adjustments
  "f8829.pdf"       # Form 8829 — Home Office
  "f4562.pdf"       # Form 4562 — Depreciation and Amortization
)

for form in "${FORMS[@]}"; do
  url="https://www.irs.gov/pub/irs-pdf/$form"
  dest="$DEST/$form"
  if [ -f "$dest" ]; then
    echo "✓ $form (already present)"
    continue
  fi
  echo "Fetching $url …"
  if curl -fsSL "$url" -o "$dest.tmp"; then
    mv "$dest.tmp" "$dest"
    shasum -a 256 "$dest" | awk '{print "  sha256:", $1}'
  else
    echo "  ✗ failed to fetch $form — will need to be placed manually"
    rm -f "$dest.tmp"
  fi
done

echo
echo "Forms in $DEST:"
ls -la "$DEST"
