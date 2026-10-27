# Revenue Agent v0

ローカルLLM（Ollama）を使い、**市場シグナル → ニッチな業務ツール候補 → 採点 → MVP生成 → 静的QA**までを自動化する実験基盤です。

## 何を狙うか

汎用AIチャット、汎用要約、汎用文章生成のような混雑領域は避けます。

2026-10-27時点の日本市場向けシグナルを `market_signals.json` に固定し、次のような「既存のExcel・コピペ・目視確認で繰り返される業務」を狙います。

- 書類・データ処理
- CSVの整形や検査
- ワークフロー前後のチェック
- 引き継ぎ用データの生成
- ブラウザ内だけで完結するプライバシー重視の業務ツール

## v0の安全弁

- 勝手に公開しない
- 勝手に課金しない
- 外部API必須のMVPを作らない
- 入力データを外部送信しない
- `fetch` / `XMLHttpRequest` / WebSocket / iframe / 外部scriptを静的QAで拒否
- 公開前に必ず人間レビューを挟む

## 必要環境

- Python 3.11+ 推奨
- Ollama
- モデル: `qwen3.5:9b`（環境変数で変更可能）

Ollama APIの既定値は `http://localhost:11434` です。

## 実行

PowerShellでリポジトリ直下から実行します。

```powershell
# Ollamaに qwen3.5:9b が入っているか確認します。
ollama list

# Revenue Agentを1回実行します。候補生成→採点→MVP生成→QAまで進みます。
python .\revenue_agent.py once
```

別モデルを使う場合:

```powershell
# このPowerShellセッションだけ、Revenue Agentが使うOllamaモデルを変更します。
$env:OLLAMA_MODEL="qwen3.5:9b"

# 指定したモデルでRevenue Agentを実行します。
python .\revenue_agent.py once
```

## 出力

実行ごとに以下を生成します。

```text
runs/<run-id>/
  ideas.json
  selected_idea.json
  spec.json
  qa.json
  summary.json

generated/<slug>/
  index.html
```

`summary.json` の `qa_passed` が `true` の場合でも、自動公開はしません。`next_gate` は常に `human_review_before_publish` です。

## テスト

```powershell
# Ollamaを呼ばない純粋な単体テストを実行します。
python -m unittest discover -s tests -v
```

## 次段階

v1では「作る」だけではなく、候補ごとに実需要を検証する Market Probe を追加します。

1. 市場シグナル更新
2. ニッチ候補生成
3. 競合・無料代替の確認
4. ランディングページまたはMVP生成
5. 公開前レビュー
6. 公開後のアクセス・CV計測
7. 数字が悪い案は停止、良い案だけ改善

目的は「AIが勝手に儲ける」と約束することではなく、**人間が使う時間を極小化しながら、収益化候補を高速に試行錯誤できる開発ライン**を作ることです。
