@echo off
rem dsh 原生后端校验包装器：需要 Node 22+（node:zlib zstd API）。
rem 推荐先手动 `nvm use 22`；本脚本也会在 nvm 目录存在 22.x 时直接使用它。
setlocal
set "NODE_EXE=node"
if exist "%APPDATA%\nvm\v22.21.1\node.exe" set "NODE_EXE=%APPDATA%\nvm\v22.21.1\node.exe"
"%NODE_EXE%" "%~dp0verify-dsh-backend.mjs" %*
endlocal & exit /b %errorlevel%
