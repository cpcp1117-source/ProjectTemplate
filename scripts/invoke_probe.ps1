# invoke_probe.ps1 — bounded entry point for bidirectional probe_peer.py invocation.
#
# Parameters:
#   -Operation  : "context" or "peer"
#   -Root       : absolute project root path
#   -Python     : absolute path to Python executable
#   -Direction  : "claude-to-codex" or "codex-to-claude"
#   -Nonce      : 1-64 hex characters
#
# Validates all inputs before invoking Python. Propagates real exit code.
# No dynamic evaluation. No encoded commands. No ExecutionPolicy bypass.
# No delegation: calls probe_peer.py or probe_context.py once only.

[CmdletBinding()]
param(
    [Parameter(Mandatory=$true)]
    [ValidateSet('context','peer')]
    [string]$Operation,

    [Parameter(Mandatory=$true)]
    [string]$Root,

    [Parameter(Mandatory=$true)]
    [string]$Python,

    [Parameter(Mandatory=$true)]
    [ValidateSet('claude-to-codex','codex-to-claude')]
    [string]$Direction,

    [Parameter(Mandatory=$true)]
    [string]$Nonce
)

Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'

# --- Input validation ---

if (-not [System.IO.Path]::IsPathRooted($Root)) {
    Write-Error "ERROR: -Root must be an absolute path, got: $Root"
    exit 1
}

if (-not [System.IO.Path]::IsPathRooted($Python)) {
    Write-Error "ERROR: -Python must be an absolute path, got: $Python"
    exit 1
}

if (-not ($Nonce -match '^[a-fA-F0-9]{1,64}$')) {
    Write-Error "ERROR: -Nonce must be 1-64 hex characters, got: $Nonce"
    exit 1
}

if (-not (Test-Path -LiteralPath $Python -PathType Leaf)) {
    Write-Error "ERROR: Python executable not found: $Python"
    exit 1
}

# --- Select target script ---

if ($Operation -eq 'peer') {
    $ScriptPath = Join-Path $Root 'scripts\probe_peer.py'
} else {
    $ScriptPath = Join-Path $Root 'scripts\probe_context.py'
}

if (-not (Test-Path -LiteralPath $ScriptPath -PathType Leaf)) {
    Write-Error "ERROR: Script not found: $ScriptPath"
    exit 1
}

# --- Invoke Python --- no shell expansion, literal argument array ---

if ($Operation -eq 'peer') {
    & $Python '-X' 'utf8' $ScriptPath '--direction' $Direction '--nonce' $Nonce '--root' $Root
} else {
    & $Python '-X' 'utf8' $ScriptPath '--nonce' $Nonce '--direction' $Direction
}

exit $LASTEXITCODE
