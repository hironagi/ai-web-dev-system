from __future__ import annotations

import argparse
import json
import os
import re
import sys
import urllib.error
import urllib.request
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


OLLAMA_URL = os.getenv("OLLAMA_URL", "http://localhost:11434")
OLLAMA_MODEL = os.getenv("OLLAMA_MODEL", "qwen3.5:9b")
ROOT = Path(__file__).resolve().parent
RUNS_DIR = ROOT / "runs"
GENERATED_DIR = ROOT / "generated"


@dataclass
class Idea:
    name: str
    problem: str
    audience: str
    monetization: list[str]
    demand: float
    automation: float
    monetization_strength: float
    low_support: float
    build_speed: float

    @property
    def score(self) -> float:
        # Revenue Agent v0: demand and repeatable automation are weighted most heavily.
        value = (
            self.demand * 0.30
            + self.automation * 0.25
            + self.monetization_strength * 0.20
            + self.low_support * 0.15
            + self.build_speed * 0.10
        )
        return round(value, 3)


def _post_json(url: str, payload: dict[str, Any], timeout: int = 180) -> dict[str, Any]:
    data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    req = urllib.request.Request(
        url,
        data=data,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=timeout) as res:
        return json.loads(res.read().decode("utf-8"))


def ollama_chat(system: str, user: str, *, json_mode: bool = False) -> str:
    payload: dict[str, Any] = {
        "model": OLLAMA_MODEL,
        "stream": False,
        "messages": [
            {"role": "system", "content": system},
            {"role": "user", "content": user},
        ],
        "options": {"temperature": 0.35},
    }
    if json_mode:
        payload["format"] = "json"

    try:
        response = _post_json(f"{OLLAMA_URL.rstrip('/')}/api/chat", payload)
    except urllib.error.URLError as exc:
        raise RuntimeError(
            f"Ollamaに接続できません: {OLLAMA_URL}. Ollamaが起動しているか確認してください。"
        ) from exc

    try:
        return str(response["message"]["content"]).strip()
    except (KeyError, TypeError) as exc:
        raise RuntimeError(f"Ollama応答形式が不正です: {response}") from exc


def clamp_score(value: Any) -> float:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return 0.0
    return max(0.0, min(10.0, number))


def discover_ideas() -> list[Idea]:
    system = (
        "あなたは小規模デジタル商品の事業開発責任者です。"
        "一人開発、低サポート、セルフサービス販売、広告・アフィリエイト・低額課金と相性が良い"
        "ブラウザ完結型ミニツールを選びます。誇大広告、医療・法律・金融助言、ギャンブル、成人向け、"
        "規制商品、他者の権利侵害、スパム用途は除外してください。"
    )
    user = """
日本語市場を主対象に、1ファイルHTML/CSS/JavaScriptでMVPを作れるミニツール案を8件出してください。
各案は以下のJSON形式のみで返してください。
{
  "ideas": [
    {
      "name": "短い名称",
      "problem": "解決する具体的な問題",
      "audience": "対象ユーザー",
      "monetization": ["ads", "affiliate", "one_time"],
      "demand": 0-10,
      "automation": 0-10,
      "monetization_strength": 0-10,
      "low_support": 0-10,
      "build_speed": 0-10
    }
  ]
}
採点は楽観的にせず、競争が激しい案はdemandが高くてもmonetization_strengthやlow_supportを下げてください。
""".strip()

    raw = ollama_chat(system, user, json_mode=True)
    parsed = json.loads(raw)
    ideas: list[Idea] = []
    for item in parsed.get("ideas", []):
        ideas.append(
            Idea(
                name=str(item.get("name", "Unnamed")),
                problem=str(item.get("problem", "")),
                audience=str(item.get("audience", "")),
                monetization=[str(x) for x in item.get("monetization", [])],
                demand=clamp_score(item.get("demand")),
                automation=clamp_score(item.get("automation")),
                monetization_strength=clamp_score(item.get("monetization_strength")),
                low_support=clamp_score(item.get("low_support")),
                build_speed=clamp_score(item.get("build_speed")),
            )
        )
    if not ideas:
        raise RuntimeError("収益候補を生成できませんでした。")
    return ideas


def select_idea(ideas: list[Idea]) -> Idea:
    return max(ideas, key=lambda idea: idea.score)


def slugify(value: str) -> str:
    value = value.lower().strip()
    value = re.sub(r"[^a-z0-9]+", "-", value)
    value = value.strip("-")
    return value or "micro-tool"


def build_spec(idea: Idea) -> dict[str, Any]:
    system = (
        "あなたはMVPプロダクトマネージャーです。"
        "一人運営で保守負荷が低く、ユーザー登録なしでも価値が伝わる仕様にしてください。"
    )
    user = f"""
次の案を、静的Webミニツールとして実装可能な仕様にしてください。
案: {json.dumps(asdict(idea), ensure_ascii=False)}

JSONのみで返してください。
{{
  "title": "日本語タイトル",
  "slug": "ascii-kebab-case",
  "value_proposition": "1文",
  "inputs": ["入力項目"],
  "outputs": ["出力項目"],
  "monetization": ["広告・アフィリエイト・買い切り等の自然な導線"],
  "keywords": ["検索語"],
  "acceptance_criteria": ["テスト可能な受け入れ条件"]
}}
条件: 外部API必須にしない、ログイン必須にしない、個人情報を収集しない、誇大な収益表現をしない。
""".strip()
    spec = json.loads(ollama_chat(system, user, json_mode=True))
    spec["slug"] = slugify(str(spec.get("slug") or idea.name))
    return spec


