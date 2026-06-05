' Nenova bidirectional monitoring - hidden launcher.
' Runs the monitor with NO console window. Only the bottom-right STOP button shows.
' Logs: logs\monitor_live.log   Stop: Ctrl+Alt+Q / red STOP button / stop_nenova.bat
Dim sh : Set sh = CreateObject("WScript.Shell")
sh.Run "cmd /c ""C:\Users\USER\nenova_agent\.claude\worktrees\cranky-yalow-f3379c\_monitor_core.bat""", 0, False
