@echo off
setlocal enabledelayedexpansion
title anime-sh

REM One-click launcher for Windows.
REM
REM Double-click this file. It installs what's missing (nothing but uv, which is
REM a single standalone binary), then starts anime-sh. Re-running it later just
REM starts the app.
REM
REM Deliberately does NOT need Python: uv brings its own. That is the whole
REM reason this file exists — pip and pipx are themselves Python packages, so
REM they cannot be the first thing you install on a clean machine.

echo.
echo   anime-sh
echo   ========
echo.

REM ---- 1. already installed? -------------------------------------------------
where anime >nul 2>&1
if %errorlevel%==0 goto :run

REM ---- 2. do we have uv? -----------------------------------------------------
where uv >nul 2>&1
if %errorlevel%==0 goto :install

echo   uv is not installed. It is a single small program that installs
echo   anime-sh (and brings its own Python, so you do not need Python).
echo.
set /p REPLY=  Install uv now? [Y/n]:
if /i "!REPLY!"=="n" goto :cancelled

echo.
echo   Installing uv...
where winget >nul 2>&1
if %errorlevel%==0 (
    winget install --id astral-sh.uv -e --accept-source-agreements --accept-package-agreements
) else (
    powershell -NoProfile -ExecutionPolicy Bypass -Command "irm https://astral.sh/uv/install.ps1 | iex"
)

REM winget/installer updates PATH for *new* shells; pick it up for this one too.
set "PATH=%USERPROFILE%\.local\bin;%LOCALAPPDATA%\Microsoft\WinGet\Links;%PATH%"

where uv >nul 2>&1
if %errorlevel% neq 0 (
    echo.
    echo   uv was installed but this window cannot see it yet.
    echo   Close this window, open it again, and re-run this file.
    echo.
    pause
    exit /b 1
)

REM ---- 3. install anime-sh ---------------------------------------------------
:install
echo.
echo   Installing anime-sh (this takes a minute the first time)...
echo.
uv tool install "anime-sh[tui]" --quiet
if %errorlevel% neq 0 (
    echo.
    echo   Install failed. Please copy the message above when reporting it.
    echo.
    pause
    exit /b 1
)
set "PATH=%USERPROFILE%\.local\bin;%PATH%"

REM ---- 4. is there a video player? ------------------------------------------
where mpv >nul 2>&1
if %errorlevel% neq 0 (
    echo.
    echo   mpv is missing - that is the video player anime-sh uses to play
    echo   episodes. Everything else works without it.
    echo.
    set /p REPLY=  Install mpv now? [Y/n]:
    if /i not "!REPLY!"=="n" (
        where winget >nul 2>&1
        if !errorlevel!==0 (
            winget install --id shinchiro.mpv -e --accept-source-agreements --accept-package-agreements
        ) else (
            echo   No winget found. Install mpv yourself from https://mpv.io
            pause
        )
    )
)

REM ---- 5. go ----------------------------------------------------------------
:run
set "PATH=%USERPROFILE%\.local\bin;%PATH%"
echo.
anime %*
if %errorlevel% neq 0 (
    echo.
    echo   anime-sh exited with an error. Run this to check your setup:
    echo       anime doctor
    echo.
    pause
)
exit /b 0

:cancelled
echo.
echo   Cancelled. Nothing was installed.
echo.
pause
exit /b 1
