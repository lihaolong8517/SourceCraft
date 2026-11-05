#!/usr/bin/env bash
set -e
cd "$(dirname "$0")/../.."
cp .rollback/20261017_batch_A/video_downloader.py video_downloader.py
cp .rollback/20261017_batch_A/locales/en_US.json locales/en_US.json
rm -f batch_download_dialog.py
echo "[OK] 已回滚到 2026-10-17 15:28 的版本"
md5sum video_downloader.py
