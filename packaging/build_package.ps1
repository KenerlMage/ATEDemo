# =====================================================================
#  ATE Runner - build a self-contained Windows deployment package
#  (frontend + backend + embedded Python runtime + double-click installer)
#
#  Outputs (default <parent-of-repo>\ATE_DIST):
#     ATE_Setup-<ver>.exe                 双击安装向导（自包含，无需联网）
#     ATERunner-portable-<ver>.zip        免安装绿色包（解压即用）
#
#  Usage (run on the BUILD machine, NOT on the customer IPC):
#     powershell -ExecutionPolicy Bypass -File packaging\build_package.ps1
#     powershell -ExecutionPolicy Bypass -File packaging\build_package.ps1 -Version 1.1.0 -SkipFrontend
#
#  Notes:
#   * This file is intentionally ASCII only. PowerShell 5.1 decodes
#     .ps1 files as ANSI/GBK when there is no BOM, and a Chinese literal
#     can swallow the following quote byte, breaking the script parse.
#   * Requires: Node.js + npm (frontend), Python 3.13 (for building the runtime).
#   * The produced package is fully offline: embedded Python + all wheels.
# =====================================================================
[CmdletBinding()]
param(
  [string]$Version = "",
  [string]$RepoRoot = "",
  [string]$Dist = "",
  [string]$Python = "",
  [string]$IndexUrl = "https://mirrors.aliyun.com/pypi/simple/",
  [switch]$SkipFrontend,
  [switch]$SkipLauncher,
  [switch]$ReuseRuntime,
  [switch]$IncludeLicense
)

$ErrorActionPreference = "Stop"
function Info($m) { Write-Host "[build] $m" -ForegroundColor Cyan }
function Warn($m) { Write-Host "[warn ] $m" -ForegroundColor Yellow }
function Die($m) { Write-Host "[fail ] $m" -ForegroundColor Red; exit 1 }

# ---------------------------------------------------------------- paths
$pkgDir = Split-Path -Parent $MyInvocation.MyCommand.Path
if (-not $RepoRoot) { $RepoRoot = Split-Path -Parent $pkgDir }
if (-not $Dist) { $Dist = Join-Path (Split-Path -Parent $RepoRoot) "ATE_DIST" }
if (-not $Version) { $Version = (Get-Date -Format "1.0.yyMMdd") }
$backend = Join-Path $RepoRoot "backend"
$frontend = Join-Path $RepoRoot "frontend"
$payload = Join-Path $Dist "payload\ATERunner"
$build = Join-Path $Dist "build"

if (-not (Test-Path (Join-Path $backend "main.py"))) { Die "backend\main.py not found under $RepoRoot" }
if (-not (Test-Path (Join-Path $pkgDir "runtime-requirements.txt"))) { Die "packaging\runtime-requirements.txt missing" }

if (-not $Python) {
  foreach ($cand in @((Join-Path $backend ".venv\Scripts\python.exe"), "python", "py")) {
    try { & $cand -c "import sys" 2>$null; if ($LASTEXITCODE -eq 0) { $Python = $cand; break } } catch { }
  }
}
if (-not $Python) { Die "no Python found; pass -Python <path to python.exe>" }
Info "repo    : $RepoRoot"
Info "dist    : $Dist"
Info "version : $Version"
Info "python  : $Python"

New-Item -ItemType Directory -Force -Path $payload, $build | Out-Null
foreach ($d in @("app", "web", "runtime", "logs", "workspace")) {
  New-Item -ItemType Directory -Force -Path (Join-Path $payload $d) | Out-Null
}

# ---------------------------------------------------------------- 1. frontend
if (-not $SkipFrontend) {
  Info "1/6 building frontend (npm run build)..."
  Push-Location $frontend
  try {
    if (-not (Test-Path (Join-Path $frontend "node_modules"))) { & npm install; if ($LASTEXITCODE -ne 0) { Die "npm install failed" } }
    & npm run build
    if ($LASTEXITCODE -ne 0) { Die "npm run build failed" }
  } finally { Pop-Location }
} else {
  Info "1/6 frontend build skipped"
}
$distDir = Join-Path $frontend "dist"
if (-not (Test-Path (Join-Path $distDir "index.html"))) { Die "frontend\dist\index.html missing - run npm run build first" }
Copy-Item (Join-Path $distDir "*") (Join-Path $payload "web") -Recurse -Force
Info ("      web/  <- " + (Get-ChildItem (Join-Path $payload "web") -Recurse -File).Count + " files")

