@echo off
title FKey - Build de la version portable
cd /d "%~dp0"
echo.
echo ==========================================
echo   Build FKey (correcteur local) - portable
echo ==========================================
echo.

:: Environnement virtuel : celui du dossier parent (zora6 local\venv) ou local
if exist "venv\Scripts\activate.bat" (
    call "venv\Scripts\activate.bat"
) else if exist "..\venv\Scripts\activate.bat" (
    call "..\venv\Scripts\activate.bat"
) else (
    echo ERREUR : aucun venv trouve. Creez-le : python -m venv venv ^&^& venv\Scripts\pip install -r requirements.txt
    pause & exit /b 1
)

echo [1/4] Dependances...
pip install -q -r requirements.txt

echo [2/4] Nettoyage...
if exist "dist\FKey" rmdir /s /q "dist\FKey"
if exist "build"     rmdir /s /q "build"

echo [3/4] Compilation PyInstaller (1-2 min)...
pyinstaller ^
  --onedir ^
  --windowed ^
  --name "FKey" ^
  --icon "fkey.ico" ^
  --add-data "fkey.ico;." ^
  --add-data "fkey_tray.ico;." ^
  --add-data "fkey_logo.png;." ^
  --hidden-import "pystray._win32" ^
  --hidden-import "pynput.keyboard._win32" ^
  --hidden-import "pynput.mouse._win32" ^
  --collect-all "pystray" ^
  --collect-all "pynput" ^
  --noconfirm ^
  fkey.py
if errorlevel 1 (
    echo ERREUR : la compilation a echoue.
    pause & exit /b 1
)

echo [4/4] Moteur llama.cpp + fichiers annexes...
xcopy /Y /I /Q "llama\*.exe" "dist\FKey\llama\" >nul
xcopy /Y /I /Q "llama\*.dll" "dist\FKey\llama\" >nul
copy /Y "LISEZMOI.txt" "dist\FKey\" >nul
if exist "dist\FKey\config.json"  del /q "dist\FKey\config.json"
if exist "dist\FKey\zora_log.txt" del /q "dist\FKey\zora_log.txt"

:: Les modeles ne sont pas inclus (3 Go) : l'assistant de premier lancement les telecharge.
:: Pour une cle USB / un reseau sans internet, copiez models\*.gguf dans dist\FKey\models\.
if exist "models\*.gguf" (
    set /p COPYM="Inclure les modeles deja telecharges dans le build ? (o/N) : "
)
if /i "%COPYM%"=="o" xcopy /Y /I /Q "models\*.gguf" "dist\FKey\models\"

:: Archive prete a distribuer
echo Creation de l'archive zip...
if exist "dist\FKey-portable.zip" del /q "dist\FKey-portable.zip"
powershell -NoProfile -Command "Compress-Archive -Path 'dist\FKey' -DestinationPath 'dist\FKey-portable.zip' -CompressionLevel Optimal"

echo.
echo ==========================================
echo   Termine : dist\FKey\FKey.exe
echo             dist\FKey-portable.zip
echo ==========================================
echo Aucun droit administrateur requis : dezipper, lancer FKey.exe.
echo.
pause
