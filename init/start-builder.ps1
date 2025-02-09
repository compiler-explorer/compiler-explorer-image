
param(
    [String] $ConanPassword,
    [String] $Language,
    [String] $Library,
    [String] $Compiler
)

if ($ConanPassword -eq "") {
    Write-Error "ConanPassword parameter required"
    exit
}
if ($Language -eq "") {
    Write-Error "Language parameter required"
    exit
}
if ($Library -eq "") {
    Write-Error "Library parameter required"
    exit
}
if ($Compiler -eq "") {
    Write-Error "Compiler parameter required"
    exit
}


do {
  $ping = test-connection -comp "s3.amazonaws.com" -count 1 -Quiet
} until ($ping)

[Environment]::SetEnvironmentVariable("PYTHON_KEYRING_BACKEND", 'keyring.backends.null.Keyring', [System.EnvironmentVariableTarget]::Process);

$env:PATH = "$env:PATH;C:\BuildTools\Python;C:\BuildTools\Python\Scripts;C:\BuildTools\Ninja;Z:\compilers\windows-kits-10\bin;C:\BuildTools\CMake\bin;Z:\compilers\mingw-w64-13.1.0-16.0.2-11.0.0-ucrt-r1\bin;C:\Program Files\Amazon\AWSCLIV2"

function FetchInfra {
    Set-Location -Path "/tmp"

    $infra = "/tmp/infra"
    if (Test-Path -Path $infra) {
        Set-Location -Path "/tmp/infra"

        Remove-Item -Path ".env" -Recurse
        Remove-Item -Path ".venv" -Recurse
        Remove-Item -Path ".poetry" -Recurse
        Remove-Item -Path ".mypy_cache" -Recurse

        git reset --hard
        git pull
    } else {
        git clone https://github.com/compiler-explorer/infra
    }

    Set-Location -Path "/tmp/infra"
    & ./ce_install.ps1 --help
}

function ConfigureConan {
    $conan_home = conan config home
    Copy-Item -Path "/tmp/infra/init/settings.yml" -Destination "${conan_home}/settings.yml"

    conan remote clean
    conan remote add ceserver https://conan.compiler-explorer.com/ True

    $env:CONAN_USER = "ce";
    $env:CONAN_PASSWORD = $ConanPassword;

    conan user ce -p -r=ceserver
}

function GetConf {
    Param(
        $Name,
        $Default = ""
    )

    try {
        return (aws ssm get-parameter --name "$Name" | ConvertFrom-Json).Parameter.Value
    }
    catch {
        Write-Host "GetConf($Name) raised Exception: $_"
        return $Default
    }
}

function GetSMBServerIP {
    Param(
        [string] $CeEnv
    )

    $configPath = "/compiler-explorer/smbserverProd"
    $smbserver = GetConf -Name $configPath -Default "smb-address-unknown";

    return $smbserver
}

function MountZ {
    $smb_ip = GetSMBServerIP -CeEnv "prod"
    $exists = Test-Path -Path "Z:\"
    while (-not $exists) {
        try {
            Write-Host "Mapping Z:"
            $exists = (New-SmbMapping -LocalPath "Z:" -RemotePath "\\$smb_ip\winshared") -as [bool]
        } catch {
            Write-Host "New-SmbMapping for Z:\ -> \\$smb_ip\winshared failed: $_"
        }
    }
}

MountZ

FetchInfra

ConfigureConan


Set-Location /tmp/infra


$FORCECOMPILERPARAM = ""
if ( $Compiler -eq "popular-compilers-only" ) {
  $FORCECOMPILERPARAM = "--popular-compilers-only"
} elseif ( $Compiler -ne "all" ) {
  $FORCECOMPILERPARAM = "--buildfor=$Compiler"
}

$LIBRARYPARAM = "libraries/$Language"

if ($Library -ne "all") {
  $LIBRARYPARAM = "libraries/$Language/$Library"
}

pwsh .\ce_install.ps1 --staging-dir "C:/tmp/staging" --dest "C:/tmp/staging" --enable windows build --temp-install "$FORCECOMPILERPARAM" "$LIBRARYPARAM"
