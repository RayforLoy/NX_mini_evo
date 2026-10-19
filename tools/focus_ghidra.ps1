param(
    [string]$ProjectName = 'NXmini110',
    [string[]]$Addresses = @('403f4090', '403f0e98', '403f0ea8', '406d4324', '40448440', '4017e7e4', '4034804c', '40348ee4')
)
$ErrorActionPreference = 'Stop'
$taskRoot = Split-Path -Parent $PSScriptRoot
$localTools = Join-Path $PSScriptRoot 'local'
$ghidraDir = Get-ChildItem -LiteralPath $localTools -Directory -Filter 'ghidra_*' | Select-Object -First 1
$jdkDir = Get-ChildItem -LiteralPath $localTools -Directory -Filter 'jdk-*' | Select-Object -First 1
if (!$ghidraDir -or !$jdkDir) { throw 'Run bootstrap_analysis.py first.' }
foreach ($address in $Addresses) {
    if ($address -notmatch '^(0x)?[0-9a-fA-F]{8}$') { throw "Invalid address: $address" }
}
$env:JAVA_HOME = $jdkDir.FullName
$headless = Join-Path $ghidraDir.FullName 'support/analyzeHeadless.bat'
& $headless (Join-Path $taskRoot 'work/ghidra') $ProjectName -process 'chunk-01.bin' -noanalysis -scriptPath (Join-Path $PSScriptRoot 'ghidra') -postScript FollowNXmini.java -postScript FocusNXmini.java (Join-Path $taskRoot 'work/ghidra-lens-focus') @Addresses
if ($LASTEXITCODE -ne 0) { throw "Focused Ghidra export exited with code $LASTEXITCODE" }
