# Run the headless smoke test via the Microsoft Store launcher (detaches; we poll the log).
$root = Split-Path -Parent $PSScriptRoot
$log = Join-Path $root "tests\smoke.log"
Remove-Item $log -ErrorAction SilentlyContinue
$b = "$env:LOCALAPPDATA\Microsoft\WindowsApps\blender-launcher.exe"
Start-Process -FilePath $b -ArgumentList '-b','--factory-startup','--python',(Join-Path $root 'tests\smoke.py')
$deadline = (Get-Date).AddSeconds(90)
while ((Get-Date) -lt $deadline) {
  Start-Sleep -Milliseconds 500
  if (Test-Path $log) {
    $t = Get-Content $log -Raw -ErrorAction SilentlyContinue
    if ($t -match "(?m)^(OK|FAILED)\s*$") { break }
  }
}
if (Test-Path $log) { Get-Content $log } else { "no log produced" }
