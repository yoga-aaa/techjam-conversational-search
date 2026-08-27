param(
    [Parameter(ValueFromRemainingArguments = $true)]
    [string[]]$RunnerArgs
)

$ErrorActionPreference = "Stop"
$pythonCommand = Get-Command python -ErrorAction Stop
$runner = Join-Path $PSScriptRoot "run_tests.py"

& $pythonCommand.Source $runner @RunnerArgs
exit $LASTEXITCODE
