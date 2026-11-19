' AI Chat Nonstop - silent launcher (no console window)
' Double-click to start the app; the window opens on its own.
Option Explicit
Dim sh, fso, base
Set sh = CreateObject("WScript.Shell")
Set fso = CreateObject("Scripting.FileSystemObject")
base = fso.GetParentFolderName(WScript.ScriptFullName)
sh.CurrentDirectory = base
On Error Resume Next
sh.Run "pythonw """ & base & "\启动.py""", 1, False
If Err.Number <> 0 Then
  MsgBox "Failed to start: pythonw not found in PATH." & vbCrLf & _
         "Install Python 3.10+ and tick 'Add python.exe to PATH'.", 16, "AI Chat Nonstop"
  WScript.Quit 1
End If
WScript.Quit 0
