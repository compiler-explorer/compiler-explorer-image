
param(
    $LogHost,
    $LogPort,
    $CeEnv,
    $HostnameForLogging,
    $SMBServer
)

function MountZ {
    $exists = (Get-SmbMapping -LocalPath 'Z:') -as [bool]
    if ($exists) {
         Remove-SmbMapping -LocalPath 'Z:' -Force
         $exists = $False
    }

    while (-not $exists) {
        try {
            Write-Host "Mapping Z:"
            $exists = (New-SmbMapping -LocalPath 'Z:' -RemotePath "\\$SMBServer\winshared") -as [bool]
        } catch {
        }
    }
}

MountZ

$env:NODE_ENV = "production"
$env:PATH = "$env:PATH;Z:/compilers/mingw-8.1.0/mingw64/bin"

# $nodeargs = ("--max_old_space_size=6000","--","app.js","--debug","--dist","--port","10240","--metricsPort","10241","--suppressConsoleLog","--logHost",$LogHost,"--logPort",$LogPort,"--env","amazonwin","--env",$CeEnv,"--language","c++","--propDebug","true")
$nodeargs = ("--max_old_space_size=6000","--","app.js","--dist","--port","10240","--metricsPort","10241","--suppressConsoleLog","--logHost",$LogHost,"--logPort",$LogPort,"--hostnameForLogging",$HostnameForLogging,"--env","amazonwin","--env",$CeEnv,"--language","c,c++")

Set-Location -Path "C:\compilerexplorer"

& 'C:\Program Files\nodejs\node.exe' $nodeargs
