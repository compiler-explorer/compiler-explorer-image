#!/bin/bash

set -exuo pipefail

DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

bash "${DIR}/setup-node.sh"

# setup nvidia drivers https://docs.nvidia.com/cuda/cuda-installation-guide-linux/index.html#runfile-nouveau-ubuntu

pushd /tmp
curl -sL https://developer.download.nvidia.com/compute/cuda/12.2.2/local_installers/cuda_12.2.2_535.104.05_linux.run -o install.run
sh install.run --silent --driver
rm install.run
popd

echo -e "blacklist nouveau\noptions nouveau modeset=0\n" > /etc/modprobe.d/blacklist-nouveau.conf
update-initramfs -u

# script from https://docs.nvidia.com/cuda/cuda-installation-guide-linux/index.html#runfile-verifications
"${DIR}/setup-gpu-node-devices.sh"
