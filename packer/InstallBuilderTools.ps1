New-Item /tmp -ItemType Directory -Force
Set-Location -Path /tmp

function InstallAwsTools {
    Write-Host "Downloading AWS cli"
    Invoke-WebRequest -Uri "https://awscli.amazonaws.com/AWSCLIV2.msi" -OutFile "C:\tmp\awscli.msi"
    Write-Host "Installing AWS cli"
    Start-Process "msiexec" -argumentlist "/quiet ALLUSERS=1 /i awscli.msi" -wait
    Write-Host "Deleting tmp files"
    Remove-Item -Force "awscli.msi"
}

function InstallGIT {
    Write-Host "Downloading GIT"
    Invoke-WebRequest -Uri "https://github.com/git-for-windows/git/releases/download/v2.28.0.windows.1/Git-2.28.0-64-bit.exe" -OutFile "C:\tmp\Git-2.28.0-64-bit.exe"
    Write-Host "Installing GIT"
    Start-Process "Git-2.28.0-64-bit.exe" -argumentlist "/silent /verysilent" -wait
    Write-Host "Deleting tmp files"
    Remove-Item -Force "Git-2.28.0-64-bit.exe"
}

function InstallGrafana {
    Write-Host "Downloading Grafana agent"
    Invoke-WebRequest -Uri "https://github.com/grafana/agent/releases/download/v0.30.2/grafana-agent-installer.exe" -OutFile "C:\tmp\grafana-agent-installer.exe"
    Write-Host "Installing Grafana agent"
    Start-Process "grafana-agent-installer.exe" -argumentlist "/S" -wait
    Remove-Item -Path "C:\tmp\grafana-agent-installer.exe"
}

function InstallExporter {
    Write-Host "Downloading windows-exporter"
    Invoke-WebRequest -Uri "https://github.com/prometheus-community/windows_exporter/releases/download/v0.20.0/windows_exporter-0.20.0-amd64.msi" -OutFile "C:\tmp\windows_exporter-0.20.0-amd64.msi"
    Write-Host "Installing windows-exporter"
    Start-Process "msiexec" -argumentlist "/quiet /i windows_exporter-0.20.0-amd64.msi ENABLED_COLLECTORS=cpu,cs,logical_disk,net,os,system" -wait
    Write-Host "Deleting tmp files"
    Remove-Item -Force -Path "windows_exporter-0.20.0-amd64.msi"
}

function AllowAppContainerRXAccess {
    param (
        $Path
    )

    $ACL = Get-ACL -Path $Path
    $AccessRule = New-Object System.Security.AccessControl.FileSystemAccessRule("ALL APPLICATION PACKAGES", "ReadAndExecute", "ContainerInherit, ObjectInherit", "None", "Allow")
    $ACL.AddAccessRule($AccessRule)
    $ACL | Set-Acl -Path $Path
}

function InstallBuildTools {
    New-Item -Path "/BuildTools" -ItemType Directory

    Write-Host "Installing CMake"
    Invoke-WebRequest -Uri "https://github.com/Kitware/CMake/releases/download/v3.28.3/cmake-3.28.3-windows-x86_64.zip" -OutFile "/tmp/cmake-win.zip"
    Expand-Archive -Path "/tmp/cmake-win.zip" -DestinationPath "/BuildTools"
    Rename-Item -Path "/BuildTools/cmake-3.28.3-windows-x86_64" -NewName "CMake"
    Remove-Item -Path "/tmp/cmake-win.zip"

    AllowAppContainerRXAccess -Path "C:\BuildTools\CMake"

    Write-Host "Installing Ninja"
    Invoke-WebRequest -Uri "https://github.com/compiler-explorer/ninja/releases/download/v1.12.1/ninja-win.zip" -OutFile "/tmp/ninja-win.zip"
    Expand-Archive -Path "/tmp/ninja-win.zip" -DestinationPath "/BuildTools/Ninja"
    Remove-Item -Path "/tmp/ninja-win.zip"

    AllowAppContainerRXAccess -Path "C:\BuildTools\Ninja"
}

