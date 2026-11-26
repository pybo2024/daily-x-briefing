@echo off
cd /d "%~dp0"
set "PATH=%USERPROFILE%\.local\bin;%PATH%"
echo ============================================
echo   Daily X Briefing Video Generator (4:5)
echo ============================================
uv run python build_video.py %*
if errorlevel 1 (
    echo [ERROR] Build failed.
    pause
    exit /b 1
)
echo.
echo [SUCCESS] Check the output\ folder for today's MP4!
pause
