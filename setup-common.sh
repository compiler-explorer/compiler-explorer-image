#!/bin/bash

set -exuo pipefail

INSTALL_TYPE=${1:-non-ci}

# https://askubuntu.com/questions/132059/how-to-make-a-package-manager-wait-if-another-instance-of-apt-is-running
wait_for_apt() {
  while fuser /var/lib/dpkg/lock >/dev/null 2>&1; do
    echo "Waiting for other software managers to finish..."
    sleep 5
  done
}

# Sometimes it seems auto apt takes a while to kick in...
sleep 5
wait_for_apt
sleep 5
wait_for_apt

# Disable unattended upgrades
apt purge -y --auto-remove unattended-upgrades

apt-get -y update
apt-get -y dist-upgrade --force-yes

apt-get -y install \
  autofs \
  jq \
  libc6-arm64-cross \
  libdatetime-perl \
  libtinfo5 \
  libwww-perl \
  nfs-common \
  python3.9 \
  python-is-python3 \
  python3-pip \
  python3.9-venv \
  qemu-user-static \
  ssmtp \
  unzip \
  wget

apt-get -y autoremove
pip3 install --upgrade pip
hash -r pip

# This returns amd64 or arm64
ARCH=$(dpkg --print-architecture)

if [ "$INSTALL_TYPE" != 'ci' ]; then
  mkdir /tmp/aws-install
  pushd /tmp/aws-install
  if [ "$ARCH" == 'amd64' ]; then
    curl -sL "https://awscli.amazonaws.com/awscli-exe-linux-x86_64.zip" -o "awscliv2.zip"
  else
    curl -sL "https://awscli.amazonaws.com/awscli-exe-linux-aarch64.zip" -o "awscliv2.zip"
  fi
  unzip awscliv2.zip
  ./aws/install
  popd
  rm -rf /tmp/aws-install
fi

mkdir -p /root/.aws /home/ubuntu/.aws
echo -e "[default]\nregion=us-east-1" | tee /root/.aws/config /home/ubuntu/.aws/config
chown -R ubuntu /home/ubuntu/.aws

get_conf() {
  aws ssm get-parameter --name "$1" | jq -r .Parameter.Value
}

LOG_DEST_HOST=$(get_conf /compiler-explorer/logDestHost)
LOG_DEST_PORT=$(get_conf /compiler-explorer/logDestPort)
PTRAIL='/etc/rsyslog.d/99-papertrail.conf'
echo "*.*          @${LOG_DEST_HOST}:${LOG_DEST_PORT}" >"${PTRAIL}"
service rsyslog restart
pushd /tmp

if [ "$ARCH" == 'amd64' ]; then
  curl -sL 'https://github.com/papertrail/remote_syslog2/releases/download/v0.21/remote_syslog_linux_amd64.tar.gz' | tar zxf -
else
  curl -sL 'https://github.com/papertrail/remote_syslog2/releases/download/v0.21/remote_syslog_linux_arm64.tar.gz' | tar zxf -
fi

cp remote_syslog/remote_syslog /usr/local/bin/
popd

