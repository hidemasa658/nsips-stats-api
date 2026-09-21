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
  smartphone)
    # スマホ向け: PKCS#12 (.p12) を生成。iPhone / Android どちらもタップで install 可
    # 使い方: ./gen_certs.sh smartphone <name> [<password>]
    NAME="${2:?smartphone name required (e.g., hidemasa-iphone)}"
    PASSWORD="${3:-}"
    if [[ ! -f ca.crt ]]; then
      echo "CA not found. Run './gen_certs.sh init-ca' first."
      exit 1
    fi
    # パスワード未指定なら 6桁 ランダム生成
    if [[ -z "$PASSWORD" ]]; then
      PASSWORD=$(openssl rand -hex 3 | tr 'a-f' 'A-F')
      echo "*** 自動生成パスワード: $PASSWORD (メモしてください) ***"
    fi
    openssl genrsa -out "client-${NAME}.key" 2048
    openssl req -new -key "client-${NAME}.key" \
      -subj "/CN=${NAME}" \
      -out "client-${NAME}.csr"
    openssl x509 -req -in "client-${NAME}.csr" \
      -CA ca.crt -CAkey ca.key -CAcreateserial \
      -out "client-${NAME}.crt" -days 1825 -sha256
    rm "client-${NAME}.csr"
    # PKCS#12 バンドル (iOS/Android両対応)
    openssl pkcs12 -export \
      -out "client-${NAME}.p12" \
      -inkey "client-${NAME}.key" \
      -in "client-${NAME}.crt" \
      -certfile ca.crt \
      -name "nsips-stats ${NAME}" \
      -passout "pass:${PASSWORD}"
    chmod 600 "client-${NAME}.key" "client-${NAME}.p12"
    echo ""
    echo "===================================================================="
    echo " スマホ用証明書 生成完了"
    echo "===================================================================="
    echo ""
    echo " ファイル: client-${NAME}.p12"
    echo " パスワード: ${PASSWORD}"
    echo ""
    echo " ── iPhone / iPad ──"
    echo "  1. .p12 を AirDrop で iPhone に送る (メール添付でも可)"
    echo "  2. タップ → 「プロファイルがダウンロードされました」通知"
    echo "  3. 設定 → 一般 → VPN とデバイス管理 → プロファイルをインストール"
    echo "  4. パスワード入力: ${PASSWORD}"
    echo "  5. Safari で https://kumatool.duckdns.org/nsips-stats/dashboard?token=..."
    echo ""
    echo " ── Android ──"
    echo "  1. .p12 を Google Drive/メール等でスマホへ転送"
    echo "  2. 設定 → セキュリティ → 暗号化と認証情報 → 証明書をインストール"
    echo "     → \"VPN とアプリのユーザー証明書\" を選択"
    echo "  3. パスワード入力: ${PASSWORD}"
    echo "  4. Chrome で https://kumatool.duckdns.org/nsips-stats/dashboard?token=..."
    echo ""
    echo "===================================================================="
    ;;
  *)
    echo "Usage: $0 {init-ca | client <pc-name> | smartphone <name> [password]}"
    exit 1
    ;;
esac
