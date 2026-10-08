param(
    [ValidateSet('check', 'register', 'bind', 'run', 'connect', 'stop', 'status', 'halt-runner')]
    [string]$Action = 'check',
    [string]$ConfigPath = '',
    [string]$Slug = '',
    [switch]$Once,
    [switch]$Disabled,
    [switch]$Background
)

# Foreground or explicitly requested background runner. No scheduled task/login/restart.
$ErrorActionPreference = 'Stop'
$taskRoot = Split-Path -Parent $PSScriptRoot
if (-not $ConfigPath) { $ConfigPath = Join-Path $taskRoot '.local/platform.json' }
$taskPython = Join-Path $taskRoot '.venv/Scripts/python.exe'
$taskCli = Join-Path $taskRoot '.venv/Scripts/qrstack-instagram.exe'
$taskPidPath = Join-Path $taskRoot '.local/platform-runner.json'
$taskRunning = $null
if (($Action -eq 'halt-runner' -or $Background) -and (Test-Path -LiteralPath $taskPidPath)) {
    $taskRecord = Get-Content -LiteralPath $taskPidPath -Raw | ConvertFrom-Json
    $taskCandidate = Get-Process -Id $taskRecord.pid -ErrorAction SilentlyContinue
    if ($taskCandidate -and $taskCandidate.Path -eq $taskPython -and $taskCandidate.StartTime.ToUniversalTime().Ticks.ToString() -eq $taskRecord.started_ticks) {
        $taskRunning = $taskCandidate
    }
}
if ($Action -eq 'halt-runner') {
    if ($taskRunning) {
        & taskkill.exe /PID $taskRunning.Id /T /F | Out-Null
        if ($LASTEXITCODE -ne 0) { throw 'Unable to stop the registered runner.' }
        Write-Host 'Publicador Windows encerrado. Confira qualquer Story que estava em andamento.'
    }
    else { Write-Host 'Nenhum publicador registrado em execucao.' }
    return
}
if ($Background -and $Action -ne 'run') { throw '-Background is only valid with -Action run.' }
if ($Background -and $taskRunning) { Write-Host 'O publicador ja esta em execucao.'; return }
$taskEnvNames = @('QRSTACK_PLATFORM_URL', 'QRSTACK_PUBLISHER_ID', 'QRSTACK_PUBLISHER_TOKEN',
    'QRSTACK_PLATFORM_ACCOUNT_MAP', 'QRSTACK_PLATFORM_OWNER_KEY', 'QRSTACK_VAULT_KEY',
    'QRSTACK_VAULT_PATH', 'QRSTACK_ENABLE_PRIVATE_PUBLISHER', 'QRSTACK_TEST_ACCOUNTS')
$taskPreviousEnv = @{}
foreach ($taskEnvName in $taskEnvNames) {
    $taskPreviousEnv[$taskEnvName] = [Environment]::GetEnvironmentVariable($taskEnvName, 'Process')
}

function Read-ProtectedValue([string]$Path) {
    $taskSecure = (Get-Content -LiteralPath $Path -Raw).Trim() | ConvertTo-SecureString
    return [Net.NetworkCredential]::new('', $taskSecure).Password
}

