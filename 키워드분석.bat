@echo off
chcp 65001 >nul
setlocal EnableDelayedExpansion
set PYTHONUTF8=1
cd /d "%~dp0"

rem Python 찾기: py 런처 -> python (Microsoft Store 안내용 껍데기는 제외)
set "PY="
where py >nul 2>nul && py -3 --version >nul 2>nul && set "PY=py -3"
if not defined PY (
  python --version >nul 2>nul && set "PY=python"
)
if not defined PY (
  echo [!] Python 3 가 설치되어 있지 않아요.
  echo     설치: winget install Python.Python.3.12
  echo     또는 https://www.python.org/downloads/ 에서 받고,
  echo     설치할 때 "Add python.exe to PATH" 를 체크해 주세요.
  echo.
  pause
  exit /b 1
)

if not exist ".env" (
  echo [i] .env 파일이 없어요. 예시 데이터^(데모^)로 실행합니다.
  echo     실제로 쓰려면 README.md 의 'API 키 준비'를 따라 .env 를 만들어 주세요.
  echo.
  %PY% "도구\seo_analyze.py" --demo
) else (
  echo 비교할 키워드를 입력하세요 ^(여러 개는 쉼표로 구분^):
  set /p INPUT=
  set "ARGS="
  for %%K in ("!INPUT:,=" "!") do (
    set "K=%%~K"
    for /f "tokens=* delims= " %%T in ("!K!") do if not "%%T"=="" set ARGS=!ARGS! "%%T"
  )
  %PY% "도구\seo_analyze.py" --quick !ARGS!
)

if exist reports start "" explorer reports
echo.
echo 아무 키나 누르면 창이 닫힙니다.
pause >nul
