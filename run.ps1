# Study Sprint launcher (Windows PowerShell).
#   .\run.ps1 serve [--demo] [--port 8000]   run the web app on http://127.0.0.1:8000
#   .\run.ps1 build "volcanoes" --grade 4    build one lesson with the agents
#   .\run.ps1 test [pytest args]             unit, API and browser tests (offline, free)
#   .\run.ps1 eval [--run runs\<id>]         score a saved lesson + red-team (free)
#   .\run.ps1 eval --live                    build the topic x grade matrix with live agents (costs API credits)
#   .\run.ps1 guide                          screenshots + docs\how-it-works.pdf
#   .\run.ps1 install-browser                install Chromium for Playwright INSIDE the project
#
# These variables must be set before Python starts. Setting them here keeps every cache
# inside the project, and the Python code never has to touch the environment (CLAUDE.md).
$ErrorActionPreference = "Stop"
$root = $PSScriptRoot
$env:PYTHONPYCACHEPREFIX = Join-Path $root ".cache\pycache"
$env:PLAYWRIGHT_BROWSERS_PATH = Join-Path $root ".cache\ms-playwright"
$py = Join-Path $root ".venv\Scripts\python.exe"
Set-Location $root
$cmd = $args[0]
# Always an array: with one extra argument, PowerShell would otherwise splat a string character by character.
$rest = @($args | Select-Object -Skip 1)
switch ($cmd) {
    "test"            { & $py -m pytest @rest }
    "eval"            { & $py -m evals.run_evals @rest }
    "install-browser" { & $py -m playwright install chromium }
    default           { & $py -m studysprint @args }
}
exit $LASTEXITCODE
