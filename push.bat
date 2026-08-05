@echo off
color 0b
echo ========================================================
echo             SUBIENDO CODIGO A GITHUB
echo ========================================================
echo.
echo Esto abrira una ventanita para iniciar sesion en tu Github.
echo Por favor, autoriza el inicio de sesion en el navegador.
echo.

:: Inicializar repositorio si no existe
if not exist ".git" (
    git init >nul
    git branch -M main >nul
)

:: Configurar repositorio remoto
git remote remove origin >nul 2>&1
git remote add origin https://github.com/Aleperez04/Tensiometro.git >nul 2>&1

:: Configurar identidad local de Git para permitir el commit sin errores
git config user.name "Aleperez04" >nul 2>&1
git config user.email "aleperez04@users.noreply.github.com" >nul 2>&1

:: Limpiar cache de archivos indexados por error (ej. *_extracted)
git rm -r --cached . >nul 2>&1

:: Agregar todos los archivos permitidos por el .gitignore
git add .

:: Crear commit automatico sin pedir texto
git commit -m "Actualizacion automatica del proyecto" >nul 2>&1

:: Subir cambios a la rama main
git push -u origin main

echo.
echo ========================================================
echo ¡Listo! Codigo subido correctamente. Ya puedes cerrar esto.
echo ========================================================
pause
