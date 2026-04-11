Option Explicit

Dim shell
Dim fso
Dim repoPath
Dim cmd
Dim venvPython
Dim cleanupCmd
Dim procPatternPath
Dim procPatternModule

Set shell = CreateObject("WScript.Shell")
Set fso = CreateObject("Scripting.FileSystemObject")

repoPath = fso.GetParentFolderName(WScript.ScriptFullName)
venvPython = repoPath & "\.venv\Scripts\pythonw.exe"
procPatternPath = repoPath & "\runner\dashboard.py"
procPatternModule = "runner.dashboard"

' Stop stale dashboard processes for this repo so latest code opens reliably.
cleanupCmd = "powershell -NoProfile -WindowStyle Hidden -Command ""Get-CimInstance Win32_Process | Where-Object { ($_.CommandLine -like '*" & procPatternPath & "*') -or ($_.CommandLine -like '*" & procPatternModule & "*') } | ForEach-Object { Stop-Process -Id $_.ProcessId -Force -ErrorAction SilentlyContinue }"""
shell.Run cleanupCmd, 0, True

shell.CurrentDirectory = repoPath

If fso.FileExists(venvPython) Then
    cmd = """" & venvPython & """ -m runner.dashboard --port 8766 --open-browser"
Else
    cmd = "py -3 -m runner.dashboard --port 8766 --open-browser"
End If

' 0 = hidden window, False = do not wait for command completion
shell.Run cmd, 0, False
