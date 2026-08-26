"""
evals/eval_sentiment.py — Evaluate LLM sentiment classification on Financial PhraseBank.

Dataset: financial_phrasebank (sentences_allagree split)
        4846 English sentences labeled: 0=negative, 1=neutral, 2=positive
        Source: Malo et al. 2014 — NOT ProsusAI/finbert (that's a model)

Usage:
    python evals/eval_sentiment.py                  # sample 200 sentences
    python evals/eval_sentiment.py --sample 100
    python evals/eval_sentiment.py --full           # all 4846 (slow)
    python evals/eval_sentiment.py --vi             # eval Vietnamese few-shot sentences
"""
from __future__ import annotations

import argparse
import json
import random
import sys
from pathlib import Path

ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(ROOT))

try:
    from dotenv import load_dotenv
    load_dotenv(ROOT / ".env", override=True)
except ImportError:
    pass

from llm.factory import create_client
from llm.types import Message

LABEL_MAP = {0: "negative", 1: "neutral", 2: "positive"}
THRESHOLD = 0.70


def classify_sentiment(client, text: str) -> str:
    """Zero-shot sentiment classification. Returns 'positive'/'neutral'/'negative'."""
    system = (
        "You are a financial sentiment classifier.\n"
        "Classify the sentiment of the following financial sentence.\n"
        "Reply with exactly one word: positive, neutral, or negative.\n"
        "No explanation. No punctuation. Just the one word."
    )
    resp = client.generate(
        [Message(role="user", content=text)],
        max_tokens=500,  # reasoning models need budget for think chain before final answer
        system=system,
    )
    raw = resp.text.strip().lower().split()[0] if resp.text.strip() else "neutral"
    if raw not in ("positive", "neutral", "negative"):
        return "neutral"
    return raw


def run_english_eval(sample_size: int = 200, full: bool = False) -> dict:
    # financial_phrasebank is distributed as a zip file in the HF dataset.
    # Download the zip, extract Sentences_AllAgree.txt (sentences@label format).
    print("Loading financial_phrasebank (zip via huggingface_hub)...")
    try:
        import zipfile
        import io
        from huggingface_hub import hf_hub_download
        zip_path = hf_hub_download(
            repo_id="takala/financial_phrasebank",
            filename="data/FinancialPhraseBank-v1.0.zip",
            repo_type="dataset",
        )
        with zipfile.ZipFile(zip_path) as zf:
            # file inside zip: FinancialPhraseBank-v1.0/Sentences_AllAgree.txt
            names = zf.namelist()
            target = next((n for n in names if "AllAgree" in n), None)
            if not target:
                raise FileNotFoundError(f"AllAgree file not found in zip. Files: {names}")
            raw_lines = zf.read(target).decode("utf-8", errors="replace").splitlines()
    except Exception as e:
        print(f"[ERROR] Could not load dataset: {e}")
        print("pip install -U huggingface_hub")
        sys.exit(1)

    VALID_LABELS = {"positive", "neutral", "negative"}
    all_items = []
    for line in raw_lines:
        line = line.strip()
        if "@" not in line:
            continue
        text, label = line.rsplit("@", 1)
        label = label.strip().lower()
        if label in VALID_LABELS:
            all_items.append({"text": text.strip(), "label": label})

    if not full:
        random.seed(42)
        items = random.sample(all_items, min(sample_size, len(all_items)))
    else:
        items = all_items

    print(f"Evaluating {len(items)} sentences (zero-shot)...")
    client = create_client()

    correct = 0
    results = []
    for i, item in enumerate(items):
        pred = classify_sentiment(client, item["text"])
        hit = pred == item["label"]
        correct += hit
        results.append({"text": item["text"], "label": item["label"], "pred": pred, "correct": hit})
        if (i + 1) % 20 == 0:
            print(f"  {i+1}/{len(items)}  acc={correct/(i+1):.3f}", end="\r")

    accuracy = correct / len(items) if items else 0.0
    print(f"\nAccuracy: {accuracy:.3f} ({correct}/{len(items)})")

    label_breakdown = {}
    for label in ("positive", "neutral", "negative"):
        subset = [r for r in results if r["label"] == label]
        if subset:
            label_acc = sum(r["correct"] for r in subset) / len(subset)
            label_breakdown[label] = {"n": len(subset), "acc": round(label_acc, 3)}

    print("\nPer-class accuracy:")
    for label, stats in label_breakdown.items():
        print(f"  {label:10s}: {stats['acc']:.3f} (n={stats['n']})")

    if accuracy >= THRESHOLD:
        print(f"\n✅ PASS — accuracy {accuracy:.3f} >= threshold {THRESHOLD}")
    else:
        print(f"\n❌ FAIL — accuracy {accuracy:.3f} < threshold {THRESHOLD}")

    return {"accuracy": round(accuracy, 4), "n": len(items), "per_class": label_breakdown}


def run_vietnamese_eval() -> dict:
    shots_path = ROOT / "data" / "sentiment_shots_vi.json"
    if not shots_path.exists():
        print(f"[ERROR] {shots_path} not found. Create it first (30 sentences, 10 per label).")
        sys.exit(1)

    items = json.loads(shots_path.read_text(encoding="utf-8"))
    print(f"Evaluating {len(items)} Vietnamese sentences (zero-shot)...")
    client = create_client()

    correct = 0
    for item in items:
        pred = classify_sentiment(client, item["text"])
        hit = pred == item["label"]
        correct += hit
        mark = "✓" if hit else "✗"
        print(f"  {mark} [{item['label']:<8}] → {pred:<8}  {item['text'][:60]}")

    accuracy = correct / len(items) if items else 0.0
    print(f"\nAccuracy: {accuracy:.3f} ({correct}/{len(items)})")
    return {"accuracy": round(accuracy, 4), "n": len(items)}


def main() -> None:
    parser = argparse.ArgumentParser(description="Sentiment eval on Financial PhraseBank")
    parser.add_argument("--sample", type=int, default=200, help="Number of English sentences")
    parser.add_argument("--full", action="store_true", help="Use all 4846 sentences (slow)")
    parser.add_argument("--vi", action="store_true", help="Eval Vietnamese few-shot sentences")
    parser.add_argument("--out", default="evals/sentiment_results.json")
    args = parser.parse_args()

    if args.vi:
        result = run_vietnamese_eval()
    else:
        result = run_english_eval(sample_size=args.sample, full=args.full)

    out_path = Path(args.out)
    out_path.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"Results → {out_path}")


if __name__ == "__main__":
    main()
