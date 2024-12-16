## CEFS

The Compiler Explorer File System manages a read-only, layered, hierarchy of squashfs images.
It's designed for compilers and libraries in Compiler Explorer, but any read-only content would work. It's not really a file system, either. So it's a terrible name. But hey!

### Why!

We use immutable squashfs images on NFS for the compilers and libraries. That's great, fast performance, great caching behaviour. But, mounting each compiler squashfs individually is slow for system startup (~1m for ~1000). We ideally want fewer squashfs mounts, but with the ability to "overlay" changes (like daily builds) without totally rebuilding everything every time.

### Overview

There is an autofs mount at `/cefs` which automounts squashfs images of the form `/cefs/ID` to `/opt/cefs-images/ID.sqfs`. The filesystem root is a symlink to a squashfs image in cefs. The root itself is a number of symlinks - one per managed directory - to other locations in cefs.

As an example:

- `/opt/compiler-explorer` would be the root, and is a symlink to `/cefs/SOME-ROOT-ID`.
- `/cefs/SOME_ROOT_ID` maps to a squashfs image `/opt/cefs-images/SOME_ROOT_ID.sqfs`.
- `/opt/cefs-images/SOME_ROOT_ID.sqfs` contains symlinks for each compiler maybe something like:
  - `gcc-12.1.0` -> `/cefs/SOME_INSTALL_ID/gcc-12.1.0`
  - `gcc-12.2.0` -> `/cefs/SOME_INSTALL_ID/gcc-12.2.0`
  - `gcc-trunk` -> `/cefs/DIFFERENT_ID/gcc-trunk`
- Here we see the advantage of cefs: we minimize the number of actual squashfs images by relying on several installations being in the "same" squashfs image. We can consolidate images over time too.

The expectation is there's one big squashfs install image holding all the stable compilers, regenerated monthly. And then several overlaying images, some for ad hoc builds of new compilers (e.g. new releases of GCC), and some for the daily builds.

## Using

To set up you'll need to run `sudo cefs install`. (it needs root to fiddle with autofs)

I locally link `/opt/compiler-explorer` to `~/ce` in this instance so I can update it quickly as a mortal user without making `/opt` writable to my user.

To make an empty root like this:

```
$ sudo ln -sfT ~/ce /opt/compiler-explorer  # one-time setup only as root
$ cefs create-root ~/ce
...lots of output...
Fresh new cefs root created at /cefs/662a3bd6f57ac05b28032dbb5dd2bb489e9761c7
$ ls /opt/compiler-explorer
metadata.txt
$ cat /opt/compiler-explorer/metadata.txt
Initial empty image created at 2022-09-05 22:56:03.530590 by mgodbolt
```

Now you have an empty base layer.  After that you can layer on things using `ce_install buildroot` (terrible name needs changing):

```
$ ce_install buildroot gcc # literally all the GCCs...
[...lots of work...]
$ ls -l /opt/compiler-explorer/
$ ls -l /opt/compiler-explorer/
lrwxrwxrwx 1 root root  57 Sep  5 18:02 gcc-12.1.0 -> /cefs/fad719bc481ff3dbaffc7f5ef804c4b5ffc281d1/gcc-12.1.0/
lrwxrwxrwx 1 root root  57 Sep  5 18:02 gcc-12.2.0 -> /cefs/fad719bc481ff3dbaffc7f5ef804c4b5ffc281d1/gcc-12.2.0/
...etc
```

## TODO

- cleanup
- consolidation
- everything, really
