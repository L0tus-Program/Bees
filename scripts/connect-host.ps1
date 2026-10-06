param(
    [switch]$NoBrowser,
    [switch]$NoDialog,
    [ValidatePattern('^[a-z0-9][a-z0-9_-]*$')][string]$ProjectName = 'bees',
    [ValidateRange(1, 65535)][int]$Port = 8080
)
$ErrorActionPreference = 'Stop'
$hostTaskRoot = Split-Path -Parent $PSScriptRoot
$hostTaskPython = Join-Path $hostTaskRoot '.venv\Scripts\python.exe'
$hostTaskExecutable = Join-Path $hostTaskRoot 'runtime\bees-host\bees-host.exe'
if (-not (Test-Path -LiteralPath (Split-Path -Parent $hostTaskExecutable))) {
    $hostTaskExecutable = Join-Path $hostTaskRoot 'dist\host-windows\bees-host-windows\runtime\bees-host\bees-host.exe'
    if (-not (Test-Path -LiteralPath (Split-Path -Parent $hostTaskExecutable))) {
        $hostTaskExecutable = $hostTaskPython
    }
}
$hostTaskPrefix = @()
if ($hostTaskExecutable -eq $hostTaskPython) { $hostTaskPrefix = @('-m', 'bees_host') }
$hostTaskState = Join-Path $hostTaskRoot "data\host-links\$ProjectName-$Port"
$hostTaskBootstrap = $null
$hostTaskPreviousPort = $env:BEES_HTTP_PORT
$hostTaskFailure = 'Não foi possível conectar o diagnóstico. Confira se o Bees está aberto e seu acesso já foi configurado.'

function Set-HostPrivateDirectory([string]$Directory) {
    # Não seguir junctions/reparse points ao criar arquivos com convite privado.
    $hostTaskCursor = [System.IO.DirectoryInfo]::new([System.IO.Path]::GetFullPath($Directory))
    while ($hostTaskCursor) {
        if ($hostTaskCursor.Exists -and ($hostTaskCursor.Attributes -band [System.IO.FileAttributes]::ReparsePoint)) {
            throw 'Diretório do vínculo inválido.'
        }
        $hostTaskCursor = $hostTaskCursor.Parent
    }
    if (Test-Path -LiteralPath $Directory) {
        & $hostTaskExecutable @hostTaskPrefix --check-private $Directory --directory *> $null
        if ($LASTEXITCODE -ne 0) { throw 'Diretório existente não é privado.' }
        return
    }
    [System.IO.Directory]::CreateDirectory($Directory) | Out-Null
    $hostTaskSid = [System.Security.Principal.WindowsIdentity]::GetCurrent().User
    $hostTaskAcl = [System.Security.AccessControl.DirectorySecurity]::new()
    $hostTaskAcl.SetOwner($hostTaskSid)
    $hostTaskAcl.SetAccessRuleProtection($true, $false)
    $hostTaskAcl.AddAccessRule([System.Security.AccessControl.FileSystemAccessRule]::new(
        $hostTaskSid, 'FullControl', 'ContainerInherit,ObjectInherit', 'None', 'Allow'
    ))
    Set-Acl -LiteralPath $Directory -AclObject $hostTaskAcl
}

