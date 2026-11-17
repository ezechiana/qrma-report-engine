param([Parameter(Mandatory=$true)][string]$PythonwPath)
$ErrorActionPreference = 'Stop'
if (-not (Test-Path -LiteralPath $PythonwPath -PathType Leaf)) { throw 'Select an installed Python 3.10+ pythonw.exe.' }
$resolvedPython = (Resolve-Path -LiteralPath $PythonwPath).Path
if ([IO.Path]::GetFileName($resolvedPython) -ne 'pythonw.exe') { throw 'Expected pythonw.exe.' }
$target = Join-Path $env:LOCALAPPDATA 'GO360\Connector-DEV-App'
New-Item -ItemType Directory -Force -Path $target | Out-Null
Copy-Item -LiteralPath (Join-Path $PSScriptRoot 'go360_connector') -Destination $target -Recurse -Force
Copy-Item -LiteralPath (Join-Path $PSScriptRoot 'launch.pyw') -Destination $target -Force
$shortcutPath = Join-Path ([Environment]::GetFolderPath('Desktop')) 'GO360 Connector DEV.lnk'
$shortcut = (New-Object -ComObject WScript.Shell).CreateShortcut($shortcutPath)
$shortcut.TargetPath = $resolvedPython
$shortcut.Arguments = '"' + (Join-Path $target 'launch.pyw') + '"'
$shortcut.WorkingDirectory = $target
$shortcut.Save()
Write-Output 'DEV prototype installed. Open GO360 Connector DEV from the desktop. Sign-in startup remains opt-in.'
