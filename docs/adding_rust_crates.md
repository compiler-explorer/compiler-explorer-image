# How to add Rust crates to CE

## To keep the Top100 up-to-date

1. Run `bin/ce_install addtoprustcrates`
   - Commit the resulting `libraries.yaml` changes
2. Run `bin/ce_install generaterustprops`
   - Copy paste the contents from the generated `props` file to https://github.com/compiler-explorer/compiler-explorer/blob/main/etc/config/rust.amazon.properties
3. Wait for the crates to build at night
4. Etc.

## Adding a single crate

1. Run `bin/ce_install addcrate mycratename versionnumber`
2. Run `bin/ce_install generaterustprops`
   - Copy paste the contents from the generated `props` file to https://github.com/compiler-explorer/compiler-explorer/blob/main/etc/config/rust.amazon.properties
3. Wait for the crates to build at night
4. Etc.

## Testing a crate build

1. Run `bin/ce_install --buildfor r1610 --dry_run --debug build`
2. In `/opt/compiler-explorer/staging` will be a bunch of folders that you can look into
   - `source_libname_version` will be where the crate source, our buildscript `build.sh` and the `buildlog.txt` will be
   - `r1610_hash` will be where the `conanfile.py` and the built binaries will be
