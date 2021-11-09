#!/bin/bash

set -exuo pipefail

finish() {
    ce builder stop
}
trap finish EXIT

ce builder status
ce builder start

LOG_DIR=~/build_logs
BUILD_FAILED=0
run_on_build() {
    local logdir=${LOG_DIR}/$1
    local revisionfile=$2
    mkdir -p "${logdir}"
    shift 2
    set +e
    date >"${logdir}/begin"
    local CE_BUILD_RESULT=""
    if ! ce builder exec -- "$@" |& tee "${logdir}/log"; then
        BUILD_FAILED=1
        CE_BUILD_RESULT=FAILED
    else
        CE_BUILD_RESULT=OK
    fi

    local CE_BUILD_STATUS
    CE_BUILD_STATUS=$(grep -P "^ce-build-status:" "${logdir}/log" | cut -d ':' -f 2-)
    if [[ -z "${CE_BUILD_STATUS}" ]]; then
        CE_BUILD_STATUS=${CE_BUILD_RESULT}
    fi
    echo "${CE_BUILD_STATUS}" >"${logdir}/status"

    if [[ "${CE_BUILD_RESULT}" == "OK" ]]; then
        local REVISION
        REVISION=$(grep -P "^ce-build-revision:" "${logdir}/log" | cut -d ':' -f 2-)
        if [[ -n "${REVISION}" ]]; then
            echo "${REVISION}" >"${revisionfile}"
            aws s3 cp "${revisionfile}" s3://compiler-explorer/opt/.buildrevs/
        fi
    fi

    if [[ "${CE_BUILD_STATUS}" == "OK" ]]; then
        date >"${logdir}/last_success"
    fi

    date >"${logdir}/end"
    set -e
}

build_latest() {
    local IMAGE=$1
    local BUILD_NAME=$2
    local COMMAND=$3
    local BUILD=$4

    local REVISION_FILENAME=/opt/.buildrevs/${BUILD_NAME}
    local REVISION=""

    if [[ -f "${REVISION_FILENAME}" ]]; then
        REVISION=$(cat "${REVISION_FILENAME}")
    fi

    run_on_build "${BUILD_NAME}" "${REVISION_FILENAME}" \
        sudo docker run --rm --name "${BUILD_NAME}.build" -v/home/ubuntu/.s3cfg:/root/.s3cfg:ro -e 'LOGSPOUT=ignore' \
        "compilerexplorer/${IMAGE}-builder" \
        bash "${COMMAND}" "${BUILD}" s3://compiler-explorer/opt/ "${REVISION}"
    log_to_json ${LOG_DIR} admin
}

build_latest_cross() {
    local IMAGE=$1
    local BUILD_NAME=$2
    local COMMAND=$3
    local ARCH=$4
    local BUILD=$5

    # We don't support the "revision" for cross compilers. I looked briefly at adding it
    # using `ct-ng sources` and similar magic, but the number of dependencies (e.g. linux source, gcc trunk)
    # means we'll almost certainly be different every time anyway.
    run_on_build "${BUILD_NAME}" /dev/null \
        sudo docker run --rm --name "${BUILD_NAME}.build" -v/home/ubuntu/.s3cfg:/home/gcc-user/.s3cfg:ro -e 'LOGSPOUT=ignore' \
        "compilerexplorer/${IMAGE}-cross-builder" \
        bash "${COMMAND}" "${ARCH}" "${BUILD}" s3://compiler-explorer/opt/
    log_to_json ${LOG_DIR} admin
}

build_libraries() {
    local IMAGE=$1
    local BUILD_NAME=library
    local COMMAND=build.sh

    local CONAN_PASSWORD
    CONAN_PASSWORD=$(aws ssm get-parameter --name /compiler-explorer/conanpwd | jq -r .Parameter.Value)

    ce builder exec -- sudo docker run --rm --name "${BUILD_NAME}.build" \
        -v/home/ubuntu/.s3cfg:/root/.s3cfg:ro \
        -v/opt:/opt:ro \
        -e 'LOGSPOUT=ignore' \
        -e "CONAN_PASSWORD=${CONAN_PASSWORD}" \
        "compilerexplorer/${IMAGE}-builder" \
        bash "${COMMAND}" "all" "all"
}

# IMPORTANT: when you add a build here you must also add an entry in remove_old_compilers.sh

# Entries commented out with #MOVED: have been moved to the compiler-workflows repo in
# .github/workflows/daily-builds.yml

# llvm build is fast, so lets do it first
#MOVED: build_latest clang llvm build.sh llvm-trunk

#MOVED: build_latest gcc gcc build.sh trunk
#MOVED: build_latest gcc gcc_contracts build.sh lock3-contracts-trunk
#MOVED: build_latest gcc gcc_contract_labels build.sh lock3-contract-labels-trunk
#MOVED: build_latest gcc gcc_modules build.sh cxx-modules-trunk
#MOVED: build_latest gcc gcc_coroutines build.sh cxx-coroutines-trunk
#MOVED: build_latest gcc gcc_gccrs_master build.sh gccrs-master
#MOVED: build_latest clang clang build.sh trunk
#MOVED: build_latest clang clang_assertions build.sh assertions-trunk
#MOVED: build_latest clang clang_cppx build.sh cppx-trunk
#MOVED: build_latest clang clang_cppx_ext build.sh cppx-ext-trunk
#MOVED: build_latest clang clang_cppx_p2320 build.sh cppx-p2320-trunk
#MOVED: build_latest clang clang_relocatable build.sh relocatable-trunk
#MOVED: build_latest clang clang_autonsdmi build.sh autonsdmi-trunk
#MOVED: build_latest clang clang_lifetime build.sh lifetime-trunk
#MOVED: build_latest clang clang_llvmflang build.sh llvmflang-trunk
#MOVED: build_latest clang clang_parmexpr build-parmexpr.sh trunk
#MOVED: build_latest clang clang_patmat build.sh patmat-trunk
#MOVED: build_latest clang clang_embed build.sh embed-trunk
#MOVED: build_latest clang llvm_spirv build.sh llvm-spirv
#MOVED: build_latest go go build.sh trunk
#MOVED: build_latest misc tinycc build-tinycc.sh trunk
#MOVED: build_latest misc cc65 buildcc65.sh trunk
#MOVED: build_latest misc mrustc build-mrustc.sh master
#MOVED: build_latest misc cproc build-cproc.sh master
#MOVED: build_latest misc rustc-cg-gcc_master build-rustc-cg-gcc.sh master
#MOVED: build_latest misc SPIRV-Tools build-spirv-tools.sh master

# before these can be moved, their build scripts need updating to output
# build name, and handle output to a directory.

build_latest_cross gcc arm32 build.sh arm trunk
build_latest_cross gcc arm64 build.sh arm64 trunk

build_libraries library

exit ${BUILD_FAILED}
