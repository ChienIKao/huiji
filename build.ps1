# 打包成免安裝的 Windows 版：dist\Huiji-v<版本>-win64.zip
# 用法：.\build.ps1（需要先建立 .venv 並安裝 requirements.txt）
$ErrorActionPreference = "Stop"
Set-Location $PSScriptRoot

$python = if (Test-Path ".venv\Scripts\python.exe") { ".venv\Scripts\python.exe" } else { "python" }
& $python -m pip install -q -r requirements-build.txt
if ($LASTEXITCODE) { throw "安裝打包工具失敗" }

$version = (& $python -c "import huiji; print(huiji.__version__)").Trim()

& $python -m PyInstaller main.py `
    --name Huiji `
    --noconfirm --clean --windowed --onedir `
    --icon assets\icon.ico `
    --add-data "huiji\ui;huiji\ui" `
    --collect-all webview
if ($LASTEXITCODE) { throw "PyInstaller 失敗" }

Copy-Item README.md, THIRD_PARTY_NOTICES.md dist\Huiji\
$zip = "dist\Huiji-v$version-win64.zip"
if (Test-Path $zip) { Remove-Item $zip }
Compress-Archive -Path dist\Huiji -DestinationPath $zip
Write-Host "完成：$zip"

$iscc = @(
    (Get-Command iscc -ErrorAction SilentlyContinue).Source,
    "$env:LOCALAPPDATA\Programs\Inno Setup 6\ISCC.exe",
    "${env:ProgramFiles(x86)}\Inno Setup 6\ISCC.exe",
    "$env:ProgramFiles\Inno Setup 6\ISCC.exe"
) | Where-Object { $_ -and (Test-Path $_) } | Select-Object -First 1
if ($iscc) {
    & $iscc /Q "/DAppVersion=$version" installer\huiji.iss
    if ($LASTEXITCODE) { throw "Inno Setup 失敗" }
    Write-Host "完成：dist\Huiji-Setup-v$version.exe"
} else {
    Write-Host "沒有安裝 Inno Setup 6，略過安裝程式"
}
