[CmdletBinding()]
param(
    [string]$ClientEnvFile = "config\pipe1.client.env",
    [string]$DistPath = "dist",
    [string]$WorkPath = "build\pyinstaller",
    [string]$OutputPath = "dist\msi",
    [string]$AppVersion = "",
    [string]$PythonExe = "",
    [string]$DotnetExe = "",
    [string]$WixExe = "",
    [string]$DotnetChannel = "8.0",
    [switch]$SkipAppBuild,
    [switch]$SkipTests,
    [switch]$UseExampleConfig,
    [switch]$NoClean,
    [switch]$NoToolDownload,
    [switch]$AcceptWixEula
)

$ErrorActionPreference = "Stop"

$Root = Resolve-Path (Join-Path $PSScriptRoot "..")
$ToolsDir = Join-Path $Root.Path ".tools"
$DotnetInstallScript = Join-Path $ToolsDir "dotnet-install.ps1"
$LocalDotnetDir = Join-Path $ToolsDir "dotnet"
$LocalWixDir = Join-Path $ToolsDir "wix"

function Get-ProjectVersion {
    param([string]$ProjectRoot)

    $PyprojectPath = Join-Path $ProjectRoot "pyproject.toml"
    if (-not (Test-Path -LiteralPath $PyprojectPath)) {
        throw "pyproject.toml was not found: $PyprojectPath"
    }

    $Match = Select-String -LiteralPath $PyprojectPath -Pattern '^\s*version\s*=\s*"([^"]+)"\s*$' | Select-Object -First 1
    if ($null -eq $Match) {
        throw "Project version was not found in $PyprojectPath"
    }

    return $Match.Matches[0].Groups[1].Value
}

function Convert-ToMsiVersion {
    param([string]$Version)

    $Match = [regex]::Match($Version, '^(\d+)(?:\.(\d+))?(?:\.(\d+))?')
    if (-not $Match.Success) {
        throw "AppVersion must start with a numeric MSI-compatible version: $Version"
    }

    $Parts = @(
        $Match.Groups[1].Value,
        $(if ($Match.Groups[2].Success) { $Match.Groups[2].Value } else { "0" }),
        $(if ($Match.Groups[3].Success) { $Match.Groups[3].Value } else { "0" })
    )
    return ($Parts -join ".")
}

function Get-CommandSource {
    param([string]$Name)

    $Command = Get-Command $Name -ErrorAction SilentlyContinue
    if ($null -eq $Command) {
        return $null
    }
    return $Command.Source
}

