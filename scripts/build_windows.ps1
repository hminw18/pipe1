[CmdletBinding()]
param(
    [string]$ClientEnvFile = "config\pipe1.client.env",
    [string]$DistPath = "dist",
    [string]$WorkPath = "build\pyinstaller",
    [string]$PythonExe = "",
    [switch]$SkipTests,
    [switch]$UseExampleConfig,
    [switch]$NoClean
)

$ErrorActionPreference = "Stop"

$Root = Resolve-Path (Join-Path $PSScriptRoot "..")

function Read-PipeEnvFile {
    param([string]$Path)

    $Values = @{}
    foreach ($Line in Get-Content -LiteralPath $Path -Encoding UTF8) {
        $Trimmed = $Line.Trim()
        if ($Trimmed.Length -eq 0 -or $Trimmed.StartsWith("#") -or -not $Trimmed.Contains("=")) {
            continue
        }

        $Parts = $Trimmed.Split("=", 2)
        $Key = $Parts[0].Trim()
        $Value = $Parts[1].Trim()
        if ($Value.Length -ge 2) {
            $First = $Value.Substring(0, 1)
            $Last = $Value.Substring($Value.Length - 1, 1)
            if (($First -eq '"' -and $Last -eq '"') -or ($First -eq "'" -and $Last -eq "'")) {
                $Value = $Value.Substring(1, $Value.Length - 2)
            }
        }
        if ($Key.Length -gt 0) {
            $Values[$Key] = $Value
        }
    }
    return $Values
}

function Assert-ProductionClientEnv {
    param([string]$Path)

    $Config = Read-PipeEnvFile -Path $Path
    $AppEnv = ""
    if ($Config.ContainsKey("PIPE1_APP_ENV")) {
        $AppEnv = [string]$Config["PIPE1_APP_ENV"]
    }
    $AppEnv = $AppEnv.ToLowerInvariant()
    if ($AppEnv -notin @("prod", "production")) {
        throw "PIPE1_APP_ENV must be prod or production in $Path"
    }

    $ApiBaseUrl = ""
    if ($Config.ContainsKey("PIPE1_LICENSE_API_BASE_URL")) {
        $ApiBaseUrl = [string]$Config["PIPE1_LICENSE_API_BASE_URL"]
    }
    if (-not $ApiBaseUrl.StartsWith("https://")) {
        throw "PIPE1_LICENSE_API_BASE_URL must use https:// in $Path"
    }

    $PublicKeysRaw = ""
    if ($Config.ContainsKey("PIPE1_LICENSE_PUBLIC_KEYS")) {
        $PublicKeysRaw = [string]$Config["PIPE1_LICENSE_PUBLIC_KEYS"]
    }
    try {
        $PublicKeys = $PublicKeysRaw | ConvertFrom-Json
    }
    catch {
        throw "PIPE1_LICENSE_PUBLIC_KEYS must be valid JSON in $Path"
    }
    if ($null -eq $PublicKeys -or $PublicKeys.PSObject.Properties.Count -eq 0) {
        throw "PIPE1_LICENSE_PUBLIC_KEYS must contain at least one key in $Path"
    }
}

function Restore-EnvValue {
    param(
        [string]$Name,
        [AllowNull()][string]$Value
    )

    if ($null -eq $Value) {
        Remove-Item -Path "Env:$Name" -ErrorAction SilentlyContinue
    }
    else {
        Set-Item -Path "Env:$Name" -Value $Value
    }
}

Push-Location $Root
try {
    if (-not $PythonExe) {
        $VenvPython = Join-Path $Root.Path ".venv\Scripts\python.exe"
        if (Test-Path -LiteralPath $VenvPython) {
            $PythonExe = $VenvPython
        }
        else {
            $PythonExe = "python"
        }
    }

    & $PythonExe -m PyInstaller --version | Out-Null

    foreach ($RequiredPath in @("font", "pipe.png", "pipe_template_calibration.json", "packaging\pipe1_desktop.spec")) {
        if (-not (Test-Path -LiteralPath $RequiredPath)) {
            throw "Required build input is missing: $RequiredPath"
        }
    }

    if ($UseExampleConfig) {
        $ResolvedClientEnv = Resolve-Path "config\pipe1.client.env.example"
        Write-Warning "Building with example client env. The app will not be production-configured."
    }
    else {
        if (-not (Test-Path -LiteralPath $ClientEnvFile)) {
            throw "Client env file is missing: $ClientEnvFile"
        }
        $ResolvedClientEnv = Resolve-Path $ClientEnvFile
        Assert-ProductionClientEnv -Path $ResolvedClientEnv.Path
    }

    if (-not $SkipTests) {
        & $PythonExe -m pytest -q
        if ($LASTEXITCODE -ne 0) {
            throw "Tests failed."
        }
    }

    $OldBuildClientEnv = $env:PIPE1_BUILD_CLIENT_ENV_FILE
    $OldProjectRoot = $env:PIPE1_PROJECT_ROOT
    $env:PIPE1_BUILD_CLIENT_ENV_FILE = $ResolvedClientEnv.Path
    $env:PIPE1_PROJECT_ROOT = $Root.Path

    try {
        $PyInstallerArgs = @(
            "-m",
            "PyInstaller",
            "packaging\pipe1_desktop.spec",
            "--noconfirm",
            "--distpath",
            $DistPath,
            "--workpath",
            $WorkPath
        )
        if (-not $NoClean) {
            $PyInstallerArgs += "--clean"
        }

        & $PythonExe @PyInstallerArgs
        if ($LASTEXITCODE -ne 0) {
            throw "PyInstaller build failed."
        }
    }
    finally {
        Restore-EnvValue -Name "PIPE1_BUILD_CLIENT_ENV_FILE" -Value $OldBuildClientEnv
        Restore-EnvValue -Name "PIPE1_PROJECT_ROOT" -Value $OldProjectRoot
    }

    $ExePath = Join-Path $Root.Path (Join-Path $DistPath "PIPE1\PIPE1.exe")
    if (-not (Test-Path -LiteralPath $ExePath)) {
        throw "Build finished but executable was not found: $ExePath"
    }

    Write-Host "Built: $ExePath"
}
finally {
    Pop-Location
}
