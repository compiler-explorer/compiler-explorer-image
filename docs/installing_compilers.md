# Installing compilers

## On Linux

The directory `/opt/compiler-explorer` is required, otherwise you'll have to supply your own destination directory and temporary staging directory to `ce_install` using `--staging-dir "/some/tmp/dir" --dest "/my/ce/dir"`

### Listing available compilers/tools

`ce_install list` or `ce_install --enable nightly list`

### For versioned compilers/tools

`ce_install install <name>`

### For nightlies:

`ce_install --enable nightly install <name>`

## On Windows

You can install a small amount of Windows compilers using ce_install.ps1, a regular powershell installation should be enough

`./ce_install.ps1 --staging-dir "D:/tmp/staging" --dest "D:/efs/compilers" --enable windows install windows`

### On CE infrastructure

`ce_install --staging-dir /efs/winshared/staging --dest /efs/winshared/compilers --enable windows install windows`

!no sudo!

`ce smb sync`
