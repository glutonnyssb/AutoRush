@echo off
REM ---------------------------------------------------------------------------
REM Construction de l'executable Windows d'AutoRush.
REM
REM   build\windows\build.bat
REM
REM Resultat : build\windows\dist\AutoRush\AutoRush.exe
REM ---------------------------------------------------------------------------
setlocal

cd /d "%~dp0\..\.."

echo.
echo === AutoRush : construction Windows ===
echo.

where py >nul 2>nul
if errorlevel 1 (
    echo [ERREUR] Python est introuvable. Installez Python 3.11 ou plus recent
    echo          depuis https://www.python.org/downloads/windows/
    echo          en cochant "Add python.exe to PATH".
    exit /b 1
)

echo [1/4] Environnement virtuel...
if not exist ".venv-build" (
    py -m venv .venv-build || exit /b 1
)
call .venv-build\Scripts\activate.bat

echo [2/4] Dependances...
py -m pip install --upgrade pip --quiet || exit /b 1
py -m pip install -r requirements-dev.txt --quiet || exit /b 1

echo [3/4] Tests...
py -m pytest -q
if errorlevel 1 (
    echo.
    echo [ATTENTION] Des tests ont echoue. La construction continue,
    echo             mais verifiez le resultat avant de distribuer.
    echo.
)

echo [4/4] Construction de l'executable...
py -m PyInstaller build\windows\AutoRush.spec --noconfirm ^
    --distpath build\windows\dist --workpath build\windows\work || exit /b 1

echo.
echo === Termine ===
echo Executable : build\windows\dist\AutoRush\AutoRush.exe
echo Ligne de commande : build\windows\dist\AutoRush\autorush-cli.exe
echo.
echo N'oubliez pas ffmpeg : soit installe sur la machine (winget install Gyan.FFmpeg),
echo soit copie dans build\windows\dist\AutoRush\ffmpeg\bin\ffmpeg.exe
echo.

endlocal
