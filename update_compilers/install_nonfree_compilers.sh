#!/usr/bin/env bash

# This script installs all the non-free compilers from s3 into a dir in /opt.
# On EC2 this location is on an EFS drive.

DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=common.inc
. "${DIR}/common.inc"
S3URL=s3://compiler-explorer/opt-nonfree

CE_INSTALL_ARG+=" --enable=non-free"

##################################
# Non-free tools
ce_install tools
ce_squash tools

##################################
# EDG compilers
ce_install edg
ce_squash edg

##################################
# QNX compilers
ce_install qnx
ce_squash qnx

##################################
# ORCA/C compilers
ce_install orca-c
ce_squash orca-c

##################################
# Intel compilers
for compiler in \
    intel.tar.gz; do
    DIR=${compiler%.tar.*}
    if [[ ! -d ${DIR} ]]; then
        s3fetch ${S3URL}/$compiler | tar zxf -
        do_strip "${DIR}"
    fi
done

for license in COM_L__CPPFOR_HFGW-87P5C9BZ.lic NCOM_L__CPPFOR_ND83-JL4ZKB6T.lic; do
    mkdir -p /opt/intel/licenses
    s3get ${S3URL}/$license /opt/intel/licenses/$license # NB not ${OPT} as we need this actually at this absolute path
done

mkdir -p /opt/qnx
s3get ${S3URL}/qnx-licenses /opt/qnx/licenses

for version in 2016.3.210 2018.0.033; do
    if [[ ! -d intel-${version} ]]; then
        compiler=intel-${version}.tar.xz
        s3fetch ${S3URL}/$compiler | tar Jxf -
    fi
done

##################################
# Windows compilers
fix_up_windows() {
    local file=$1
    if [[ -d "${file}/lib/native/bin/amd64" ]]; then
        cp "${file}/lib/native/bin/amd64/mspdb140.dll" "${file}/lib/native/bin/amd64/msvcdis140.dll" "${file}/lib/native/bin/amd64_arm/"
        cp "${file}/lib/native/bin/amd64/mspdb140.dll" "${file}/lib/native/bin/amd64_x86/"
    fi
    if [[ -d "${file}/bin/amd64" ]]; then
        cp "${file}/bin/amd64/mspdb140.dll" "${file}/bin/amd64/msvcdis140.dll" "${file}/bin/amd64_arm/"
        cp "${file}/bin/amd64/mspdb140.dll" "${file}/bin/amd64_x86/"
    fi
    if [[ -d "${file}/bin/Hostx64" ]]; then
        cp "${file}/bin/Hostx64/x64/mspdbcore.dll" "${file}/bin/Hostx64/x64/mspdb140.dll" "${file}/bin/Hostx64/x86/"
        cp "${file}/bin/Hostx64/x64/mspdbcore.dll" "${file}/bin/Hostx64/x64/mspdb140.dll" "${file}/bin/Hostx64/x64/msvcdis140.dll" "${file}/bin/Hostx64/arm/"
        cp "${file}/bin/Hostx64/x64/mspdbcore.dll" "${file}/bin/Hostx64/x64/mspdb140.dll" "${file}/bin/Hostx64/x64/msvcdis140.dll" "${file}/bin/Hostx64/arm64/"
    fi
}

mkdir -p windows
pushd windows
for file in \
    10.0.10240.0 \
    14.0.24224-Pre \
    19.00.24210 \
    19.10.25017 \
    19.14.26423; do
    if [[ ! -d ${file} ]]; then
        s3fetch ${S3URL}/${file}.tar.xz ${file}.tar.xz | tar Jxf -
        fix_up_windows ${file}
    fi
done
popd

##################################
# Zapcc
for version in 20170226-190308-1.0; do
    fullname=zapcc-${version}
    if [[ ! -d ${fullname} ]]; then
        compiler=${fullname}.tar.gz
        s3fetch ${S3URL}/${compiler} "${OPT}/$compiler" | tar zxf -
        s3get ${S3URL}/zapcc-key.txt "${OPT}/${fullname}/bin/zapcc-key.txt"
    fi
done

##################################
# CUDA
install_cuda() {
    local URL=$1
    mkdir -p cuda
    pushd cuda
    local DIR
    DIR=$(pwd)/$2
    if [[ ! -d ${DIR} ]]; then
        rm -rf" ${OPT}/tmp/cuda"
        mkdir -p "${OPT}/tmp/cuda"
        fetch "${URL}" >"${OPT}/tmp/cuda/combined.sh"
        sh "${OPT}/tmp/cuda/combined.sh" --extract="${OPT}/tmp/cuda"
        local LINUX
        LINUX=$(ls -1 "${OPT}/tmp/cuda/cuda-linux.$2"*.run 2>/dev/null || true)
        if [[ -f ${LINUX} ]]; then
            ${LINUX} --prefix="${DIR}" -noprompt -nosymlink -no-man-page
        else
            # As of CUDA 10.1, the toolkit is already extracted here.
            mv "${OPT}/tmp/cuda/cuda-toolkit" "${DIR}"
        fi
        rm -rf "${OPT}/tmp/cuda"
    fi
    popd
}

install_cuda https://developer.nvidia.com/compute/cuda/9.1/Prod/local_installers/cuda_9.1.85_387.26_linux 9.1.85
install_cuda https://developer.nvidia.com/compute/cuda/9.2/Prod/local_installers/cuda_9.2.88_396.26_linux 9.2.88
install_cuda https://developer.nvidia.com/compute/cuda/10.0/Prod/local_installers/cuda_10.0.130_410.48_linux 10.0.130
install_cuda https://developer.nvidia.com/compute/cuda/10.1/Prod/local_installers/cuda_10.1.105_418.39_linux.run 10.1.105
install_cuda https://developer.nvidia.com/compute/cuda/10.1/Prod/local_installers/cuda_10.1.168_418.67_linux.run 10.1.168
install_cuda https://developer.download.nvidia.com/compute/cuda/10.1/Prod/local_installers/cuda_10.1.243_418.87.00_linux.run 10.1.243
install_cuda https://developer.download.nvidia.com/compute/cuda/10.2/Prod/local_installers/cuda_10.2.89_440.33.01_linux.run 10.2.89

##################################
# Sonar Analyzer
s3fetch ${S3URL}/sonar/sonar.txz | tar Jxf -