try {
    if (-not (Test-Path -LiteralPath $hostTaskExecutable -PathType Leaf)) {
        $hostTaskFailure = 'O runtime local de diagnóstico não está instalado nesta distribuição. A conversa por Docker continua disponível.'
        throw 'Runtime local ausente.'
    }
    # A instalação padrão nunca é configurada com uma identidade de teste.
    $env:BEES_HTTP_PORT = "$Port"
    $hostTaskCompose = @('compose', '--project-name', $ProjectName, '--project-directory', $hostTaskRoot, '-f', (Join-Path $hostTaskRoot 'compose.yaml'))
    Set-HostPrivateDirectory $hostTaskState
    $hostTaskPublic = Join-Path $hostTaskState 'public-status.json'
    $hostTaskStatus = $null
    if (Test-Path -LiteralPath $hostTaskPublic -PathType Leaf) {
        & $hostTaskExecutable @hostTaskPrefix --check-private $hostTaskPublic *> $null
        if ($LASTEXITCODE -ne 0) { throw 'Estado público não é privado.' }
        try {
            $hostTaskCandidate = Get-Content -LiteralPath $hostTaskPublic -Raw | ConvertFrom-Json
            if ($hostTaskCandidate.fingerprint -match '^[A-F0-9]{4}(-[A-F0-9]{4}){3}$') {
                $hostTaskStatus = $hostTaskCandidate
            }
        } catch { $hostTaskStatus = $null }
    }
    # Runtime reutiliza credencial DPAPI; uma nova tentativa não concede acesso adicional.
    $hostTaskNewPair = $hostTaskStatus -and $hostTaskStatus.status -in @('revoked', 'expired')
    if ($hostTaskNewPair -or -not (Test-Path -LiteralPath (Join-Path $hostTaskState 'credentials.bin') -PathType Leaf)) {
        $ErrorActionPreference = 'Continue'
        $hostTaskReceiptText = & docker @hostTaskCompose exec -T bees bees-host-link invite --format json 2>$null
        $ErrorActionPreference = 'Stop'
        if ($LASTEXITCODE -ne 0) { throw 'Convite indisponível.' }
        $hostTaskReceipt = ($hostTaskReceiptText -join "`n") | ConvertFrom-Json
        if ($hostTaskReceipt.invite_token -notmatch '^bi_[A-Za-z0-9_-]{43}$') { throw 'Convite inválido.' }
        $hostTaskBootstrap = Join-Path $hostTaskState ('bootstrap-' + [guid]::NewGuid().ToString('N') + '.json')
        $hostTaskPayload = @{
            origin = "http://localhost:$Port"
            installation_id = $hostTaskReceipt.installation_id
            invite_id = $hostTaskReceipt.invite_id
            invite_token = $hostTaskReceipt.invite_token
            expires_at = $hostTaskReceipt.expires_at
        } | ConvertTo-Json -Compress
        [System.IO.File]::WriteAllText($hostTaskBootstrap, $hostTaskPayload, [System.Text.UTF8Encoding]::new($false))
        $hostTaskFileAcl = [System.Security.AccessControl.FileSecurity]::new()
        $hostTaskFileSid = [System.Security.Principal.WindowsIdentity]::GetCurrent().User
        $hostTaskFileAcl.SetOwner($hostTaskFileSid)
        $hostTaskFileAcl.SetAccessRuleProtection($true, $false)
        $hostTaskFileAcl.AddAccessRule([System.Security.AccessControl.FileSystemAccessRule]::new($hostTaskFileSid, 'FullControl', 'Allow'))
        Set-Acl -LiteralPath $hostTaskBootstrap -AclObject $hostTaskFileAcl
    }
    $hostTaskArguments = $hostTaskPrefix + @('--state-dir', ('"' + $hostTaskState + '"'))
    if ($hostTaskBootstrap) { $hostTaskArguments += @('--bootstrap-file', ('"' + $hostTaskBootstrap + '"')) }
    if ($hostTaskNewPair) {
        $hostTaskArguments += '--new-pair'
        Remove-Item -LiteralPath $hostTaskPublic -Force -ErrorAction SilentlyContinue
        $hostTaskStatus = $null
    }
    Start-Process -FilePath $hostTaskExecutable -ArgumentList $hostTaskArguments -WorkingDirectory $hostTaskRoot -WindowStyle Hidden | Out-Null
    # Só o código público é lido. Nenhuma credencial sai do arquivo cifrado.
    for ($hostTaskAttempt = 0; $hostTaskAttempt -lt 20; $hostTaskAttempt++) {
        if (Test-Path -LiteralPath $hostTaskPublic -PathType Leaf) {
            try {
                $hostTaskCandidate = Get-Content -LiteralPath $hostTaskPublic -Raw | ConvertFrom-Json
                if ($hostTaskCandidate.fingerprint -match '^[A-F0-9]{4}(-[A-F0-9]{4}){3}$') {
                    $hostTaskStatus = $hostTaskCandidate
                    break
                }
            } catch { }
        }
        Start-Sleep -Milliseconds 500
    }
    if (-not $hostTaskStatus) { throw 'Diagnóstico não iniciou.' }
    if (-not $NoDialog) {
        Add-Type -AssemblyName System.Windows.Forms
        [System.Windows.Forms.MessageBox]::Show(
            ('Na aba Computador, compare o código ' + $hostTaskStatus.fingerprint + ' e confirme somente o diagnóstico. Isso não cria uma VM nem autoriza acesso pessoal.'),
            'Bees — diagnóstico do host', 'OK', 'Information'
        ) | Out-Null
    }
    if (-not $NoBrowser) { Start-Process -FilePath "http://localhost:$Port/" }
    Write-Output 'Vínculo de diagnóstico preparado. Confirme o código na aba Computador.'
} catch {
    Write-Error $hostTaskFailure -ErrorAction Continue
    exit 1
} finally {
    $env:BEES_HTTP_PORT = $hostTaskPreviousPort
    # Helper normalmente já removeu o bootstrap; falhas não deixam ticket em claro.
    if ($hostTaskBootstrap -and (Test-Path -LiteralPath $hostTaskBootstrap)) {
        Remove-Item -LiteralPath $hostTaskBootstrap -Force -ErrorAction SilentlyContinue
    }
    $hostTaskReceiptText = $null; $hostTaskReceipt = $null; $hostTaskPayload = $null
}
