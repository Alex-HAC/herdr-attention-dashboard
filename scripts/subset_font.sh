#!/usr/bin/env bash
# v0.9.8.7 (load speed): the dashboard serves a subset of Inter (Latin, Latin Extended-A, Greek,
# punctuation, arrows, maths, box drawing and symbols; every weight, variable axes kept): 352 KB -> about 179 KB.
# Characters outside it (Cyrillic, CJK, ...) fall back to the system font. The full font stays in
# third_party/inter/ (SIL OFL 1.1, no Reserved Font Name, so the subset may keep the name Inter).
# Needs uv (fonttools is fetched for this run only). Usage: scripts/subset_font.sh
set -euo pipefail
cd "$(dirname "$0")/.."
UNICODES="U+0000-017F,U+0192,U+0218-021B,U+02B0-02FF,U+0300-030C,U+0370-03FF,U+2000-206F,U+2070-209F,U+20A0-20CF,U+2100-214F,U+2190-21FF,U+2200-22FF,U+2300-23FF,U+2500-25FF,U+2600-26FF,U+2700-27BF"
uvx --from 'fonttools[woff]' pyftsubset third_party/inter/InterVariable.woff2 --unicodes="$UNICODES" \
  --layout-features='*' --flavor=woff2 --output-file=herdr_attention/web/InterVariable.woff2
ls -l herdr_attention/web/InterVariable.woff2
