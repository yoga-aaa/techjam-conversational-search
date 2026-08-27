param(
    [Parameter(ValueFromRemainingArguments = $true)]
    [string[]]$RunnerArgs
)

$ErrorActionPreference = "Stop"
$pythonCommand = Get-Command python -ErrorAction Stop
$runner = Join-Path $PSScriptRoot "run_v2_holdout.py"

& $pythonCommand.Source $runner @RunnerArgs
exit $LASTEXITCODE
