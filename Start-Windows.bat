@echo off
REM Asterion Expedition launcher. Run from this folder or double-click.
REM NOTE: %* forwards arguments as received by cmd.exe. Shell metacharacters
REM such as & | < > ^ are interpreted by cmd.exe before they reach the game,
REM so quote such values. For full control prefer running
REM `py -3 bootstrap.py ...` (or `python bootstrap.py ...`) directly.
setlocal
cd /d "%~dp0"
if exist ".venv\Scripts\python.exe" goto local_python
where py >nul 2>&1
if errorlevel 1 goto no_py_launcher
REM Probe the `py -3` interpreter version first; fall through to `python`
REM when it is missing or outside the supported 3.10-3.14 range.
py -3 -c "import sys; raise SystemExit(0 if (3, 10) <= sys.version_info[:2] <= (3, 14) else 1)" >nul 2>&1
if errorlevel 1 goto no_py_launcher
goto py_launcher

::no_py_launcher
where python >nul 2>&1
if not errorlevel 1 goto python_launcher
echo Asterion Expedition requires 64-bit Python 3.10 through 3.14.
echo Install it from https://www.python.org/downloads/ and enable the Python launcher.
pause
exit /b 1

:local_python
".venv\Scripts\python.exe" bootstrap.py %*
set "ASTERION_EXIT=%ERRORLEVEL%"
goto finished

:py_launcher
py -3 bootstrap.py %*
set "ASTERION_EXIT=%ERRORLEVEL%"
goto finished

:python_launcher
python bootstrap.py %*
set "ASTERION_EXIT=%ERRORLEVEL%"
goto finished

:finished
if not "%ASTERION_EXIT%"=="0" echo The game stopped with code %ASTERION_EXIT%. See the message above.
if not "%ASTERION_EXIT%"=="0" pause
exit /b %ASTERION_EXIT%
