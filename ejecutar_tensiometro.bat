@echo off
title Estacion Medica de Diagnostico Cardiovascular v2.0
cd /d "%~dp0"
echo ======================================================================
echo   ESTACION MEDICA DE DIAGNOSTICO CARDIOVASCULAR - VERSION 2.0
echo   Iniciando software de escritorio PyQt6 con filtrado adaptativo NLMS...
echo ======================================================================
python tensiometro_reconstructed.py
if %ERRORLEVEL% NEQ 0 (
    echo.
    echo Ocurrio un error al ejecutar la aplicacion. Verifique las dependencias de Python.
    pause
)