function Resolve-Dotnet {
    param(
        [string]$ExplicitDotnetExe,
        [bool]$AllowDownload
    )

    if ($ExplicitDotnetExe) {
        if (-not (Test-Path -LiteralPath $ExplicitDotnetExe)) {
            throw "dotnet executable was not found: $ExplicitDotnetExe"
        }
        return (Resolve-Path -LiteralPath $ExplicitDotnetExe).Path
    }

    $GlobalDotnet = Get-CommandSource -Name "dotnet"
    if ($GlobalDotnet) {
        return $GlobalDotnet
    }

    $LocalDotnet = Join-Path $LocalDotnetDir "dotnet.exe"
    if (Test-Path -LiteralPath $LocalDotnet) {
        return $LocalDotnet
    }

    if (-not $AllowDownload) {
        throw "dotnet was not found. Install .NET SDK, pass -DotnetExe, or omit -NoToolDownload."
    }

    New-Item -ItemType Directory -Force -Path $ToolsDir | Out-Null
    if (-not (Test-Path -LiteralPath $DotnetInstallScript)) {
        Invoke-WebRequest `
            -Uri "https://dot.net/v1/dotnet-install.ps1" `
            -OutFile $DotnetInstallScript `
            -UseBasicParsing
    }

    $InstallOutput = & powershell -NoProfile -ExecutionPolicy Bypass -File $DotnetInstallScript `
        -Channel $DotnetChannel `
        -InstallDir $LocalDotnetDir `
        -NoPath
    $InstallExitCode = $LASTEXITCODE
    $InstallOutput | ForEach-Object { Write-Host $_ }
    if ($InstallExitCode -ne 0) {
        throw ".NET SDK download/install failed."
    }

    if (-not (Test-Path -LiteralPath $LocalDotnet)) {
        throw ".NET SDK install finished but dotnet.exe was not found: $LocalDotnet"
    }
    return $LocalDotnet
}

function Resolve-Wix {
    param(
        [string]$ExplicitWixExe,
        [string]$ResolvedDotnetExe,
        [bool]$AllowDownload
    )

    if ($ExplicitWixExe) {
        if (-not (Test-Path -LiteralPath $ExplicitWixExe)) {
            throw "WiX executable was not found: $ExplicitWixExe"
        }
        return (Resolve-Path -LiteralPath $ExplicitWixExe).Path
    }

    $GlobalWix = Get-CommandSource -Name "wix"
    if ($GlobalWix) {
        return $GlobalWix
    }

    $LocalWix = Join-Path $LocalWixDir "wix.exe"
    if (Test-Path -LiteralPath $LocalWix) {
        return $LocalWix
    }

    if (-not $AllowDownload) {
        throw "WiX CLI was not found. Install WiX Toolset, pass -WixExe, or omit -NoToolDownload."
    }

    New-Item -ItemType Directory -Force -Path $LocalWixDir | Out-Null
    $InstallOutput = & $ResolvedDotnetExe tool install wix --tool-path $LocalWixDir
    $InstallExitCode = $LASTEXITCODE
    $InstallOutput | ForEach-Object { Write-Host $_ }
    if ($InstallExitCode -ne 0) {
        throw "WiX CLI install failed."
    }

    if (-not (Test-Path -LiteralPath $LocalWix)) {
        throw "WiX CLI install finished but wix.exe was not found: $LocalWix"
    }
    return $LocalWix
}

function Ensure-WixExtension {
    param(
        [string]$ResolvedWixExe,
        [string]$ExtensionRef
    )

    $InstalledExtensions = & $ResolvedWixExe extension list
    if ($LASTEXITCODE -ne 0) {
        throw "WiX extension list failed."
    }
    if ($InstalledExtensions -match [regex]::Escape($ExtensionRef.Split("/")[0])) {
        return
    }

    $InstallOutput = & $ResolvedWixExe extension add $ExtensionRef
    $InstallExitCode = $LASTEXITCODE
    $InstallOutput | ForEach-Object { Write-Host $_ }
    if ($InstallExitCode -ne 0) {
        throw "WiX extension install failed: $ExtensionRef"
    }
}

function Invoke-Checked {
    param(
        [string]$FilePath,
        [string[]]$Arguments,
        [string]$FailureMessage
    )

    & $FilePath @Arguments
    if ($LASTEXITCODE -ne 0) {
        throw $FailureMessage
    }
}

Push-Location $Root
try {
    if (-not $AppVersion) {
        $AppVersion = Get-ProjectVersion -ProjectRoot $Root.Path
    }
    $MsiVersion = Convert-ToMsiVersion -Version $AppVersion

    if (-not $SkipAppBuild) {
        $BuildWindowsScript = Join-Path $PSScriptRoot "build_windows.ps1"
        $BuildArgs = @{
            ClientEnvFile = $ClientEnvFile
            DistPath = $DistPath
            WorkPath = $WorkPath
        }
        if ($PythonExe) {
            $BuildArgs["PythonExe"] = $PythonExe
        }
        if ($SkipTests) {
            $BuildArgs["SkipTests"] = $true
        }
        if ($UseExampleConfig) {
            $BuildArgs["UseExampleConfig"] = $true
        }
        if ($NoClean) {
            $BuildArgs["NoClean"] = $true
        }

        & $BuildWindowsScript @BuildArgs
        if ($LASTEXITCODE -ne 0) {
            throw "Windows app build failed."
        }
    }

    $SourceDir = Join-Path $Root.Path (Join-Path $DistPath "PIPE1")
    $ExePath = Join-Path $SourceDir "PIPE1.exe"
    if (-not (Test-Path -LiteralPath $ExePath)) {
        throw "Application executable was not found: $ExePath"
    }

    $InternalPath = Join-Path $SourceDir "_internal"
    if (-not (Test-Path -LiteralPath $InternalPath)) {
        throw "PyInstaller internal dependency directory was not found: $InternalPath"
    }

    $AllowToolDownload = -not $NoToolDownload
    $ResolvedDotnet = Resolve-Dotnet -ExplicitDotnetExe $DotnetExe -AllowDownload $AllowToolDownload
    $ResolvedWix = Resolve-Wix -ExplicitWixExe $WixExe -ResolvedDotnetExe $ResolvedDotnet -AllowDownload $AllowToolDownload

    $OldDotnetRoot = $env:DOTNET_ROOT
    $OldPath = $env:PATH
    $env:DOTNET_ROOT = Split-Path -Parent $ResolvedDotnet
    $env:PATH = "$env:DOTNET_ROOT;$LocalWixDir;$OldPath"

    try {
        if ($AcceptWixEula) {
            Invoke-Checked -FilePath $ResolvedWix -Arguments @("eula", "accept", "wix7") -FailureMessage "WiX EULA acceptance failed."
        }

        Invoke-Checked -FilePath $ResolvedWix -Arguments @("--version") -FailureMessage "WiX CLI version check failed."

        $WixSource = Join-Path $Root.Path "packaging\wix\Pipe1Inspector.wxs"
        $WixUiSource = Join-Path $Root.Path "packaging\wix\Pipe1Ui.wxs"
        $WixLicenseRtf = Join-Path $Root.Path "packaging\wix\License.rtf"
        $WixIcon = Join-Path $Root.Path "packaging\assets\pipe1.ico"
        $WixBannerBmp = Join-Path $Root.Path "packaging\assets\wix-banner.bmp"
        $OutputDir = Join-Path $Root.Path $OutputPath
        $IntermediateDir = Join-Path $Root.Path "build\wix"
        $MsiPath = Join-Path $OutputDir "PIPE1-$MsiVersion.msi"

        New-Item -ItemType Directory -Force -Path $OutputDir | Out-Null
        New-Item -ItemType Directory -Force -Path $IntermediateDir | Out-Null

        $WixArgs = @(
            "build",
            $WixSource,
            $WixUiSource,
            "-arch", "x64",
            "-d", "SourceDir=$SourceDir",
            "-d", "AppName=PIPE1",
            "-d", "Manufacturer=PIPE1",
            "-d", "AppVersion=$MsiVersion",
            "-d", "WixLicenseRtf=$WixLicenseRtf",
            "-d", "WixIcon=$WixIcon",
            "-d", "WixBannerBmp=$WixBannerBmp",
            "-intermediateFolder", $IntermediateDir,
            "-out", $MsiPath
        )
        Invoke-Checked -FilePath $ResolvedWix -Arguments $WixArgs -FailureMessage "MSI build failed."
    }
    finally {
        $env:DOTNET_ROOT = $OldDotnetRoot
        $env:PATH = $OldPath
    }

    if (-not (Test-Path -LiteralPath $MsiPath)) {
        throw "MSI build finished but output was not found: $MsiPath"
    }

    Write-Host "Built MSI: $MsiPath"
}
finally {
    Pop-Location
}
