param(
    [Parameter(Mandatory = $true)][ValidatePattern('^[a-z0-9._]+$')][string]$Account,
    [Parameter(Mandatory = $true)][string]$Image,
    [Parameter(Mandatory = $true)][uri]$TargetUrl,
    [Parameter(Mandatory = $true)][ValidatePattern('^[a-zA-Z0-9_-]+$')][string]$Job
)

$ErrorActionPreference = 'Stop'
$taskRoot = Split-Path -Parent $PSScriptRoot
$taskLocal = Join-Path $taskRoot '.local'
$taskCli = Join-Path $taskRoot '.venv/Scripts/qrstack-instagram.exe'
$taskVault = Join-Path $taskLocal 'vault.db'
$taskResult = Join-Path $taskLocal ($Job + '.result.json')
$taskKeyFile = Join-Path $env:LOCALAPPDATA 'QrStackInstagram/vault-key.dpapi'
if ($TargetUrl.Scheme -ne 'https' -or -not (Test-Path -LiteralPath $Image -PathType Leaf)) {
    throw 'A local image and HTTPS URL are required.'
}
New-Item -ItemType Directory -Path $taskLocal -Force | Out-Null
# A second launch of this exact attempt must not prompt or send credentials again.
$taskReservation = [IO.File]::Open($taskResult, [IO.FileMode]::CreateNew, [IO.FileAccess]::Write)
$taskReservation.Close()
$taskState = [ordered]@{
    account = $Account
    job = $Job
    target_url = $TargetUrl.AbsoluteUri
    image = [IO.Path]::GetFullPath($Image)
    stage = 'PREPARING'
    steps = @()
    verifications = @()
}
function Save-TestState([string]$Stage) {
    $taskState.stage = $Stage
    $taskState.updated_at_utc = [DateTime]::UtcNow.ToString('o')
    $taskTemporary = $taskResult + '.tmp'
    [IO.File]::WriteAllText($taskTemporary, ($taskState | ConvertTo-Json -Depth 6))
    Move-Item -LiteralPath $taskTemporary -Destination $taskResult -Force
}
function Invoke-TestStep([string]$Stage, [string[]]$CliArguments) {
    Save-TestState $Stage
    if ($Stage.StartsWith('VERIFYING')) {
        $taskVerification = & $taskCli @CliArguments
        $taskExitCode = $LASTEXITCODE
        Write-Host $taskVerification
        if ($taskExitCode -eq 0) {
            $taskState.verifications += ($taskVerification | ConvertFrom-Json)
        }
    } else {
        & $taskCli @CliArguments
        $taskExitCode = $LASTEXITCODE
    }
    $taskState.steps += @{ stage = $Stage; exit_code = $taskExitCode }
    Save-TestState $Stage
    if ($taskExitCode -ne 0) { throw 'Test step stopped; no retry will be made.' }
}

$taskSucceeded = $false
$taskKeyReady = $false
try {
    Save-TestState 'PREPARING'
    Set-Location -LiteralPath $taskRoot
    if (-not $env:QRSTACK_VAULT_KEY) {
        if (Test-Path -LiteralPath $taskKeyFile) {
            $taskSecureKey = Get-Content -LiteralPath $taskKeyFile -Raw | ConvertTo-SecureString
            $env:QRSTACK_VAULT_KEY = [Net.NetworkCredential]::new('', $taskSecureKey).Password
        } else {
            if (Test-Path -LiteralPath $taskVault) {
                throw 'Existing vault has no matching local key. Supply its original key externally.'
            }
            $taskRandom = New-Object byte[] 32
            $taskRng = [Security.Cryptography.RandomNumberGenerator]::Create()
            try { $taskRng.GetBytes($taskRandom) } finally { $taskRng.Dispose() }
            $taskKey = [Convert]::ToBase64String($taskRandom).Replace('+', '-').Replace('/', '_')
            $taskSecureKey = ConvertTo-SecureString -String $taskKey -AsPlainText -Force
            $taskProtectedKey = ConvertFrom-SecureString -SecureString $taskSecureKey
            New-Item -ItemType Directory -Path (Split-Path -Parent $taskKeyFile) -Force | Out-Null
            $taskKeyStream = [IO.File]::Open($taskKeyFile, [IO.FileMode]::CreateNew, [IO.FileAccess]::Write)
            try {
                $taskProtectedBytes = [Text.Encoding]::UTF8.GetBytes($taskProtectedKey)
                $taskKeyStream.Write($taskProtectedBytes, 0, $taskProtectedBytes.Length)
            } finally { $taskKeyStream.Dispose() }
            $env:QRSTACK_VAULT_KEY = $taskKey
            $taskKey = $null
            [Array]::Clear($taskRandom, 0, $taskRandom.Length)
        }
    }
    $taskKeyReady = $true
    $env:QRSTACK_VAULT_PATH = $taskVault
    $env:QRSTACK_ENABLE_PRIVATE_PUBLISHER = '1'
    $env:QRSTACK_TEST_ACCOUNTS = $Account
    Write-Host "Teste interno: @$Account" -ForegroundColor Cyan
    Write-Host "Imagem: $Image"
    Write-Host "Link: $($TargetUrl.AbsoluteUri)"
    Write-Host "Job unico: $Job"
    Write-Host 'Digite a senha somente no prompt oculto abaixo.'
    Write-Host 'Apos login e verificacao, sera tentada UMA publicacao. Qualquer falha encerra o fluxo.'
    Invoke-TestStep 'AWAITING_PASSWORD_OR_LOGIN' @('connect', $Account)
    Invoke-TestStep 'VERIFYING_SAVED_SESSION' @('verify', $Account)
    Invoke-TestStep 'PUBLISHING' @('publish', $Account, '--image', $Image, '--url', $TargetUrl.AbsoluteUri, '--job', $Job)
    Invoke-TestStep 'VERIFYING_STORY' @('verify', $Account, '--job', $Job)
    $taskSucceeded = $true
} catch {
    # Store only a local error class, never upstream messages or secrets.
    $taskState.runner_error_class = $_.Exception.GetType().Name
    Write-Host 'Teste interrompido. Nao repetir login/publicacao sem revisar o resultado.' -ForegroundColor Yellow
} finally {
    if ($taskKeyReady) {
        try {
            $taskStatus = & $taskCli status $Account --job $Job
            if ($LASTEXITCODE -eq 0) { $taskState.status_before_stop = $taskStatus | ConvertFrom-Json }
            & $taskCli stop $Account
            $taskState.stop_exit_code = $LASTEXITCODE
        } catch { $taskState.cleanup_error_class = $_.Exception.GetType().Name }
    }
    $env:QRSTACK_ENABLE_PRIVATE_PUBLISHER = '0'
    $env:QRSTACK_VAULT_KEY = $null
    $env:QRSTACK_TEST_ACCOUNTS = $null
    Save-TestState $(if ($taskSucceeded) { 'COMPLETED_NEEDS_VISUAL_CHECK' } else { 'STOPPED' })
    Write-Host "Resultado sem segredos: $taskResult"
    if ($taskSucceeded) {
        Write-Host 'Confira o Story no aplicativo oficial: imagem, sticker e destino do toque.' -ForegroundColor Green
    }
}
