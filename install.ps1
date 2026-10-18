# Carrot Leaf installer for Windows.
#
#   powershell -ExecutionPolicy ByPass -c "irm https://raw.githubusercontent.com/Yore-Des/carrot-leaf/main/install.ps1 | iex"
#
# Installs uv if it's missing, then installs Carrot Leaf with its own Python.
$ErrorActionPreference = "Stop"
$source = if ($env:CARROT_LEAF_SOURCE) { $env:CARROT_LEAF_SOURCE } else { "carrot-leaf" }

Write-Host "  \|/  Carrot Leaf installer" -ForegroundColor DarkYellow

if (-not (Get-Command uv -ErrorAction SilentlyContinue)) {
    Write-Host "-> Installing uv (Python package manager)..."
    powershell -ExecutionPolicy ByPass -c "irm https://astral.sh/uv/install.ps1 | iex"
    $env:Path = "$env:USERPROFILE\.local\bin;$env:Path"
}

Write-Host "-> Installing Carrot Leaf..."
uv tool install --python 3.12 --upgrade $source
uv tool update-shell | Out-Null

Write-Host "Done. Open a new terminal and run:  carrot" -ForegroundColor Green
