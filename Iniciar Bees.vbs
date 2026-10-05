' Launcher Windows: sobe a aplicacao e abre o navegador sem janela de terminal.
Option Explicit
Dim shell, files, root, script, command, result
Set shell = CreateObject("WScript.Shell")
Set files = CreateObject("Scripting.FileSystemObject")
root = files.GetParentFolderName(WScript.ScriptFullName)
script = files.BuildPath(root, "scripts\start.ps1")
command = "powershell.exe -NoProfile -ExecutionPolicy Bypass -File " & Chr(34) & script & Chr(34)
MsgBox "O Bees vai preparar os containers e abrir seu navegador. Na primeira vez, isso pode levar alguns minutos. Mantenha o Docker Desktop aberto.", vbInformation, "Bees"
result = shell.Run(command, 0, True)
If result <> 0 Then
  MsgBox "Falha ao iniciar o Bees. Confira Docker Desktop aberto, porta 8080 livre e logs\startup.log.", vbExclamation, "Bees"
End If
