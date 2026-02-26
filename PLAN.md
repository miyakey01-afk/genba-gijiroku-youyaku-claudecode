# 議事録作成アプリ リビルド計画

## 現状分析

### Difyワークフローの構成
```
[ユーザー入力] → [Gemini 2.5 Flash LLM] → [IF/ELSE分岐] → テキスト出力 or DOCX出力
```

- **入力**: テキスト貼り付け or テキストファイルアップロード（最大3ファイル）
- **処理**: Gemini 2.5 Flashで営業議事録を構造化要約
- **出力**: Markdown形式テキスト or DOCX形式ファイル
- **制限**: テキストのみ対応。音声ファイル非対応。

### LLMプロンプトの役割
- B2B IT機器・インフラ営業会社の議事録要約アシスタント
- 話者認識、動的セクション生成、アクション項目抽出、AI提案

---

## リビルド方針

### アーキテクチャ
```
[ブラウザ (HTML/JS)]
    ↓ ファイルアップロード / テキスト入力
[FastAPI バックエンド (Python)]
    ↓
    ├─ テキストファイル → そのままLLMへ
    ├─ 音声ファイル(MP3) → Gemini File API経由でLLMへ
    ↓
[Google Gemini API (gemini-2.5-flash)]
    ↓
    ├─ テキスト形式 → Markdownテキスト返却
    └─ WORD形式 → python-docxでDOCX生成 → ファイル返却
```

### 音声ファイル処理の方針

**Gemini File APIを使用** (推奨理由):
- Gemini 2.5 Flashは音声を直接処理可能（ネイティブマルチモーダル）
- 音声は約25トークン/秒 → 2時間 = 約180,000トークン（1Mコンテキスト枠内に十分収まる）
- 別途Speech-to-Text APIを使う必要がなく、アーキテクチャがシンプル
- Gemini File APIで大容量ファイル（最大2GB）をアップロード可能

**処理フロー（音声）**:
1. ユーザーがMP3をアップロード（FastAPIに直接POST）
2. バックエンドがローカル一時ディレクトリに保存
3. Gemini File APIにアップロード（最大2GB対応）
4. ポーリングでGemini側のファイル処理完了を待機
5. Geminiが音声を直接解析し、議事録を生成
6. 一時ファイルを削除

**大容量MP3のアップロード対策**:
- Cloud RunのHTTP/1.1ではリクエストサイズ上限が32MB → 2時間MP3（100-300MB）は超過する
- 対策: **HTTP/2を使用**してCloud Runの32MB制限を回避（HTTP/2にはサイズ制限なし）
  - Uvicorn側: `--http h2` オプションで h2c (HTTP/2 cleartext) を有効化
  - Cloud Run側: `gcloud run deploy --use-http2` でHTTP/2を有効化
  - 依存: `h2` パッケージをrequirements.txtに追加
- GCS署名付きURLは不要（アーキテクチャをシンプルに保つ）

### Cloud Run構成
- **タイムアウト**: 最大60分（2時間音声の処理に対応）
- **メモリ**: 2GB（大きなMP3ファイルのメモリ内処理用）
- **CPU**: 2 vCPU
- **HTTP/2**: 有効（`--use-http2`）— 32MBリクエストサイズ制限を回避
- **同時実行数**: 1（LLM処理は重いため）

---

## 実装項目

### 1. プロジェクト基盤
- `pyproject.toml` or `requirements.txt` で依存関係管理
- FastAPIアプリケーション構造
- 環境変数管理（`.env` + pydantic-settings）
- Dockerfile + `.dockerignore`

### 2. バックエンドAPI (FastAPI)
- `POST /api/generate` — 議事録生成エンドポイント
  - マルチパートフォームデータ受付（テキスト、ファイル、出力形式）
  - 入力バリデーション
- `GET /api/health` — ヘルスチェック
- Server-Sent Events (SSE) でストリーミングレスポンス対応（長時間処理のフィードバック用）

### 3. Gemini統合
- Google Generative AI Python SDK (`google-genai`)
- テキスト入力の処理
- 音声ファイル処理（Gemini File API経由）
  - ファイルアップロード → ポーリングで処理完了待ち → プロンプト送信
- Difyから移植したシステムプロンプト・ユーザープロンプト

### 4. DOCX生成
- `python-docx` ライブラリ
- MarkdownテキストをDOCX構造に変換
  - `## 見出し` → Heading 2
  - `### 見出し` → Heading 3
  - `- 箇条書き` → List Bullet
  - 本文 → Normal

### 5. フロントエンド (HTML/CSS/JS)
- シンプルなSPA（Single Page Application）
- 入力フォーム:
  - テキスト貼り付けエリア（textarea）
  - ファイルアップロード（テキスト or MP3）
  - 出力形式選択（テキスト / WORD）
- 処理状態表示（プログレス、ステータスメッセージ）
- 結果表示（Markdownレンダリング or DOCXダウンロード）

### 6. Cloud Runデプロイ
- Dockerfile（Python 3.12 slim）
- `cloudbuild.yaml` or デプロイスクリプト
- 環境変数設定（GEMINI_API_KEY等）
- Cloud Run設定（タイムアウト、メモリ、CPU）

---

## ファイル構成（予定）

```
genba-gijiroku-youyaku-claudecode/
├── app/
│   ├── __init__.py
│   ├── main.py              # FastAPIアプリ + 静的ファイル配信
│   ├── config.py             # 設定・環境変数
│   ├── gemini_client.py      # Gemini API統合
│   ├── docx_generator.py     # DOCX生成
│   ├── prompts.py            # LLMプロンプト定義
│   └── static/
│       ├── index.html        # フロントエンド
│       ├── style.css         # スタイル
│       └── app.js            # フロントエンドロジック
├── Dockerfile
├── .dockerignore
├── .env.example
├── requirements.txt
└── 【1011】議事録作成エージェント（テキストのみ対応）.yml  # 元のDify定義（参考保存）
```

---

## 技術スタック

| 領域 | 技術 |
|------|------|
| バックエンド | Python 3.12 + FastAPI |
| LLM | Google Gemini 2.5 Flash (google-genai SDK) |
| 音声処理 | Gemini File API（ネイティブ音声対応） |
| DOCX生成 | python-docx |
| フロントエンド | Vanilla HTML/CSS/JS（フレームワーク不要） |
| デプロイ | Docker → Google Cloud Run |

## 主要な依存ライブラリ

```
fastapi
uvicorn[standard]
python-multipart
google-genai
python-docx
pydantic-settings
```

---

## リスクと対策

| リスク | 対策 |
|--------|------|
| 2時間MP3の処理時間が長い | SSEでステータス通知 + Cloud Runタイムアウト60分に設定 |
| MP3ファイルサイズが大きい（~200MB） | Gemini File APIは最大2GBまで対応。Cloud Runへのアップロードはメモリ内処理 |
| Gemini APIのレート制限 | リトライロジック + エラーハンドリング |
| Cloud Runコールドスタート | min-instances=1 設定（コスト増だが応答性向上） |
