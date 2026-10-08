param([Parameter(ValueFromRemainingArguments = $true)][string[]]$TauriArgs)

$ErrorActionPreference = 'Stop'
$projectRoot = (Resolve-Path (Join-Path $PSScriptRoot '..\..')).Path
$cargoBin = Join-Path $env:USERPROFILE '.cargo\bin'
$venvScripts = Join-Path $projectRoot '.venv\Scripts'
if (-not (Test-Path (Join-Path $cargoBin 'cargo.exe'))) {
    throw "Cargo was not found at $cargoBin. Install Rust with rustup first."
}
$env:Path = "$cargoBin;$venvScripts;$env:Path"
$env:GOAL_LOOP_EXECUTABLE = Join-Path $venvScripts 'goal-loop.exe'
$tauriCli = Join-Path $PSScriptRoot '..\node_modules\.bin\tauri.cmd'
if (-not (Test-Path $tauriCli)) {
    throw "Local Tauri CLI was not found. Run npm install in the ui directory."
}
& $tauriCli @TauriArgs
exit $LASTEXITCODE