cat >/etc/log_files.yml <<EOF
files:
    - /var/log/nginx/*.err
exclude_patterns:
    - smbd_calculate_access_mask_fsp
destination:
    host: ${LOG_DEST_HOST}
    port: ${LOG_DEST_PORT}
    protocol: tls
EOF

cat >/lib/systemd/system/remote-syslog.service <<EOF
[Unit]
Description=remote_syslog2
Documentation=https://github.com/papertrail/remote_syslog2
After=network-online.target

[Service]
ExecStartPre=/usr/bin/test -e /etc/log_files.yml
ExecStart=/usr/local/bin/remote_syslog -D
Restart=always
User=root
Group=root

[Install]
WantedBy=multi-user.target
EOF
systemctl enable remote-syslog

cp /infra/init/log-instance-id.service /lib/systemd/system/log-instance-id.service
systemctl enable log-instance-id

setup_grafana() {
    local GRAFANA_CONFIG=/infra/grafana/agent.yaml
    local GRAFANA_VERSION=0.41.1

    pushd /tmp
    curl -sLo agent-linux.zip "https://github.com/grafana/agent/releases/download/v${GRAFANA_VERSION}/grafana-agent-linux-${ARCH}.zip"
    unzip agent-linux.zip
    cp "grafana-agent-linux-${ARCH}" /usr/local/bin/grafana-agent
    popd

    local PROM_PASSWORD
    local LOKI_PASSWORD
    PROM_PASSWORD=$(get_conf /compiler-explorer/promPassword)
    LOKI_PASSWORD=$(get_conf /compiler-explorer/lokiPassword)
    mkdir -p /etc/grafana
    cp $GRAFANA_CONFIG /etc/grafana/agent.yaml.tpl
    sed -i "s/@PROM_PASSWORD@/${PROM_PASSWORD}/g" /etc/grafana/agent.yaml.tpl
    sed -i "s/@LOKI_PASSWORD@/${LOKI_PASSWORD}/g" /etc/grafana/agent.yaml.tpl
    chmod 600 /etc/grafana/agent.yaml.tpl
    if [ "${INSTALL_TYPE}" = "ci" ]; then
      cp /infra/grafana/make-config-ci.sh /etc/grafana/make-config.sh
    elif [ "${INSTALL_TYPE}" = "admin" ]; then
      cp /infra/grafana/make-config-admin.sh /etc/grafana/make-config.sh
    else
      cp /infra/grafana/make-config.sh /etc/grafana/make-config.sh
    fi
    cp /infra/grafana/grafana-agent.service /lib/systemd/system/grafana-agent.service
    systemctl daemon-reload
    systemctl enable grafana-agent
}
setup_grafana

mkdir -p /efs
if ! grep "/efs nfs" /etc/fstab; then
  echo "fs-db4c8192.efs.us-east-1.amazonaws.com:/ /efs nfs nfsvers=4.1,rsize=1048576,wsize=1048576,hard,timeo=600,retrans=2,noresvport${EXTRA_NFS_ARGS} 0 0" >>/etc/fstab
fi

# Configure email
SMTP_PASS=$(aws ssm get-parameter --name /admin/smtp_pass | jq -r .Parameter.Value)
cat >/etc/ssmtp/ssmtp.conf <<EOF
root=postmaster
mailhub=email-smtp.us-east-1.amazonaws.com
hostname=compiler-explorer.com
FromLineOverride=NO
AuthUser=AKIAJZWPG4D3SSK45LJA
AuthPass=${SMTP_PASS}
UseTLS=YES
UseSTARTTLS=YES
EOF
cat >/etc/ssmtp/revaliases <<EOF
ubuntu:admin@compiler-explorer.com:email-smtp.us-east-1.amazonaws.com
EOF

if [ "${INSTALL_TYPE}" = "ci" ]; then
  chfn -f 'Compiler Explorer Admin' ubuntu
else
  chfn -f 'Compiler Explorer Build Agent' ubuntu
fi
chmod 640 /etc/ssmtp/*

mount -a

cd /home/ubuntu/

mkdir -p /home/ubuntu/.ssh
mkdir -p /tmp/auth_keys
aws s3 sync s3://compiler-explorer/authorized_keys /tmp/auth_keys
cat /tmp/auth_keys/* >>/home/ubuntu/.ssh/authorized_keys
rm -rf /tmp/auth_keys
chown -R ubuntu /home/ubuntu/.ssh

setup_cefs() {
    mkdir /cefs
    echo "* -fstype=squashfs,loop,nosuid,nodev,ro :/efs/cefs-images/&.sqfs" > /etc/auto.cefs
    echo "/cefs /etc/auto.cefs --negative-timeout 1" > /etc/auto.master.d/cefs.autofs
    service autofs restart
}
setup_cefs
