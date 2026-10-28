#!/usr/bin/env bash
# Split dist/images into multiple zip files, each under a size limit, so the
# repo can be pushed to GitHub without tripping its large-file warnings (50MB)
# or hard limit (100MB).
#
# Usage:
#   ./zip_images.sh            # zip dist/images -> dist/images_zips/images_NNN.zip
#   ./zip_images.sh --unzip    # restore dist/images from the zip parts
#
# Env overrides:
#   MAX_MB=45  SRC_DIR=dist/images  OUT_DIR=dist/images_zips
set -euo pipefail

cd "$(dirname "$0")"

MAX_MB="${MAX_MB:-45}"   # headroom under GitHub's 50MB warning
SRC_DIR="${SRC_DIR:-dist/images}"
OUT_DIR="${OUT_DIR:-dist/images_zips}"
MAX_BYTES=$((MAX_MB * 1024 * 1024))

if [[ "${1:-}" == "--unzip" ]]; then
    shopt -s nullglob
    parts=("$OUT_DIR"/images_*.zip)
    if (( ${#parts[@]} == 0 )); then
        echo "No zip parts found in $OUT_DIR" >&2
        exit 1
    fi
    # Paths inside the zips are relative to the parent of SRC_DIR
    dest="$(dirname "$SRC_DIR")"
    for z in "${parts[@]}"; do
        echo "Extracting $z"
        unzip -q -o "$z" -d "$dest"
    done
    echo "Restored $(find "$SRC_DIR" -type f | wc -l | tr -d ' ') files into $SRC_DIR"
    exit 0
fi

if [[ ! -d "$SRC_DIR" ]]; then
    echo "Source dir not found: $SRC_DIR" >&2
    exit 1
fi

rm -rf "$OUT_DIR"
mkdir -p "$OUT_DIR"
OUT_ABS="$(cd "$OUT_DIR" && pwd)"

tmp="$(mktemp -d)"
trap 'rm -rf "$tmp"' EXIT

# stat flags differ between macOS (BSD) and Linux (GNU)
if stat -f '%z' . >/dev/null 2>&1; then
    stat_fmt=(-f '%z %N')
else
    stat_fmt=(-c '%s %n')
fi

# List "size path" for every file, relative to the parent of SRC_DIR so the
# zips extract back into the same layout.
parent="$(dirname "$SRC_DIR")"
base="$(basename "$SRC_DIR")"
(cd "$parent" && find "$base" -type f -exec stat "${stat_fmt[@]}" {} + | sort -k2) > "$tmp/files.txt"

# Greedily pack files into numbered lists, starting a new list whenever the
# next file would push the running total past MAX_BYTES. JPEGs barely compress,
# so raw size is a safe upper bound on the zip size (plus small header overhead,
# covered by the headroom).
awk -v max="$MAX_BYTES" -v dir="$tmp" '
{
    size = $1
    path = substr($0, index($0, " ") + 1)
    if (n == 0 || total + size > max) {
        n++
        total = 0
    }
    total += size
    printf "%s\n", path > sprintf("%s/list_%03d.txt", dir, n)
}
' "$tmp/files.txt"

for list in "$tmp"/list_*.txt; do
    num="${list##*/list_}"
    num="${num%.txt}"
    zipfile="$OUT_ABS/images_${num}.zip"
    # -0: store only (images are already compressed); -X: drop extra attrs
    (cd "$parent" && zip -q -0 -X "$zipfile" -@ < "$list")
    count=$(wc -l < "$list" | tr -d ' ')
    size=$(du -h "$zipfile" | cut -f1)
    echo "images_${num}.zip  $count files  $size"
done

echo "Done. Zip parts are in $OUT_DIR"
echo "Remember to add '$SRC_DIR/' to .gitignore so the raw images aren't committed."
