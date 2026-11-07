@echo off
rem Chay mot lan thu thap + phan tich. Task Scheduler goi file nay theo lich.
chcp 65001 >nul
cd /d "%~dp0"
if not exist logs mkdir logs
echo ===== %date% %time% ===== >> logs\nhat-ky.log
python news_tool.py %* >> logs\nhat-ky.log 2>&1