function InstallPython {
    # note: conan 1.59 won't install with Python 3.12 (because of a dependency), so we use the last 3.10 where there's a .exe
    Write-Host "Downloading python"
    $url = "https://www.python.org/ftp/python/3.10.11/python-3.10.11-amd64.exe"
    Invoke-WebRequest -Uri $url -OutFile "/tmp/python-win.exe"
    Write-Host "Installing python"
    Start-Process -FilePath "/tmp/python-win.exe" -ArgumentList ("-quiet", "InstallAllUsers=1", "TargetDir=C:\BuildTools\Python") -NoNewWindow -Wait

    $env:PATH = "C:\BuildTools\Python;C:\BuildTools\Python\Scripts;$env:PATH"
}

function InstallConan {
    Write-Host "Installing conan"
    python -m pip install conan==1.59

    Write-Host "Configuring conan"
    conan remote clean
    conan remote add ceserver https://conan.compiler-explorer.com/ True
}

function Install7Zip {
    Write-Host "Downloading 7zip"
    Invoke-WebRequest -Uri "https://www.7-zip.org/a/7z2409-x64.exe" -OutFile "/tmp/7z.exe"
    Write-Host "Installing 7zip"
    Start-Process -FilePath "C:/tmp/7z.exe" -ArgumentList ("/S") -NoNewWindow -Wait
    Remove-Item -Path "/tmp/7z.exe"
}

function ConfigureSmbRights {
    $tmpfile = "c:\tmp\secpol.cfg"
    secedit /export /cfg $tmpfile
    $secpol = (Get-Content $tmpfile)

    $Value = $secpol | Where-Object{ $_ -like "MACHINE\SYSTEM\CurrentControlSet\Services\LanmanWorkstation\Parameters\AllowInsecureGuestAuth" }
    $Index = [array]::IndexOf($secpol,$Value)
    if ($Index -eq -1) {
        $Index = [array]::IndexOf($secpol, "[Registry Values]")
        $idx2 = $Index + 1
        $NewValue = "MACHINE\SYSTEM\CurrentControlSet\Services\LanmanWorkstation\Parameters\AllowInsecureGuestAuth=4,1"
        $newpol = $secpol[0..$Index]
        $newpol += ($NewValue)
        $newpol += $secpol[$idx2..$secpol.Length]
        $secpol = $newpol
    } else {
        $NewValue = "MACHINE\SYSTEM\CurrentControlSet\Services\LanmanWorkstation\Parameters\AllowInsecureGuestAuth=4,1"
        $secpol.item($Index) = $NewValue
    }

    $secpol | out-file $tmpfile -Force
    secedit /configure /db c:\windows\security\local.sdb /cfg $tmpfile
    Remove-Item -Path $tmpfile

    gpupdate /Force
}

function InitializeAgentConfig {
    Write-Host "Downloading Grafana config template"
    Invoke-WebRequest -Uri "https://raw.githubusercontent.com/compiler-explorer/infra/refs/heads/main/grafana/agent-win.yaml" -OutFile "/tmp/agent-win.yaml"

    Write-Host "Setting up Grafana Agent"
    $config = Get-Content -Path "/tmp/grafana/agent-win.yaml"
    $config = $config.Replace("@HOSTNAME@", "win-builder")
    $config = $config.Replace("@ENV@", "ce-ci")
    $prom_pass = ""
    try {
        $prom_pass = GetConf -Name "/compiler-explorer/promPassword"
    } catch {
    }
    $config = $config.Replace("@PROM_PASSWORD@", $prom_pass)
    Set-Content -Path "C:\Program Files\Grafana Agent\agent-config.yaml" -Value $config

    Stop-Service "Grafana Agent"
    $started = (Start-Service "Grafana Agent") -as [bool]
    if (-not $started) {
        Start-Service "Grafana Agent"
    }
}

ConfigureSmbRights
InstallAwsTools
InstallGIT
Install7Zip
InstallBuildTools
InstallGrafana
InitializeAgentConfig
InstallExporter
InstallPython
InstallConan
