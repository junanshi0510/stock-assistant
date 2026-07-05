@echo off
chcp 65001 >nul
cd /d %~dp0

echo 正在启动金融投资助手...
echo (会弹出两个黑色窗口,分别是后端和前端,使用期间请勿关闭)
echo.

REM 启动后端 API(端口 8000)
start "金融助手-后端" cmd /k "call venv\Scripts\activate.bat && cd backend && uvicorn main:app --port 8000"

REM 启动前端界面(端口 5173)
start "金融助手-前端" cmd /k "cd frontend && npm run dev"

REM 等几秒让服务起来,然后打开浏览器
timeout /t 6 >nul
start http://localhost:5173

echo 浏览器已打开 http://localhost:5173
echo 用完后关闭那两个黑色窗口即可停止。
