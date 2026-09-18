# rag-server デプロイ手順

## 初回セットアップ

```bash
ssh rag-server
cd /root
git clone https://github.com/hidemasa658/nsips-stats-api.git
cd nsips-stats-api
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt

# .env 作成
cp .env.example .env
# エディタで API_TOKEN を強めのランダム文字列に変更
python3 -c "import secrets; print(secrets.token_urlsafe(48))"
# 出力を .env の API_TOKEN に設定

# systemd
cp deploy/nsips-stats.service /etc/systemd/system/
systemctl daemon-reload
systemctl enable --now nsips-stats
systemctl status nsips-stats

# ポート疎通確認
curl -H "X-API-Token: $(grep API_TOKEN .env | cut -d= -f2)" http://127.0.0.1:18821/health
# → {"status":"ok"}
```

## mTLS 用 CA 証明書を配置

開発者マシンで生成した `ca.crt` を rag-server にコピー:

```bash
# macOS 側で
scp ~/dev/nsips-stats-api/deploy/certs/ca.crt rag-server:/etc/nginx/certs/nsips-ca.crt

# rag-server 側で
ls -la /etc/nginx/certs/nsips-ca.crt
# 存在確認 (owner root, mode 644 で OK)
```

## Nginx 追加

既存の `kumatool.duckdns.org` 設定 (`/etc/nginx/conf.d/kumatool.conf` 等) の **server ブロック内** に:

```nginx
# mTLS のための CA 指定 (optional にして他 location への影響を無くす)
ssl_client_certificate /etc/nginx/certs/nsips-ca.crt;
ssl_verify_client optional;
```

さらに location を追加:

```nginx
location /nsips-stats/ {
    # mTLS 検証: 有効な client cert 無しは 403
    if ($ssl_client_verify != SUCCESS) {
        return 403 "client cert required";
    }

    proxy_pass http://127.0.0.1:18821/;
    proxy_set_header X-API-Token $http_x_api_token;
    proxy_set_header Host $host;
    proxy_set_header X-Real-IP $remote_addr;
    proxy_read_timeout 60s;
}
```

適用:
```bash
nginx -t && systemctl reload nginx
```

外部から確認 (証明書なし → 403 を期待):
```bash
curl -H "X-API-Token: <token>" https://kumatool.duckdns.org/nsips-stats/health
# → 403 "client cert required"
```

外部から確認 (証明書あり → 200 を期待):
```bash
curl -H "X-API-Token: <token>" \
     --cert ~/dev/nsips-stats-api/deploy/certs/client-test-macos.crt \
     --key ~/dev/nsips-stats-api/deploy/certs/client-test-macos.key \
     https://kumatool.duckdns.org/nsips-stats/health
# → {"status":"ok"}
```

## 更新デプロイ

```bash
ssh rag-server
cd /root/nsips-stats-api
git pull
.venv/bin/pip install -r requirements.txt
systemctl restart nsips-stats
```

## CLAUDE_CHANGES.md に記録

`/root/CLAUDE_CHANGES.md` に本デプロイ日時と Nginx 変更を追記。
