# BIT 岡山県競売情報スクレイピング

BIT 不動産競売物件情報サイトの岡山県検索結果一覧から物件詳細リンクを収集し、各詳細画面の HTML と「3点セット」PDF を Cloudflare R2 に保存するサーバレス運用のためのリポジトリです。

## 採用構成

- 実行基盤: GitHub Actions
- ブラウザ操作: Playwright Chromium
- 保存先: Cloudflare R2（S3互換API）
- 実装言語: Python

## 現在の実装範囲

1. 一覧・検索結果ページを `BIT_START_URL` から開く
2. 一覧内の物件詳細リンクを抽出する
3. 詳細画面 HTML を R2 に保存する
4. 詳細画面の「3点セットのダウンロード」から PDF を取得し、R2 に保存する
5. `SCRAPE_MAX_DETAILS` で処理件数を制限する
   - 未設定または空文字の場合は、一覧画面で検出した全件を処理する

## R2 保存パス

デフォルトでは `R2_PREFIX=bit` として以下の形式で保存します。

```text
bit/okayama/html/YYYY/MM/DD/<detail-stable-id>.html
bit/okayama/pdf/YYYY/MM/DD/<detail-stable-id>.pdf
```

`detail-stable-id` は詳細URLのパスとURLハッシュから生成します。

## GitHub Actions の設定

### Secrets

Repository secrets に以下を設定してください。

| Name | 説明 |
| --- | --- |
| `R2_ACCOUNT_ID` | Cloudflare account ID |
| `R2_ACCESS_KEY_ID` | R2 API token の access key ID |
| `R2_SECRET_ACCESS_KEY` | R2 API token の secret access key |
| `R2_BUCKET` | 保存先 R2 bucket 名 |

### Variables

Repository variables に以下を設定できます。

| Name | 必須 | 説明 |
| --- | --- | --- |
| `BIT_START_URL` | 推奨 | BITの検索結果または一覧URL |
| `SCRAPE_MAX_DETAILS` | 任意 | 最大取得件数。空ならMAX |
| `R2_PREFIX` | 任意 | R2内のプレフィックス。未設定時は `bit` |

## 手動実行

GitHub Actions の `scrape-bit-okayama` workflow は `workflow_dispatch` に対応しています。

- `start_url`: 対象の検索結果・一覧URL
- `max_details`: 取得する詳細件数。空欄ならMAX

## ローカル実行

```bash
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
playwright install chromium
export BIT_START_URL="https://www.bit.courts.go.jp/app/..."
export SCRAPE_MAX_DETAILS="3"
export R2_ACCOUNT_ID="..."
export R2_ACCESS_KEY_ID="..."
export R2_SECRET_ACCESS_KEY="..."
export R2_BUCKET="..."
python -m scraper.bit.scrape_details
```

## 開発指針

- まずは HTML と PDF の原本保存を優先する
- パース済みデータの DB 保存は次フェーズで追加する
- サイト負荷を抑えるため、取得頻度と並列度は低く保つ
- 将来的には詳細HTMLから事件番号、裁判所、売却基準価額、入札期間、所在地などを抽出する
- PDF は再解析可能な原本として R2 に保管し、メタデータは PostgreSQL などに分離して保存する
- セレクタが変更されても復旧しやすいように、画面文言に依存する処理は小さな関数に閉じ込める

## 注意

BITサイトの利用条件、robots.txt、アクセス負荷に配慮してください。公共サイトに対して短時間に大量アクセスしない運用にしてください。
