@echo off
setlocal

set "PROJECT_DIR=%~dp0"
cd /d "%PROJECT_DIR%"

if not exist ".venv\Scripts\python.exe" (
    echo [ERROR] Khong tim thay Python trong .venv.
    pause
    exit /b 1
)

if not exist "backend\app\daily_api.py" (
    echo [ERROR] Khong tim thay backend\app\daily_api.py.
    pause
    exit /b 1
)

if not exist "frontend\package.json" (
    echo [ERROR] Khong tim thay frontend\package.json.
    pause
    exit /b 1
)

where npm.cmd >nul 2>&1
if errorlevel 1 (
    echo [ERROR] Khong tim thay npm. Hay kiem tra Node.js.
    pause
    exit /b 1
)

if not exist "frontend\node_modules" (
    echo [ERROR] Frontend chua duoc cai thu vien.
    echo Chay npm.cmd ci trong thu muc frontend.
    pause
    exit /b 1
)

set "PYTHONIOENCODING=utf-8"

echo Dang mo backend va frontend...
echo Backend: backend.app.daily_api:app

start "Warehouse - Backend" /D "%PROJECT_DIR%" cmd /k ".venv\Scripts\python.exe -m uvicorn backend.app.daily_api:app --host 127.0.0.1 --port 8000 --no-access-log"

start "Warehouse - Frontend" /D "%PROJECT_DIR%frontend" cmd /k "npm.cmd run dev -- --host 127.0.0.1 --port 5173 --strictPort"

echo.
echo Giao dien: http://127.0.0.1:5173
echo API:       http://127.0.0.1:8000/docs
echo.
echo Giu hai cua so Backend va Frontend trong khi su dung.
echo Chi chay file khoi dong mot lan.
echo Dung du an: nhan Ctrl+C trong tung cua so.
echo.
pause
endlocal