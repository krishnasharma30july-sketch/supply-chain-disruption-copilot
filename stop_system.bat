@echo off
rem Stops all containers. Your data (database, search index) is kept; run start_system.bat to start again.
cd /d "%~dp0"
set "PATH=%LOCALAPPDATA%\Programs\DockerDesktop\resources\bin;%ProgramFiles%\Docker\Docker\resources\bin;%PATH%"
docker compose --profile local-llm stop
echo.
echo Stopped. Your data is kept. Run start_system.bat to start again.
pause
