@echo off
cd /d "%~dp0"
set PYTHONPATH=%~dp0src
rem Prefer a Python that actually ships with tkinter; some stripped-down
rem Python installs on PATH lack it and would fail the GUI tests.
python -c "import tkinter" >nul 2>&1
if not errorlevel 1 (
    python -m unittest discover -s tests -v
) else (
    py -3 -c "import tkinter" >nul 2>&1
    if errorlevel 1 (
        echo No Python with tkinter found. Please install Python 3.12+ with tcl/tk.
        pause
        exit /b 1
    )
    py -3 -m unittest discover -s tests -v
)
pause
