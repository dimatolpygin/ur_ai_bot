@echo off
chcp 65001 > nul
cd /d %~dp0
docker compose -f docker-compose.dev.yml up --build
pause
