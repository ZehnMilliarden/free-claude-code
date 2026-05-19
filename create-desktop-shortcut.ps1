$projectDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$desktop = [System.IO.Path]::Combine($env:USERPROFILE, "Desktop")
$lnkPath = [System.IO.Path]::Combine($desktop, "Claude Code Proxy.lnk")

$ws = New-Object -ComObject WScript.Shell
$lnk = $ws.CreateShortcut($lnkPath)
$lnk.TargetPath = "$env:windir\system32\cmd.exe"
$lnk.Arguments = '/c cd /d "' + $projectDir + '" && uv run uvicorn server:app --host 127.0.0.1 --port 9999 && pause'
$lnk.WorkingDirectory = $projectDir
$lnk.WindowStyle = 1

$iconPath = Join-Path $projectDir "claude.ico"
if (Test-Path $iconPath) {
    $lnk.IconLocation = $iconPath
}

$lnk.Save()

if (Test-Path $lnkPath) {
    Write-Host "Shortcut created on desktop: Claude Code Proxy.lnk"
} else {
    Write-Host "Failed to create shortcut."
}
