@echo off
chcp 65001 >nul
cd /d "%~dp0"
echo ============================================
echo   AlphaForge 选股打分策略回测
echo ============================================
echo.
set PYTHONPATH=src
python scripts\backtest_scoring.py
echo.
echo ============================================
echo   回测完成！结果在 output\scoring_backtest\
echo ============================================
pause