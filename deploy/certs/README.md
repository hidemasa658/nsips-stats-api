# 証明書ディレクトリ

このディレクトリの `.crt`/`.key`/`.srl`/`.csr` は `.gitignore` されています。開発者ローカルでのみ管理。

## 生成物

- `ca.crt` — サーバー配布用 (VPS の `/etc/nginx/certs/nsips-ca.crt` へコピー)
- `ca.key` — 発行者の秘密鍵 (**絶対公開しない**)
- `client-<name>.crt` + `client-<name>.key` — 各薬局 PC 用 (USB 手渡し配布)
- `ca.srl` — シリアル番号管理 (openssl 自動生成)

## バックアップ

`ca.key` を紛失すると新しい CA を作り直す必要があり、全 client 証明書を再発行する必要がある。1Password / パスワードマネージャ等に格納推奨。

## 使い方

```
../gen_certs.sh init-ca                    # 初回のみ
../gen_certs.sh client pharmacy-pc-01      # PC ごと
```