# ---------------------------------------------------------------- 2. backend
Info "2/6 copying backend payload (app/)..."
$items = @("main.py", "ate_db.py", "testbench_registry.py", "testbench_lifecycle.py",
           "instrument_tools.py", "license_utils.py", "requirements.txt",
           "drivers", "tps_runtime", "testresource", "test_cases")
foreach ($it in $items) {
  $src = Join-Path $backend $it
  if (-not (Test-Path $src)) { Warn "missing $it (skipped)"; continue }
  Copy-Item $src (Join-Path $payload "app") -Recurse -Force
}
if ($IncludeLicense) {
  $lic = Join-Path $backend "license.dat"
  if (Test-Path $lic) { Copy-Item $lic (Join-Path $payload "app\license.dat") -Force; Info "      license.dat included (machine bound)" }
}
Get-ChildItem (Join-Path $payload "app") -Recurse -Directory -Include "__pycache__", ".pytest_cache" |
  ForEach-Object { Remove-Item $_.FullName -Recurse -Force -ErrorAction SilentlyContinue }
Get-ChildItem (Join-Path $payload "app") -Recurse -File -Include "*.pyc", "*.log" |
  ForEach-Object { Remove-Item $_.FullName -Force -ErrorAction SilentlyContinue }
Remove-Item (Join-Path $payload "app\ate.db") -Force -ErrorAction SilentlyContinue
Info ("      app/  <- " + (Get-ChildItem (Join-Path $payload "app") -Recurse -File).Count + " files")

# ---------------------------------------------------------------- 3. embedded python runtime
Info "3/6 preparing embedded Python runtime..."
$pyExe = Join-Path $payload "runtime\python.exe"
if (-not (Test-Path $pyExe)) {
  $pyVer = (& $Python -c "import sys;print('%d.%d.%d' % sys.version_info[:3])").Trim()
  $arch = (& $Python -c "import platform;print('amd64' if platform.machine() in ('AMD64','x86_64') else 'win32')").Trim()
  $zipName = "python-$pyVer-embed-$arch.zip"
  $zipPath = Join-Path $build $zipName
  $urls = @(
    "https://registry.npmmirror.com/-/binary/python/$pyVer/$zipName",
    "https://mirrors.huaweicloud.com/python/$pyVer/$zipName",
    "https://www.python.org/ftp/python/$pyVer/$zipName"
  )
  if (-not (Test-Path $zipPath)) {
    $ok = $false
    foreach ($u in $urls) {
      try {
        Info "      downloading $u"
        Invoke-WebRequest -Uri $u -OutFile $zipPath -UseBasicParsing -TimeoutSec 120
        if ((Get-Item $zipPath).Length -gt 5000000) { $ok = $true; break }
      } catch { Warn "download failed: $($_.Exception.Message)" }
    }
    if (-not $ok) { Die "cannot download $zipName - put it manually at $zipPath" }
  }
  Expand-Archive -Path $zipPath -DestinationPath (Join-Path $payload "runtime") -Force
  # the embedded interpreter reads python<major><minor>._pth (e.g. python313._pth)
  $pyShort = (& $Python -c "import sys;print('%d%d' % sys.version_info[:2])").Trim()
  $pth = Join-Path $payload "runtime\python$pyShort._pth"
  if (-not (Test-Path $pth)) {
    $pth = (Get-ChildItem (Join-Path $payload "runtime") -Filter "*._pth" | Select-Object -First 1).FullName
  }
  if ($pth -and (Test-Path $pth)) {
    $c = Get-Content $pth
    $c = $c | ForEach-Object { if ($_ -match '^#\s*import site') { 'import site' } else { $_ } }
    if (-not ($c -match 'import site')) { $c += 'import site' }
    if (-not ($c -match 'Lib\\site-packages')) { $c += 'Lib\site-packages' }
    if (-not ($c -match '^\s*\.\s*$')) { $c = @('.') + $c }
    Set-Content -Path $pth -Value $c -Encoding ASCII
    Info "      patched $(Split-Path -Leaf $pth)"
  } else {
    Warn "      ._pth not found - runtime may not see Lib\site-packages"
  }
} else {
  Info "      runtime already prepared (skip download)"
}

