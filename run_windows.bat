@echo off
REM Double-click this file to rebuild and deploy the site.
cd /d "%~dp0"
python build_site.py --deploy
echo.
pause
