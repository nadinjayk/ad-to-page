@echo off
setlocal

cd /d "%~dp0"
set "APP_ROOT=%~dp0"
if "%APP_ROOT:~-1%"=="\" set "APP_ROOT=%APP_ROOT:~0,-1%"

echo ----------------------------------------
echo Design Transfer Launcher
echo ----------------------------------------

if not defined ASSET_MODE set "ASSET_MODE=official_web"
echo Asset mode: %ASSET_MODE%

if not exist ".venv\Scripts\python.exe" (
  echo Creating Python virtual environment...
  python -m venv .venv
  call ".venv\Scripts\activate.bat"
  python -m pip install --upgrade pip
)

call ".venv\Scripts\activate.bat"
if errorlevel 1 (
  echo Failed to activate the Python virtual environment.
  exit /b 1
)

echo Installing backend dependencies...
pip install -r requirements.txt

echo Installing Playwright Chromium...
python -m playwright install chromium

echo Installing frontend dependencies...
pushd frontend
call npm install
popd

echo Reclaiming local dev ports if needed...
powershell -NoProfile -ExecutionPolicy Bypass -Command ^
  "$ports = 8765, 5173; foreach ($port in $ports) { $conns = Get-NetTCPConnection -LocalPort $port -State Listen -ErrorAction SilentlyContinue; foreach ($conn in $conns) { try { Stop-Process -Id $conn.OwningProcess -Force -ErrorAction Stop } catch {} } }"

echo Starting backend on http://127.0.0.1:8765
start "Design Transfer Backend" cmd /k "cd /d ""%APP_ROOT%"" && call "".venv\Scripts\activate.bat"" && python -m uvicorn backend.main:app --host 127.0.0.1 --port 8765"

echo Starting frontend on http://127.0.0.1:5173
start "Design Transfer Frontend" cmd /k "cd /d ""%APP_ROOT%\frontend"" && call npm run dev -- --host 127.0.0.1 --port 5173 --strictPort"

echo Waiting for frontend to come online...
powershell -NoProfile -ExecutionPolicy Bypass -Command ^
  "$url = 'http://127.0.0.1:5173'; for ($i = 0; $i -lt 30; $i++) { try { Invoke-WebRequest -UseBasicParsing $url | Out-Null; Start-Process $url; exit 0 } catch { Start-Sleep -Seconds 1 } }; Start-Process $url"

echo.
echo Backend:  http://127.0.0.1:8765
echo Frontend: http://127.0.0.1:5173
echo.
echo If this is the first run, dependency installation may take a few minutes.
