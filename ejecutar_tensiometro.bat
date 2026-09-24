@echo off
title Estacion Medica de Diagnostico Cardiovascular
cd /d "%~dp0"
echo ======================================================================
echo   ESTACION MEDICA DE DIAGNOSTICO CARDIOVASCULAR
echo   Iniciando aplicacion de escritorio con filtrado adaptativo NLMS...
echo ======================================================================

if exist "tensiometro_reconstructed.exe" (
    echo Iniciando aplicacion ejecutable (.exe)...
    start "" "tensiometro_reconstructed.exe"
    exit /b 0
)

if exist "dist\tensiometro_reconstructed.exe" (
    echo Iniciando aplicacion ejecutable desde dist (.exe)...
    start "" "dist\tensiometro_reconstructed.exe"
    exit /b 0
)

echo Iniciando mediante interprete de Python...
python tensiometro_reconstructed.py
if %ERRORLEVEL% NEQ 0 (
    echo.
    echo Ocurrio un error al ejecutar la aplicacion. Verifique las dependencias de Python.
    pause
)
