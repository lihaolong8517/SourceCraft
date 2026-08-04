@echo off
chcp 65001 >nul
cd /d "%~dp0"
echo [SourceCraft Dev] 以包模式启动 (python -m video_downloader) ...
"C:\Users\Administrator\miniconda3\envs\sourcecraft_realesrgan_cuda\python.exe" -m video_downloader %*
