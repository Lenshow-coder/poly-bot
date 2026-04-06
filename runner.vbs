Option Explicit

Dim shell
Dim fso
Dim repoPath
Dim cmd
Dim venvPython

Set shell = CreateObject("WScript.Shell")
Set fso = CreateObject("Scripting.FileSystemObject")

repoPath = fso.GetParentFolderName(WScript.ScriptFullName)
venvPython = repoPath & "\.venv\Scripts\python.exe"

If fso.FileExists(venvPython) Then
    cmd = "cmd /c cd /d """ & repoPath & """ && """ & venvPython & """ runner\dashboard.py --port 8766 --open-browser"
Else
    cmd = "cmd /c cd /d """ & repoPath & """ && py -3 runner\dashboard.py --port 8766 --open-browser"
End If

' 0 = hidden window, False = do not wait for command completion
shell.Run cmd, 0, False
