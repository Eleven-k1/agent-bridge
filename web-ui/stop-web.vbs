Set sh = CreateObject("WScript.Shell")
Set fso = CreateObject("Scripting.FileSystemObject")
root = fso.GetParentFolderName(WScript.ScriptFullName)
pythonw = root & "\..\backend\.venv\Scripts\pythonw.exe"
If Not fso.FileExists(pythonw) Then pythonw = "pythonw.exe"
cmd = Chr(34) & pythonw & Chr(34) & " " & Chr(34) & root & "\server\launcher.py" & Chr(34) & " --stop"
sh.CurrentDirectory = root & "\.."
sh.Run cmd, 0, True
