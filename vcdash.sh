#!/bin/sh
# 掃描同一層目錄的應用程式並產生報告
cd "$(dirname "$0")" || exit 1
python3 vcdash.py scan --report "$@"
