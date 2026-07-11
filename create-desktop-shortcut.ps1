$projectDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$desktop = [System.IO.Path]::Combine($env:USERPROFILE, "Desktop")
$lnkPath = [System.IO.Path]::Combine($desktop, "Claude Code Client.lnk")
$vbsPath = [System.IO.Path]::Combine($projectDir, "launch.vbs")

$ws = New-Object -ComObject WScript.Shell
$lnk = $ws.CreateShortcut($lnkPath)
$lnk.TargetPath = "$env:windir\system32\wscript.exe"
$lnk.Arguments = """" + $vbsPath + """"
$lnk.WorkingDirectory = $projectDir
$lnk.WindowStyle = 1

$iconPath = Join-Path $projectDir "claude.ico"
if (Test-Path $iconPath) {
    $lnk.IconLocation = $iconPath
}

$lnk.Save()

if (Test-Path $lnkPath) {
    Write-Host "Shortcut created on desktop: Claude Code Client.lnk"
} else {
    Write-Host "Failed to create shortcut."
}
