param([string]$ProjectName = 'NXmini110')
$ErrorActionPreference = 'Stop'
$taskRoot = Split-Path -Parent $PSScriptRoot
$localTools = Join-Path $PSScriptRoot 'local'
$ghidraDir = Get-ChildItem -LiteralPath $localTools -Directory -Filter 'ghidra_*' | Select-Object -First 1
$jdkDir = Get-ChildItem -LiteralPath $localTools -Directory -Filter 'jdk-*' | Select-Object -First 1
if (!$ghidraDir -or !$jdkDir) { throw 'Run python tools/bootstrap_analysis.py first.' }
$env:JAVA_HOME = $jdkDir.FullName
$projectDir = Join-Path $taskRoot 'work/ghidra'
$exportDir = Join-Path $taskRoot 'work/ghidra-export'
New-Item -ItemType Directory -Force -Path $projectDir | Out-Null
$headless = Join-Path $ghidraDir.FullName 'support/analyzeHeadless.bat'
$firmwarePart = Join-Path $taskRoot 'work/nxmini-1.10/decoded/chunk-01.bin'
$scriptsDir = Join-Path $PSScriptRoot 'ghidra'
& $headless $projectDir $ProjectName -import $firmwarePart -loader BinaryLoader -loader-baseAddr 0x40000000 -processor 'ARM:LE:32:v7' -cspec default -scriptPath $scriptsDir -preScript SeedNXmini.java -postScript ExportNXmini.java $exportDir -analysisTimeoutPerFile 300 -max-cpu 4
if ($LASTEXITCODE -ne 0) { throw "Ghidra exited with code $LASTEXITCODE" }
& $headless $projectDir $ProjectName -process 'chunk-01.bin' -noanalysis -scriptPath $scriptsDir -postScript FollowNXmini.java -postScript ExportNXmini.java $exportDir
if ($LASTEXITCODE -ne 0) { throw "Ghidra UI follow-up exited with code $LASTEXITCODE" }
