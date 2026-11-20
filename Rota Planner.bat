@echo off
rem Windows: double-click to start Rota Planner. Its window shows whether it is
rem running, with Open, Start and Stop; closing the window stops the program.
cd /d "%~dp0"
where pyw >nul 2>nul
if %errorlevel%==0 (
  py -3 -c "import tkinter" >nul 2>nul
  if not errorlevel 1 (
    start "" pyw -3 run.py --window %*
    exit /b 0
  )
)
rem No window possible: Rota Planner runs in this window instead; keep it open
rem while you work, close it to stop the program.
where py >nul 2>nul
if %errorlevel%==0 (
  py -3 run.py %*
) else (
  python run.py %*
)
if errorlevel 1 (
  echo.
  echo Rota Planner could not start. Python 3 is needed: install it from https://www.python.org/downloads/
  echo ^(tick "Add python.exe to PATH" during the installation^), then start again.
  pause
)
