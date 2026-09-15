@echo off
REM Lance AutoRush depuis les sources, sans construire d'executable.
setlocal
cd /d "%~dp0\..\.."
py -m autorush gui %*
endlocal
