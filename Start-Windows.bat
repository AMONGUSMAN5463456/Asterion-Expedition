@echo off
setlocal
cd /d "%~dp0"
if exist ".venv\Scripts\python.exe" goto local_python
where py >nul 2>&1
if not errorlevel 1 goto py_launcher
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
