@echo off
rem Run the panel in a console window. Paths are relative to this file, so the folder can
rem live anywhere -- an absolute path here worked only on the machine it was written on.
cd /d "%~dp0"

if not exist ".venv\Scripts\python.exe" (
	echo .venv not found. Create it first:
	echo     python -m venv .venv
	echo     .venv\Scripts\python.exe -m pip install -r requirements-browser.txt
	pause
	exit /b 1
)

rem Only for the browser login, and only if you actually run a proxy: Cloudflare's Turnstile
rem challenge does not render from a bare exit IP (ADR-0012). Delete these two lines if you
rem have no proxy -- a wrong address here makes the browser login fail, HTTP check-ins do not
rem care either way.
if not defined CHECKIN_PROXY_URL set CHECKIN_PROXY_URL=http://127.0.0.1:7897

rem Bind loopback unless told otherwise. run.py defaults to 0.0.0.0, which is LAN-reachable,
rem and the panel has no login (ADR-0003).
if not defined PANEL_HOST set PANEL_HOST=127.0.0.1

".venv\Scripts\python.exe" run.py
