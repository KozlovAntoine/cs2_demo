@echo off
chcp 65001 >nul
title Mise a jour de HLAE
echo Mise a jour de HLAE pour CS Demo Manager
echo.
python "%~dp0update_hlae.py" %*
echo.
pause
