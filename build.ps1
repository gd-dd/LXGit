param([string]$Python = 'E:/project/LXTools/LXTools/.venv/Scripts/python.exe')
$ErrorActionPreference = 'Stop'
Set-Location $PSScriptRoot
$buildCache = Join-Path $PSScriptRoot 'runtime/build-cache'
New-Item -ItemType Directory -Force $buildCache | Out-Null
$env:TEMP = $buildCache
$env:TMP = $buildCache
$env:PYTHONDONTWRITEBYTECODE = '1'
$env:PYINSTALLER_CONFIG_DIR = Join-Path $buildCache 'pyinstaller'
& $Python -m unittest discover -s tests -v
if ($LASTEXITCODE -ne 0) { throw '测试失败' }
& $Python -m PyInstaller --noconfirm --clean --onefile --console --paths $PSScriptRoot --name LXGit-worker --distpath bin --workpath build/worker --specpath build backend/worker.py
if ($LASTEXITCODE -ne 0) { throw '后台构建失败' }
& $Python scripts/prepare_package.py
if ($LASTEXITCODE -ne 0) { throw '清单生成失败' }
& $Python C:/Users/26318/.codex/skills/lx-tool-creator/scripts/validate_tool.py $PSScriptRoot
if ($LASTEXITCODE -ne 0) { throw '协议校验失败' }
& $Python C:/Users/26318/.codex/skills/lx-tool-creator/scripts/package_release.py $PSScriptRoot --output dist/LXTool-package.zip
if ($LASTEXITCODE -ne 0) { throw '打包失败' }
