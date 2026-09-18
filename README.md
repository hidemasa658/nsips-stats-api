# nsips-stats-api

nsips-watcher の統計データ受信・集計 API (さくら rag-server デプロイ用)。

FastAPI + SQLite、認証は **mTLS + X-API-Token** の二段階。詳細は `~/dev/nsips-watcher/docs/superpowers/specs/2026-09-18-nsips-stats-design.md` 参照。

## 開発 (macOS)

```
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements-dev.txt
cp .env.example .env
# .env の API_TOKEN を編集
pytest
uvicorn main:app --reload
```

## デプロイ (rag-server)

`deploy/deploy.md` 参照。
