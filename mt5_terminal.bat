@echo off
REM Keep MT5 attached to the interactive ONLOGON session; wrapper avoids
REM schtasks quoting failures for Program Files paths (0x80070002).
start "" /wait "C:\Program Files\MetaTrader 5\terminal64.exe"