try {
    $taskConfig = Get-Content -LiteralPath $ConfigPath -Raw | ConvertFrom-Json
    if ($taskConfig.publisher_id -notmatch '^[a-zA-Z0-9_-]{1,100}$') { throw 'Invalid publisher ID in configuration.' }
    $taskAccountNames = @($taskConfig.accounts.PSObject.Properties | ForEach-Object {
        if ($_.Name -notmatch '^[a-z0-9][a-z0-9_-]{0,99}$' -or $_.Value -notmatch '^[a-z0-9._]{1,30}$') {
            throw 'Invalid restaurant/account mapping.'
        }
        $_.Value
    })
    if ($taskAccountNames.Count -eq 0) { throw 'Add the intended restaurant/account mapping to the configuration first.' }
    $taskSecureDir = Join-Path $env:LOCALAPPDATA 'QrStackInstagram'
    $taskTokenPath = Join-Path $taskSecureDir ('publisher-' + $taskConfig.publisher_id + '-token.dpapi')
    $taskVaultKeyPath = Join-Path $taskSecureDir 'vault-key.dpapi'
    $env:QRSTACK_PLATFORM_URL = [string]$taskConfig.worker_url
    $env:QRSTACK_PUBLISHER_ID = [string]$taskConfig.publisher_id
    $env:QRSTACK_PLATFORM_ACCOUNT_MAP = $taskConfig.accounts | ConvertTo-Json -Compress
    $env:QRSTACK_VAULT_PATH = Join-Path $taskRoot '.local/vault.db'
    $env:QRSTACK_TEST_ACCOUNTS = $taskAccountNames -join ','
    $env:QRSTACK_ENABLE_PRIVATE_PUBLISHER = '1'

    if ($Action -notin @('connect', 'stop', 'status')) {
        if (-not $env:QRSTACK_PUBLISHER_TOKEN) {
            if (-not (Test-Path -LiteralPath $taskTokenPath) -and $Action -eq 'register') {
                $taskRandom = New-Object byte[] 32
                $taskRng = [Security.Cryptography.RandomNumberGenerator]::Create()
                try { $taskRng.GetBytes($taskRandom) } finally { $taskRng.Dispose() }
                $taskToken = [Convert]::ToBase64String($taskRandom)
                $taskSecureToken = ConvertTo-SecureString -String $taskToken -AsPlainText -Force
                $taskProtectedToken = ConvertFrom-SecureString -SecureString $taskSecureToken
                New-Item -ItemType Directory -Path $taskSecureDir -Force | Out-Null
                $taskStream = [IO.File]::Open($taskTokenPath, [IO.FileMode]::CreateNew, [IO.FileAccess]::Write)
                try {
                    $taskBytes = [Text.Encoding]::UTF8.GetBytes($taskProtectedToken)
                    $taskStream.Write($taskBytes, 0, $taskBytes.Length)
                } finally { $taskStream.Dispose() }
                $taskToken = $null
                [Array]::Clear($taskRandom, 0, $taskRandom.Length)
            }
            if (-not (Test-Path -LiteralPath $taskTokenPath)) { throw 'Register this publisher first, or supply its existing token through the environment.' }
            $env:QRSTACK_PUBLISHER_TOKEN = Read-ProtectedValue $taskTokenPath
        }
    }
    if ($Action -ne 'register' -and -not $env:QRSTACK_VAULT_KEY) {
        if (-not (Test-Path -LiteralPath $taskVaultKeyPath)) { throw 'Supply the original QRSTACK_VAULT_KEY through the environment. No existing vault or key will be replaced.' }
        $env:QRSTACK_VAULT_KEY = Read-ProtectedValue $taskVaultKeyPath
    }
    if ($Action -in @('register', 'bind') -and -not $env:QRSTACK_PLATFORM_OWNER_KEY) {
        $taskOwnerKeyPath = Join-Path $taskSecureDir 'owner-key.dpapi'
        if (Test-Path -LiteralPath $taskOwnerKeyPath) {
            $env:QRSTACK_PLATFORM_OWNER_KEY = Read-ProtectedValue $taskOwnerKeyPath
        } else {
            $taskOwnerKey = Read-Host 'Chave administrativa QrStack (oculta; nao e a senha do Instagram)' -AsSecureString
            $env:QRSTACK_PLATFORM_OWNER_KEY = [Net.NetworkCredential]::new('', $taskOwnerKey).Password
        }
    }
    if ($Action -in @('bind', 'connect', 'stop', 'status')) {
        $taskAccount = $taskConfig.accounts.PSObject.Properties[$Slug]
        if (-not $Slug -or -not $taskAccount) { throw 'Specify -Slug using a restaurant from the local account map.' }
    }
    if ($Action -in @('connect', 'stop', 'status')) {
        & $taskCli $Action $taskAccount.Value
    } else {
        $taskArguments = @('-m', 'qrstack_instagram.platform', $Action)
        if ($Action -eq 'run') {
            $taskArguments += $(if ($Once) { '--once' } else { '--loop' })
            if (-not $Background) { Write-Host 'Publicador QrStack ativo neste terminal. Ctrl+C encerra. Nao ha login automatico.' }
        }
        if ($Action -eq 'bind') {
            $taskArguments += @('--slug', $Slug)
            if ($Disabled) { $taskArguments += '--disabled' }
        }
        if ($Background) {
            $taskLog = Join-Path $taskRoot '.local/platform-runner.log'
            $taskErrorLog = Join-Path $taskRoot '.local/platform-runner.err.log'
            $taskProcess = Start-Process -FilePath $taskPython -ArgumentList $taskArguments -WorkingDirectory $taskRoot -WindowStyle Hidden -RedirectStandardOutput $taskLog -RedirectStandardError $taskErrorLog -PassThru
            @{pid=$taskProcess.Id;started_ticks=$taskProcess.StartTime.ToUniversalTime().Ticks.ToString()} | ConvertTo-Json | Set-Content -LiteralPath $taskPidPath -Encoding UTF8
            Write-Host ('Publicador iniciado em segundo plano. Registro: ' + $taskLog)
            return
        }
        & $taskPython @taskArguments
    }
    if ($LASTEXITCODE -ne 0) { throw 'Command stopped. Review account/job state before continuing; no retry was made.' }
} finally {
    foreach ($taskEnvName in $taskEnvNames) {
        [Environment]::SetEnvironmentVariable($taskEnvName, $taskPreviousEnv[$taskEnvName], 'Process')
    }
}
