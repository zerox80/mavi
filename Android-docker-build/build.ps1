param(
    [string]$Distribution,
    [Parameter(ValueFromRemainingArguments = $true)]
    [string[]]$GradleArguments = @()
)

$ErrorActionPreference = 'Stop'
if (-not (Get-Command wsl.exe -ErrorAction SilentlyContinue)) {
    throw 'WSL 2 mit einer Linux-Distribution (z.B. Ubuntu) wird benoetigt.'
}

$wslArguments = @()
if ($Distribution) {
    $wslArguments += @('--distribution', $Distribution)
}
# Pass paths and Gradle arguments positionally; never interpolate shell code.
$linuxScript = & wsl.exe @wslArguments --exec wslpath -a -u "$PSScriptRoot\build.sh"
if ($LASTEXITCODE -ne 0) {
    throw 'Der Build-Pfad konnte nicht nach WSL uebersetzt werden.'
}
$linuxScript = ($linuxScript -join "`n").Trim()
& wsl.exe @wslArguments --exec bash $linuxScript @GradleArguments
exit $LASTEXITCODE
