@echo off
chcp 65001 > nul
title BusinessApp - Serveur Flask
color 0A

echo.
echo ==============================================
echo   BusinessApp - Demarrage du backend Flask
echo ==============================================
echo.

cd /d "%~dp0"

if exist "backend\app.py" (
    cd backend
    echo [OK] Dossier backend detecte
) else if exist "app.py" (
    echo [OK] app.py trouve dans le dossier courant
) else (
    echo [ERREUR] app.py introuvable
    echo Placez ce fichier .bat dans le dossier racine du projet.
    pause
    exit /b 1
)

echo [INFO] Verification de Python...
python --version 2>nul
if errorlevel 1 (
    echo [ERREUR] Python n'est pas installe ou absent du PATH.
    pause
    exit /b 1
)

echo.
echo [INFO] Installation des dependances (si necessaire)...
pip install flask flask-cors flask_login authlib pymysql sqlalchemy pandas openpyxl werkzeug -q

echo.
echo [OK] Lancement de Flask sur http://127.0.0.1:5000
echo.
echo Ouvrez dans le navigateur:
echo   http://127.0.0.1:5000/index.html
echo.

start "" "http://127.0.0.1:5000/index.html"
python app.py

echo.
echo [INFO] Flask s'est arrete. Appuyez sur une touche pour fermer.
pause
