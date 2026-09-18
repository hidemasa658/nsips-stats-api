#!/usr/bin/env bash
# nsips-stats-api mTLS 証明書生成スクリプト
# 使い方:
#   ./gen_certs.sh init-ca                 # 初回のみ: CA 生成
#   ./gen_certs.sh client <pc-identifier>  # PC ごとにクライアント証明書発行
set -euo pipefail

CERTS_DIR="$(dirname "$0")/certs"
mkdir -p "$CERTS_DIR"
cd "$CERTS_DIR"

case "${1:-}" in
  init-ca)
    if [[ -f ca.key ]]; then
      echo "CA already exists. Aborting."
      exit 1
    fi
    openssl genrsa -out ca.key 4096
    openssl req -x509 -new -nodes -key ca.key -sha256 -days 3650 \
      -subj "/CN=nsips-stats-api CA" \
      -out ca.crt
    chmod 600 ca.key
    echo "CA generated: ca.crt (デプロイ用) + ca.key (秘密、絶対公開しない)"
    ;;
  client)
    NAME="${2:?client name required (e.g., pharmacy-pc-01)}"
    if [[ ! -f ca.crt ]]; then
      echo "CA not found. Run './gen_certs.sh init-ca' first."
      exit 1
    fi
    openssl genrsa -out "client-${NAME}.key" 2048
    openssl req -new -key "client-${NAME}.key" \
      -subj "/CN=${NAME}" \
      -out "client-${NAME}.csr"
    openssl x509 -req -in "client-${NAME}.csr" \
      -CA ca.crt -CAkey ca.key -CAcreateserial \
      -out "client-${NAME}.crt" -days 1825 -sha256
    rm "client-${NAME}.csr"
    chmod 600 "client-${NAME}.key"
    echo "Client cert generated:"
    echo "  client-${NAME}.crt  (公開可: 対象 PC + サーバーは不要)"
    echo "  client-${NAME}.key  (秘密: 対象 PC にのみ配置、USB 手渡し推奨)"
    ;;
  *)
    echo "Usage: $0 {init-ca | client <pc-name>}"
    exit 1
    ;;
esac
