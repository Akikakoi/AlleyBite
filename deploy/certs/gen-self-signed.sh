#!/usr/bin/env sh
# 生成本地自签证书（仅用于 staging / 本地验证，文档 10.2）。
# 生产环境请将正式证书替换为 deploy/certs/server.crt 与 server.key。
set -eu

DIR="$(cd "$(dirname "$0")" && pwd)"

openssl req -x509 -nodes -newkey rsa:2048 -days 825 \
  -keyout "$DIR/server.key" \
  -out "$DIR/server.crt" \
  -subj "/C=CN/ST=Local/L=Local/O=AlleyBite/CN=localhost" \
  -addext "subjectAltName=DNS:localhost,DNS:alleybite.local,IP:127.0.0.1"

echo "已生成：$DIR/server.crt 与 $DIR/server.key"