Info "      installing wheels into runtime\Lib\site-packages (offline-ready)..."
$sp = Join-Path $payload "runtime\Lib\site-packages"
if ($ReuseRuntime -and (Test-Path $sp) -and ((Get-ChildItem $sp -Directory | Measure-Object).Count -gt 10)) {
  Info "      site-packages already present (reuse)"
} else {
  Remove-Item $sp -Recurse -Force -ErrorAction SilentlyContinue
  New-Item -ItemType Directory -Force -Path $sp | Out-Null
  & $Python -m pip install --target $sp --only-binary=:all: --no-warn-script-location --upgrade `
    -i $IndexUrl -r (Join-Path $pkgDir "runtime-requirements.txt")
  if ($LASTEXITCODE -ne 0) { Die "pip install --target failed" }
}
Info ("      site-packages <- " + (Get-ChildItem $sp -Recurse -File).Count + " files")

# ---------------------------------------------------------------- 4. portable extras + launcher
Info "4/6 adding portable extras..."
$portable = Join-Path $pkgDir "portable"
if (Test-Path $portable) { Copy-Item (Join-Path $portable "*") $payload -Recurse -Force }

if (-not $SkipLauncher) {
  $hasPyI = $false
  try { & $Python -c "import PyInstaller" 2>$null; if ($LASTEXITCODE -eq 0) { $hasPyI = $true } } catch { }
  if ($hasPyI) {
    Info "      building launcher exe (PyInstaller)..."
    & $Python -m PyInstaller --noconfirm --clean --onefile --console `
      --name ATE_Launcher `
      --distpath (Join-Path $build "launcher") --workpath (Join-Path $build "launcher_work") `
      --specpath (Join-Path $build "launcher_spec") `
      (Join-Path $pkgDir "launcher\ate_launcher.py")
    if ($LASTEXITCODE -ne 0) { Warn "launcher build failed (portable .bat launcher still works)" }
    else { Copy-Item (Join-Path $build "launcher\ATE_Launcher.exe") $payload -Force }
  } else {
    Warn "PyInstaller not installed in $Python - skipping ATE_Launcher.exe"
    Warn "  (python -m pip install pyinstaller, then rebuild to get the single-file launcher)"
  }
}

# ---------------------------------------------------------------- 5. clean leftovers + zip
Info "5/6 cleaning build-machine leftovers, writing version info and packaging zip..."
Remove-Item (Join-Path $payload "logs") -Recurse -Force -ErrorAction SilentlyContinue
Remove-Item (Join-Path $payload "workspace") -Recurse -Force -ErrorAction SilentlyContinue
New-Item -ItemType Directory -Force -Path (Join-Path $payload "logs"), (Join-Path $payload "workspace") | Out-Null
# Compress-Archive drops empty directories, so keep a placeholder in the data dirs
"ATE Runner runtime logs / reports" | Set-Content -Path (Join-Path $payload "logs\.keep") -Encoding UTF8
"ATE Runner TPS workspace" | Set-Content -Path (Join-Path $payload "workspace\.keep") -Encoding UTF8
foreach ($junk in @("app\ate.db", "app\license.dat", "app\ate.db-journal", "app\ate.db-wal")) {
  Remove-Item (Join-Path $payload $junk) -Force -ErrorAction SilentlyContinue
}
Get-ChildItem (Join-Path $payload "app") -Recurse -Directory -Include "__pycache__", ".pytest_cache" |
  ForEach-Object { Remove-Item $_.FullName -Recurse -Force -ErrorAction SilentlyContinue }

@"
ATE Runner deployment package
Version   : $Version
Built at  : $(Get-Date -Format 'yyyy-MM-dd HH:mm:ss')
Backend   : app\      (FastAPI + pytest)
Frontend  : web\      (Vue 3 build output, served by the backend on the same port)
Runtime   : runtime\  (embedded Python, fully offline)
Entry     : ATE_Launcher.exe   or   start-ate.bat
Data      : logs\  workspace\  app\ate.db   (inside this folder)
"@ | Set-Content -Path (Join-Path $payload "VERSION.txt") -Encoding UTF8

$zipOut = Join-Path $Dist "ATERunner-portable-$Version.zip"
Remove-Item $zipOut -Force -ErrorAction SilentlyContinue
Compress-Archive -Path (Join-Path $payload "*") -DestinationPath $zipOut -CompressionLevel Optimal
Info ("      " + $zipOut + "  (" + [math]::Round((Get-Item $zipOut).Length / 1MB, 1) + " MB)")

# ---------------------------------------------------------------- 6. installer
# Prefer Inno Setup when the build machine has it, otherwise compile a
# self-contained setup exe with the csc compiler shipped by Windows.
Info "6/6 building installer..."
$setupOut = Join-Path $Dist "ATE_Setup-$Version.exe"
$madeSetup = $false
$iscc = $null
foreach ($c in @("$env:LOCALAPPDATA\Programs\Inno Setup 6\ISCC.exe",
                 "C:\Program Files (x86)\Inno Setup 6\ISCC.exe",
                 "C:\Program Files\Inno Setup 6\ISCC.exe")) {
  if (Test-Path $c) { $iscc = $c; break }
}
if (-not $iscc) { $g = Get-Command iscc.exe -ErrorAction SilentlyContinue; if ($g) { $iscc = $g.Source } }

if ($iscc) {
  $iss = Join-Path $pkgDir "installer\ate_runner.iss"
  & $iscc /DAppVersion=$Version "/DPayloadDir=$payload" "/DOutDir=$Dist" $iss
  if ($LASTEXITCODE -eq 0) { $madeSetup = $true; Info "      [Inno] $setupOut" }
  else { Warn "ISCC failed - falling back to the built-in setup compiler" }
}

if (-not $madeSetup) {
  $csc = $null
  foreach ($c in @("$env:WINDIR\Microsoft.NET\Framework64\v4.0.30319\csc.exe",
                   "$env:WINDIR\Microsoft.NET\Framework\v4.0.30319\csc.exe")) {
    if (Test-Path $c) { $csc = $c; break }
  }
  if (-not $csc) {
    Warn "no ISCC.exe and no csc.exe - only the portable zip was produced."
    Warn "  install Inno Setup 6 (winget install JRSoftware.InnoSetup) and re-run for ATE_Setup-$Version.exe"
  } else {
    Info "      compiling self-contained setup exe with $csc"
    $resZip = Join-Path $build "payload.zip"
    Copy-Item $zipOut $resZip -Force
    $asmInfo = Join-Path $build "AssemblyInfo.g.cs"
    # a .NET assembly version has to be 4 numbers <= 65534, so the real
    # version string is carried as AssemblyInformationalVersion instead
    @"
using System.Reflection;
[assembly: AssemblyTitle("ATE Runner Setup")]
[assembly: AssemblyProduct("ATE Runner")]
[assembly: AssemblyCompany("ATE")]
[assembly: AssemblyVersion("1.0.0.0")]
[assembly: AssemblyFileVersion("1.0.0.0")]
[assembly: AssemblyInformationalVersion("$Version")]
"@ | Set-Content -Path $asmInfo -Encoding UTF8
    $setupCs = Join-Path $pkgDir "installer\setup.cs"
    Remove-Item $setupOut -Force -ErrorAction SilentlyContinue
    & $csc /nologo /target:winexe /codepage:65001 /platform:anycpu /optimize+ `
      "/out:$setupOut" "/resource:$resZip,payload.zip" `
      /r:System.IO.Compression.dll /r:System.IO.Compression.FileSystem.dll `
      /r:System.Windows.Forms.dll /r:System.Drawing.dll `
      $setupCs $asmInfo
    if ($LASTEXITCODE -eq 0 -and (Test-Path $setupOut)) {
      $madeSetup = $true
      Info ("      " + $setupOut + "  (" + [math]::Round((Get-Item $setupOut).Length / 1MB, 1) + " MB)")
    } else {
      Warn "csc compilation failed - only the portable zip was produced"
    }
  }
}

Write-Host ""
Info "done. artifacts in $Dist"
Get-ChildItem $Dist -File | Select-Object Name, @{n = "MB"; e = { [math]::Round($_.Length / 1MB, 1) } } | Format-Table -AutoSize
