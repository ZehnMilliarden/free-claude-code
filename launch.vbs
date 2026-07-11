' Free Claude Code Client — launcher (zero console windows)
' Uses the uv virtual environment's pythonw.exe directly — no cmd, no uv console.
' WScript.Shell.Run 0 = hidden window (CREATE_NO_WINDOW / DETACHED_PROCESS)

Dim shell, fso, projectDir, pythonw
Set fso = CreateObject("Scripting.FileSystemObject")
projectDir = fso.GetParentFolderName(WScript.ScriptFullName)
pythonw = fso.BuildPath(fso.BuildPath(projectDir, ".venv\Scripts"), "pythonw.exe")

If Not fso.FileExists(pythonw) Then
    MsgBox "Cannot find " & pythonw & "." & vbCrLf & vbCrLf & "Run 'uv sync' first to create the virtual environment.", vbExclamation, "Free Claude Code Client"
    WScript.Quit 1
End If

Set shell = CreateObject("WScript.Shell")
shell.CurrentDirectory = projectDir
shell.Run """" & pythonw & """ -m client", 0, False