def strip_code_fence(text: str) -> str:
    match = re.match(r"^```(?:html)?\s*(.*?)\s*```$", text, flags=re.DOTALL | re.IGNORECASE)
    return match.group(1).strip() if match else text.strip()


def build_html(spec: dict[str, Any]) -> str:
    system = (
        "あなたはシニアフロントエンドエンジニアです。"
        "外部依存なしの単一index.htmlを実装します。安全性、アクセシビリティ、モバイル対応を優先します。"
    )
    user = f"""
以下の仕様を満たす、実際にブラウザで動く完成版index.htmlを生成してください。
仕様: {json.dumps(spec, ensure_ascii=False)}

必須条件:
- HTML/CSS/JavaScriptを1ファイルに含める
- CDN、外部script、外部API、fetch、WebSocket、iframeを使わない
- 入力値はブラウザ内だけで処理し、送信しない
- 日本語UI
- レスポンシブ対応
- title、meta description、h1を含める
- 上部と結果付近に data-monetization-slot 属性を持つ広告/収益化用プレースホルダーを置く
- プレースホルダーはユーザー操作を邪魔しない
- 免責が必要なテーマなら短い注意書きを置く
- TODO、ダミー処理、未実装ボタンを残さない
- コードフェンスを付けず、<!doctype html>から始まるHTMLだけを返す
""".strip()
    return strip_code_fence(ollama_chat(system, user, json_mode=False))


def qa_html(html: str) -> dict[str, Any]:
    lowered = html.lower()
    checks = {
        "doctype": "<!doctype html" in lowered,
        "html_tag": "<html" in lowered,
        "title": "<title" in lowered and "</title>" in lowered,
        "h1": "<h1" in lowered,
        "javascript": "<script" in lowered,
        "monetization_slot": "data-monetization-slot" in lowered,
        "minimum_size": len(html) >= 1200,
        "no_todo": "todo" not in lowered,
        "no_fetch": "fetch(" not in lowered,
        "no_xhr": "xmlhttprequest" not in lowered,
        "no_websocket": "websocket" not in lowered,
        "no_iframe": "<iframe" not in lowered,
        "no_external_script": not bool(re.search(r"<script[^>]+src\s*=", lowered)),
        "no_form_action": not bool(re.search(r"<form[^>]+action\s*=", lowered)),
    }
    failures = [name for name, passed in checks.items() if not passed]
    return {"passed": not failures, "checks": checks, "failures": failures}


def write_json(path: Path, data: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


def run_once() -> int:
    started = datetime.now(timezone.utc)
    run_id = started.strftime("%Y%m%d-%H%M%S")
    run_dir = RUNS_DIR / run_id
    run_dir.mkdir(parents=True, exist_ok=True)

    print(f"[1/4] Ollama ({OLLAMA_MODEL}) で収益候補を生成")
    ideas = discover_ideas()
    ranked = sorted(ideas, key=lambda item: item.score, reverse=True)
    write_json(
        run_dir / "ideas.json",
        [{**asdict(idea), "score": idea.score} for idea in ranked],
    )

    selected = select_idea(ideas)
    print(f"[2/4] 選定: {selected.name} / score={selected.score}")
    write_json(run_dir / "selected_idea.json", {**asdict(selected), "score": selected.score})

    print("[3/4] MVP仕様と単一HTMLツールを生成")
    spec = build_spec(selected)
    write_json(run_dir / "spec.json", spec)
    html = build_html(spec)
    product_dir = GENERATED_DIR / spec["slug"]
    product_dir.mkdir(parents=True, exist_ok=True)
    html_path = product_dir / "index.html"
    html_path.write_text(html, encoding="utf-8")

    print("[4/4] 静的QA")
    qa = qa_html(html)
    write_json(run_dir / "qa.json", qa)

    summary = {
        "run_id": run_id,
        "model": OLLAMA_MODEL,
        "selected": selected.name,
        "score": selected.score,
        "product_path": str(html_path.relative_to(ROOT)),
        "qa_passed": qa["passed"],
        "qa_failures": qa["failures"],
        "next_gate": "human_review_before_publish",
    }
    write_json(run_dir / "summary.json", summary)
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    return 0 if qa["passed"] else 2


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Local-LLM Revenue Agent v0: discover, score, build and QA a static micro-tool."
    )
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("once", help="候補生成からMVP生成・QAまでを1回実行")
    args = parser.parse_args()

    if args.command == "once":
        return run_once()
    return 1


if __name__ == "__main__":
    sys.exit(main())
