' hidden launcher - runs a console program with NO visible window
' usage: wscript.exe //nologo hidden_run.vbs "C:\path\prog.exe" "arg1" "arg2" ...
' window style 0 = hidden, bWaitOnReturn = True keeps task state accurate
Option Explicit
Dim sh, i, n, cmd, a
Set sh = CreateObject("WScript.Shell")
n = WScript.Arguments.Count
If n < 1 Then
  WScript.Quit 2
End If
cmd = """" & WScript.Arguments(0) & """"
For i = 1 To n - 1
  a = WScript.Arguments(i)
  If InStr(a, " ") > 0 Then
    a = """" & a & """"
  End If
  cmd = cmd & " " & a
Next
On Error Resume Next
sh.Run cmd, 0, True
If Err.Number <> 0 Then
  WScript.Quit 3
End If
WScript.Quit 0
