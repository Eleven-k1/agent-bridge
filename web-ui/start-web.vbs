Set sh = CreateObject("WScript.Shell")
root = CreateObject("Scripting.FileSystemObject").GetParentFolderName(WScript.ScriptFullName)
sh.CurrentDirectory = root & "\.."
pythonw = root & "\..\backend\.venv\Scripts\pythonw.exe"
If Not CreateObject("Scripting.FileSystemObject").FileExists(pythonw) Then pythonw = "pythonw.exe"
cmd = Chr(34) & pythonw & Chr(34) & " " & Chr(34) & root & "\server\launcher.py" & Chr(34)
sh.Run cmd, 0, False
