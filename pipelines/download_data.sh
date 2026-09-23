#!/usr/bin/env bash
# Download the raw Amazon Reviews 2018 data used by Toko Marcell.
#
# Source: McAuley Lab, UCSD
#   https://cseweb.ucsd.edu/~jmcauley/datasets/amazon_v2/
# Category: Clothing, Shoes & Jewelry
#   reviews  = 5-core version (every item and user has >= 5 reviews)
#   metadata = all items in the category (we filter it ourselves later)
#
# Research / non-commercial use only. Raw files are ~2.8 GB and are
# .gitignore'd — see pipelines/README.md for provenance and licence.
#
# Usage:  bash pipelines/download_data.sh
set -euo pipefail

RAW_DIR="$(cd "$(dirname "$0")/.." && pwd)/data/raw"
BASE="https://mcauleylab.ucsd.edu/public_datasets/data/amazon_v2"
mkdir -p "$RAW_DIR"

fetch() { # fetch <url> <filename>
  echo "==> $2"
  # -C - resumes a partial file instead of starting over
  curl -fL --retry 3 --retry-delay 2 -C - -o "$RAW_DIR/$2" "$1"
}

# 1.27 GB — user_id / asin / rating / timestamp / review text
fetch "$BASE/categoryFilesSmall/Clothing_Shoes_and_Jewelry_5.json.gz" \
      "reviews_clothing_5core.json.gz"

# 1.57 GB — title / brand / price / categories / features / images
fetch "$BASE/metaFiles2/meta_Clothing_Shoes_and_Jewelry.json.gz" \
      "meta_clothing.json.gz"

echo
echo "Raw files in $RAW_DIR:"
ls -la "$RAW_DIR"
