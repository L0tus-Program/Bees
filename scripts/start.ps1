param(
    [switch]$NoBrowser,
    [switch]$NoBuild,
    [ValidatePattern('^[a-z0-9][a-z0-9_-]*$')][string]$ProjectName = 'bees',
    [ValidateRange(1, 65535)][int]$Port = 8080,
    [string]$LogDirectory
)
$ErrorActionPreference = 'Stop'
$taskRoot = Split-Path -Parent $PSScriptRoot
$taskPreviousPort = $env:BEES_HTTP_PORT
if (-not $LogDirectory) { $LogDirectory = Join-Path $taskRoot 'logs' }
$taskLog = Join-Path $LogDirectory 'startup.log'
$taskFailure = 'Confira o Docker Desktop, a porta escolhida e logs/startup.log.'

try {
    New-Item -ItemType Directory -Path (Split-Path -Parent $taskLog) -Force | Out-Null
    if (-not (Get-Command docker -ErrorAction SilentlyContinue)) {
        $taskFailure = 'Instale e abra o Docker Desktop com containers Linux.'
        throw 'Instale e abra o Docker Desktop com containers Linux antes de iniciar o Bees.'
    }
    # Windows PowerShell trata stderr nativo de progresso como ErrorRecord.
    # Redirecionar, permitir a conclusão e avaliar o exit code, sem abortar por progresso.
    $ErrorActionPreference = 'Continue'
    $taskDockerReady = & docker info --format '{{.OSType}}' 2>$null
    $ErrorActionPreference = 'Stop'
    if ($LASTEXITCODE -ne 0 -or "$taskDockerReady".Trim() -ne 'linux') {
        $taskFailure = 'Abra o Docker Desktop e aguarde containers Linux ficarem disponíveis.'
        throw 'Abra o Docker Desktop e aguarde o serviço de containers Linux ficar disponível.'
    }
    $env:BEES_HTTP_PORT = "$Port"
    $taskCompose = @('compose', '--project-name', $ProjectName, '--project-directory', $taskRoot, '-f', (Join-Path $taskRoot 'compose.yaml'))
    $taskUp = @('up', '--detach', '--wait', '--wait-timeout', '180')
    if (-not $NoBuild) { $taskUp += '--build' }
    Write-Output 'Preparando o Bees. Na primeira vez, o Docker precisa baixar e compilar a aplicação.'
    $ErrorActionPreference = 'Continue'
    & docker @taskCompose @taskUp *> $taskLog
    $ErrorActionPreference = 'Stop'
    if ($LASTEXITCODE -ne 0) {
        throw 'Não foi possível iniciar o Bees. Confira a disponibilidade da porta e logs/startup.log.'
    }
    # Saída privada: token só permanece em memória e nunca entra no log acima.
    $taskFailure = 'Não foi possível preparar o primeiro acesso. Inicie o Bees novamente.'
    $ErrorActionPreference = 'Continue'
    $taskReceiptText = & docker @taskCompose exec -T bees bees-auth bootstrap --format json 2>$null
    $ErrorActionPreference = 'Stop'
    if ($LASTEXITCODE -ne 0) { throw 'Não foi possível preparar o primeiro acesso. Tente iniciar o Bees novamente.' }
    $taskReceipt = ($taskReceiptText -join "`n") | ConvertFrom-Json
    if ($taskReceipt.configured -isnot [bool]) { throw 'Resposta de primeiro acesso inválida.' }
    $taskUrl = "http://localhost:$Port/"
    if (-not $taskReceipt.configured) {
        if ($taskReceipt.bootstrap_token -notmatch '^[A-Za-z0-9_-]{43}$') { throw 'Código de primeiro acesso inválido.' }
        $taskUrl += '#setup=' + $taskReceipt.bootstrap_token
    }
    if (-not $NoBrowser) { Start-Process -FilePath $taskUrl }
    Write-Output "Bees pronto em http://localhost:$Port/."
} catch {
    # Mensagens fixas; não imprimir objetos/JSON que possam conter o código privado.
    Write-Error ('Falha ao iniciar o Bees: ' + $taskFailure) -ErrorAction Continue
    exit 1
} finally {
    $env:BEES_HTTP_PORT = $taskPreviousPort
    $taskReceiptText = $null; $taskReceipt = $null; $taskUrl = $null
}
