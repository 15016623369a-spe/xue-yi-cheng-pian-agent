#!/bin/zsh
cd -- "$(dirname -- "$0")" || exit 1
exec python3 scripts/start_workbench.py
