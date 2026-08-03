#!/usr/bin/env bash
# One-time setup for the Lynx VPS (Ubuntu). Run as root on the server.
set -euo pipefail

echo "== Installing Docker + Compose plugin =="
apt-get update
apt-get install -y ca-certificates curl gnupg git
install -m 0755 -d /etc/apt/keyrings
curl -fsSL https://download.docker.com/linux/ubuntu/gpg -o /etc/apt/keyrings/docker.asc
chmod a+r /etc/apt/keyrings/docker.asc
echo \
  "deb [arch=$(dpkg --print-architecture) signed-by=/etc/apt/keyrings/docker.asc] https://download.docker.com/linux/ubuntu \
  $(. /etc/os-release && echo "$VERSION_CODENAME") stable" | tee /etc/apt/sources.list.d/docker.list > /dev/null
apt-get update
apt-get install -y docker-ce docker-ce-cli containerd.io docker-buildx-plugin docker-compose-plugin

echo "== Setting up GitHub deploy key (read-only pull access) =="
mkdir -p ~/.ssh
cat > ~/.ssh/github_deploy_key << 'EOF'
PASTE_GITHUB_DEPLOY_PRIVATE_KEY_HERE
EOF
chmod 600 ~/.ssh/github_deploy_key
cat >> ~/.ssh/config << 'EOF'
Host github.com
  IdentityFile ~/.ssh/github_deploy_key
  IdentitiesOnly yes
EOF
ssh-keyscan github.com >> ~/.ssh/known_hosts 2>/dev/null

echo "== Cloning repo =="
mkdir -p /opt
git clone git@github.com:Alessiiio/lynxanalyser.git /opt/lynx
cd /opt/lynx

echo "== Now: cp .env.example .env and fill in secrets, then run =="
echo "   export DOMAIN=your.domain.tld   (or leave unset to use 'localhost' for now)"
echo "   docker compose up -d --build"
