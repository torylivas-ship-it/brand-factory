#!/usr/bin/env bash
set -euo pipefail
cd /home/toe/brand-factory-backend
export STRIPE_SECRET_KEY="$(railway variables --kv | grep '^STRIPE_SECRET_KEY=' | cut -d= -f2-)"
if [ -z "$STRIPE_SECRET_KEY" ]; then
  echo "Could not read STRIPE_SECRET_KEY from railway variables --kv" >&2
  exit 1
fi
python3 create_bfn_ops_prices.py
