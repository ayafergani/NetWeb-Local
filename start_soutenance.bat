@echo off
setlocal

cd /d "%~dp0"

set "PYTHON_EXE=%CD%\venv\Scripts\python.exe"
if not exist "%PYTHON_EXE%" set "PYTHON_EXE=python"

echo ============================================
echo   NetGuard - Mode Soutenance
echo ============================================
echo.
echo Installation des dependances backend...
"%PYTHON_EXE%" -m pip install -r "%CD%\Backend\requirements.txt"
if errorlevel 1 (
    echo.
    echo Echec de l'installation des dependances.
    pause
    exit /b 1
)

echo.
echo Demarrage du serveur web sur http://127.0.0.1:5000
start "" http://127.0.0.1:5000
"%PYTHON_EXE%" "%CD%\Backend\app.py"
