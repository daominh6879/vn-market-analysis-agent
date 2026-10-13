"""Train the multi-head model on frozen bge-m3 embeddings and report per-field quality.

    python intent_model/train.py

Heads (one logistic regression each): intent, route_type, language, time kind, time unit,
plus one-vs-rest for market/macro/rag topics and risk flags.
Not modelled (fall back to the LLM / existing deterministic code in the cascade):
  - ticker/sector: agents.focus.extract_focus_entities/sector already do this deterministically.
  - screening filters: needs token-level tagging to pair indicator<->op<->number; reported as a gap.

Evaluation:
  - intent on the gold seed set (evals/*.json), training rows duplicating a seed query removed;
  - every head on a stratified 20% dev split of the synthetic data.
Writes artifacts/heads.joblib.
"""
import sys
from datetime import date
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent))
from common import ARTIFACTS, DATA, build_time, embed, load_synthetic, read_jsonl, resolve_time  # noqa: E402

MULTI = ("market_topics", "macro_topics", "rag_topics", "risk_flags")


def fit(X, y):
    from sklearn.linear_model import LogisticRegression
    return LogisticRegression(max_iter=3000, C=10, class_weight="balanced").fit(X, y)


def main():
    import joblib
    from sklearn.linear_model import LogisticRegression
    from sklearn.metrics import classification_report, f1_score
    from sklearn.model_selection import train_test_split
    from sklearn.multiclass import OneVsRestClassifier
    from sklearn.preprocessing import MultiLabelBinarizer

    seed = read_jsonl(DATA / "seed.jsonl")
    syn = load_synthetic(r["query"] for r in seed)  # drops label noise + any gold-seed leakage

    X = embed([r["query"] for r in syn])
    idx = np.arange(len(syn))
    # same split as finetune.py so dev numbers are comparable and probabilities can be ensembled
    tr, dev = train_test_split(idx, test_size=0.15, random_state=0, stratify=[r["label"] for r in syn])
    Xs = embed([r["query"] for r in seed])
    print(f"synthetic train={len(tr)} dev={len(dev)} gold seed={len(seed)}")

    heads = {}

    def col(name, rows):
        return [name_get(r, name) for r in rows]

    def name_get(r, name):
        return {"intent": r["label"], "route_type": r["route_type"], "language": r["language"],
                "time_kind": r["time"]["kind"], "time_unit": r["time"].get("unit")}[name]

    # single-label heads
    for name in ("intent", "route_type", "language", "time_kind"):
        heads[name] = fit(X[tr], col(name, [syn[i] for i in tr]))
    rel_tr = [i for i in tr if syn[i]["time"]["kind"] == "relative"]
    heads["time_unit"] = fit(X[rel_tr], [syn[i]["time"]["unit"] for i in rel_tr])

    # multi-label heads
    for name in MULTI:
        mlb = MultiLabelBinarizer().fit([syn[i][name] for i in tr])
        if len(mlb.classes_) < 2:
            continue
        ovr = OneVsRestClassifier(
            LogisticRegression(max_iter=3000, C=10, class_weight="balanced")
        ).fit(X[tr], mlb.transform([syn[i][name] for i in tr]))
        heads[name] = (mlb, ovr)

    # ── dev metrics ───────────────────────────────────────────────────────────
    print("\n== dev split (synthetic) ==")
    dev_rows = [syn[i] for i in dev]
    for name in ("intent", "route_type", "language", "time_kind"):
        pred = heads[name].predict(X[dev])
        gold = col(name, dev_rows)
        print(f"{name:12s} acc={np.mean(pred == np.array(gold)):.3f}")
    print("\nintent report:\n" + classification_report(
        col("intent", dev_rows), heads["intent"].predict(X[dev]), zero_division=0))
    for name in MULTI:
        if name not in heads:
            continue
        mlb, ovr = heads[name]
        f1 = f1_score(mlb.transform([r[name] for r in dev_rows]), ovr.predict(X[dev]),
                      average="micro", zero_division=0)
        print(f"{name:14s} micro-F1={f1:.3f}")

    # time end-to-end: predicted kind/unit + regex numbers -> resolved dates must equal gold
    today = date.today()
    kinds = heads["time_kind"].predict(X[dev])
    units = heads["time_unit"].predict(X[dev])
    ok = 0
    for r, k, u in zip(dev_rows, kinds, units):
        got = resolve_time(build_time(r["query"], k, u), today)
        ok += got == resolve_time(r["time"], today)
    print(f"\ntime_context exact match (dates) = {ok / len(dev_rows):.3f}")

    # ── gold seed: intent + cascade threshold table ──────────────────────────
    proba = heads["intent"].predict_proba(Xs)
    pred = heads["intent"].classes_[proba.argmax(1)]
    conf = proba.max(1)
    yte = np.array([r["label"] for r in seed])
    print(f"\n== gold seed intent accuracy = {np.mean(pred == yte):.3f} (n={len(seed)}) ==")
    print("threshold  coverage  accuracy_on_covered")
    for t in (0.5, 0.6, 0.7, 0.8, 0.9, 0.95):
        m = conf >= t
        acc = np.mean(pred[m] == yte[m]) if m.any() else float("nan")
        print(f"{t:9.2f}  {m.mean():8.2f}  {acc:19.3f}")

    # ── gold seed: slots (only rows that carry the label) ────────────────────
    pk, pu = heads["time_kind"].predict(Xs), heads["time_unit"].predict(Xs)
    t_rows = [(r, k, u) for r, k, u in zip(seed, pk, pu) if r["time"] is not None]
    t_ok = sum(resolve_time(build_time(r["query"], k, u), today) == resolve_time(r["time"], today)
               for r, k, u in t_rows)
    print(f"\ntime_context on gold seed: {t_ok}/{len(t_rows)} exact")
    for r, k, u in t_rows:
        if resolve_time(build_time(r["query"], k, u), today) != resolve_time(r["time"], today):
            print(f"   MISS {r['query']!r}: pred={build_time(r['query'], k, u)} gold={r['time']}")
    try:
        sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
        from agents.focus import extract_focus_entities
        tk = [(r, extract_focus_entities(r["query"])) for r in seed if r["quality"] == "gold"]
        ok = sum(set(g) == set(r["tickers"]) for r, g in tk)
        print(f"ticker extraction (deterministic, agents.focus) on gold seed: {ok}/{len(tk)} exact")
        for r, g in tk:
            if set(g) != set(r["tickers"]):
                print(f"   MISS {r['query']!r}: got={g} gold={r['tickers']}")
    except Exception as e:  # noqa: BLE001
        print(f"ticker extraction check skipped: {e}")
    for name in ("risk_flags", "market_topics", "macro_topics", "rag_topics"):
        if name in heads:
            mlb, ovr = heads[name]
            rows = [(i, r) for i, r in enumerate(seed) if r["quality"] == "gold"]
            P = ovr.predict(Xs[[i for i, _ in rows]])
            ok = sum(set(mlb.classes_[p == 1]) == set(r[name]) for (_, r), p in zip(rows, P))
            print(f"{name:14s} exact-set match on gold seed: {ok}/{len(rows)}")

    ARTIFACTS.mkdir(exist_ok=True)
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
    from agents.classifier import INTENTS  # class order shared by every model's saved probabilities
    cls = list(heads["intent"].classes_)
    reorder = [cls.index(c) for c in INTENTS]
    np.savez(ARTIFACTS / "probs_lr_bge-m3.npz", dev=heads["intent"].predict_proba(X[dev])[:, reorder],
             gold=proba[:, reorder])
    joblib.dump(heads, ARTIFACTS / "heads.joblib")
    print("\nsaved artifacts/heads.joblib")


if __name__ == "__main__":
    main()
