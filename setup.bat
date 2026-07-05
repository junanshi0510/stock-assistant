@echo off
chcp 65001 >nul
cd /d %~dp0
echo ============================================
echo   金融投资助手 - 首次安装(只需运行一次)
echo ============================================
echo.

echo [1/4] 创建 Python 环境...
python -m venv venv
if errorlevel 1 ( echo 创建失败,请确认已安装 Python。 & pause & exit /b )

echo [2/4] 安装后端依赖...
call venv\Scripts\activate.bat
python -m pip install --upgrade pip
pip install -r backend\requirements.txt
if errorlevel 1 ( echo 后端依赖安装失败。 & pause & exit /b )

echo [3/4] 安装前端依赖...
cd frontend
call npm install
if errorlevel 1 ( echo 前端依赖安装失败,请确认已安装 Node.js。 & pause & exit /b )
cd ..

echo.
echo [4/4] 完成!以后双击 start.bat 即可启动。
pause
