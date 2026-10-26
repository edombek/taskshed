#!/usr/bin/env bash
# Render every Mermaid source in docs/diagrams/ to a committed PNG
# (the PNGs are embedded in README and docs/en|ru pages).
#
# Requires Node.js and a Chromium binary; override with:
#   PUPPETEER_EXECUTABLE_PATH=/path/to/chromium scripts/render_diagrams.sh
set -euo pipefail
cd "$(dirname "$0")/.."

CFG="$(mktemp)"
trap 'rm -f "$CFG"' EXIT
cat > "$CFG" <<JSON
{"executablePath": "${PUPPETEER_EXECUTABLE_PATH:-/usr/bin/chromium}",
 "headless": "shell",
 "args": ["--no-sandbox", "--disable-gpu", "--disable-dev-shm-usage"]}
JSON

for f in docs/diagrams/*.mmd; do
  echo "rendering $f"
  npx -y @mermaid-js/mermaid-cli@11 -i "$f" -o "${f%.mmd}.png" -b white -p "$CFG"
done
