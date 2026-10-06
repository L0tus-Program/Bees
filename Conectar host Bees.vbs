' Solicita somente diagnostico; confirmacao e revogacao ficam na interface.
Option Explicit
Dim shell, files, root, script, command, result
Set shell = CreateObject("WScript.Shell")
Set files = CreateObject("Scripting.FileSystemObject")
root = files.GetParentFolderName(WScript.ScriptFullName)
script = files.BuildPath(root, "scripts\connect-host.ps1")
command = "powershell.exe -NoProfile -ExecutionPolicy Bypass -File " & Chr(34) & script & Chr(34)
result = shell.Run(command, 0, True)
If result <> 0 Then
  MsgBox "Nao foi possivel preparar o diagnostico. Abra o Bees, configure seu acesso e confira a disponibilidade do runtime local nesta distribuicao.", vbExclamation, "Bees"
End If
