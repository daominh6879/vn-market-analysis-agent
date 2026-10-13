"""Average the saved intent probabilities of several models and report dev / gold-seed quality.

    python intent_model/ensemble.py probs_lr_bge-m3 probs_ft_multilingual-e5-small ...

Each name is artifacts/<name>.npz written by train.py / finetune.py (columns follow agents.classifier.INTENTS).
Prints every model alone, then the mean of all of them.
"""
import sys
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from agents.classifier import INTENTS  # noqa: E402
from common import ARTIFACTS, DATA, load_synthetic, read_jsonl  # noqa: E402
from sklearn.model_selection import train_test_split  # noqa: E402


def report(title, dev_p, gold_p, dev_y, gold_y):
    pred, conf = gold_p.argmax(1), gold_p.max(1)
    cov = {t: (float((conf >= t).mean()), float(np.mean(pred[conf >= t] == gold_y[conf >= t])) if (conf >= t).any() else float("nan"))
           for t in (0.5, 0.7, 0.8, 0.9)}
    row = "  ".join(f"@{t}: {c:.0%}/{a:.1%}" for t, (c, a) in cov.items())
    print(f"{title:45s} dev={np.mean(dev_p.argmax(1) == dev_y):.3f} gold={np.mean(pred == gold_y):.3f}  {row}")


def main():
    seed = read_jsonl(DATA / "seed.jsonl")
    import contextlib
    import io
    with contextlib.redirect_stdout(io.StringIO()):
        syn = load_synthetic(r["query"] for r in seed)
    _, dev = train_test_split(np.arange(len(syn)), test_size=0.15, random_state=0, stratify=[r["label"] for r in syn])
    dev_y = np.array([INTENTS.index(syn[i]["label"]) for i in dev])
    gold_y = np.array([INTENTS.index(r["label"]) for r in seed])
    names = sys.argv[1:]
    P = {n: np.load(ARTIFACTS / f"{n}.npz") for n in names}
    for n in names:
        report(n, P[n]["dev"], P[n]["gold"], dev_y, gold_y)
    if len(names) > 1:
        report("MEAN of all", np.mean([P[n]["dev"] for n in names], 0), np.mean([P[n]["gold"] for n in names], 0), dev_y, gold_y)


if __name__ == "__main__":
    main()
