"""Fine-tune a small multilingual encoder with all routing heads jointly (CPU-friendly).

    python intent_model/finetune.py                                  # multilingual-e5-small
    python intent_model/finetune.py --model intfloat/multilingual-e5-base --epochs 5

One shared encoder (mean-pooled) + a linear head per field: intent, route_type, language, time kind,
time unit (relative rows only), and multi-label market/macro/rag topics + risk flags.
Reports the same metrics as train.py so the two are comparable, and saves the best epoch
(by synthetic-dev intent accuracy) to artifacts/ft/<model-name>/.
"""
import argparse
import json
import sys
from datetime import date
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from agents.classifier import INTENTS, RAG_TOPICS, RISK_FLAGS  # noqa: E402
from common import (  # noqa: E402
    ARTIFACTS, DATA, MACRO_TOPICS, MARKET_TOPICS, TIME_UNITS, build_time, load_synthetic, read_jsonl, resolve_time,
)

ROUTES = ("agent", "text", "out_of_scope")
LANGS = ("vi", "en")
KINDS = ("none", "relative", "absolute")
MULTI = {"market_topics": MARKET_TOPICS, "macro_topics": MACRO_TOPICS, "rag_topics": RAG_TOPICS, "risk_flags": RISK_FLAGS}
MULTI_DIM = sum(len(v) for v in MULTI.values())


def encode_targets(r: dict) -> dict:
    multi = []
    for name, vocab in MULTI.items():
        multi += [1.0 if v in r.get(name, []) else 0.0 for v in vocab]
    t = r["time"]
    return {
        "intent": INTENTS.index(r["label"]), "route": ROUTES.index(r["route_type"]),
        "lang": LANGS.index(r["language"]), "kind": KINDS.index(t["kind"]),
        "unit": TIME_UNITS.index(t["unit"]) if t["kind"] == "relative" else -100,
        "multi": multi,
    }


def build_model(name: str):
    import torch
    from transformers import AutoModel

    class Net(torch.nn.Module):
        def __init__(self):
            super().__init__()
            self.enc = AutoModel.from_pretrained(name)
            h = self.enc.config.hidden_size
            self.drop = torch.nn.Dropout(0.1)
            self.heads = torch.nn.ModuleDict({
                "intent": torch.nn.Linear(h, len(INTENTS)), "route": torch.nn.Linear(h, len(ROUTES)),
                "lang": torch.nn.Linear(h, len(LANGS)), "kind": torch.nn.Linear(h, len(KINDS)),
                "unit": torch.nn.Linear(h, len(TIME_UNITS)), "multi": torch.nn.Linear(h, MULTI_DIM),
            })

        def forward(self, ids, mask):
            hid = self.enc(input_ids=ids, attention_mask=mask).last_hidden_state
            m = mask.unsqueeze(-1).float()
            pooled = self.drop((hid * m).sum(1) / m.sum(1).clamp(min=1))
            return {k: head(pooled) for k, head in self.heads.items()}

    return Net()


def predict(model, tok, texts, prefix, bs=64):
    import torch
    model.eval()
    outs = {k: [] for k in ("intent", "route", "lang", "kind", "unit", "multi")}
    with torch.no_grad():
        for i in range(0, len(texts), bs):
            b = tok([prefix + t for t in texts[i:i + bs]], padding=True, truncation=True, max_length=96,
                    return_tensors="pt")
            o = model(b["input_ids"], b["attention_mask"])
            for k in ("intent", "route", "lang", "kind", "unit"):
                outs[k].append(torch.softmax(o[k], -1).numpy())
            outs["multi"].append(torch.sigmoid(o["multi"]).numpy())
    return {k: np.concatenate(v) for k, v in outs.items()}


def multi_preds(P):
    """Split the concatenated multi-label probabilities back into per-field boolean matrices."""
    out, i = {}, 0
    for name, vocab in MULTI.items():
        out[name] = P[:, i:i + len(vocab)] >= 0.5
        i += len(vocab)
    return out


def main():
    import torch
    from sklearn.metrics import classification_report, f1_score
    from sklearn.model_selection import train_test_split
    from transformers import AutoTokenizer, get_linear_schedule_with_warmup

    ap = argparse.ArgumentParser()
    ap.add_argument("--model", default="intfloat/multilingual-e5-small")
    ap.add_argument("--epochs", type=int, default=12)
    ap.add_argument("--lr", type=float, default=5e-5)
    ap.add_argument("--head-lr", type=float, default=2e-3)
    ap.add_argument("--bs", type=int, default=32)
    ap.add_argument("--threads", type=int, default=0)
    ap.add_argument("--seed", type=int, default=0)
    ap.add_argument("--tag", default="", help="suffix for artifact names (extra seeds)")
    ap.add_argument("--pos-weight", type=float, default=2.0, help="weight of positive topic/flag labels (recall over precision)")
    args = ap.parse_args()
    if args.threads:
        torch.set_num_threads(args.threads)
    torch.manual_seed(args.seed)
    prefix = "query: " if "e5" in args.model else ""

    seed = read_jsonl(DATA / "seed.jsonl")
    syn = load_synthetic(r["query"] for r in seed)
    idx = np.arange(len(syn))
    tr, dev = train_test_split(idx, test_size=0.15, random_state=0, stratify=[r["label"] for r in syn])
    print(f"model={args.model} train={len(tr)} dev={len(dev)} gold seed={len(seed)}", flush=True)

    tok = AutoTokenizer.from_pretrained(args.model)
    model = build_model(args.model)
    targets = [encode_targets(r) for r in syn]
    # fresh linear heads need a much larger lr than the pretrained encoder, otherwise the sparse
    # multi-label heads stay at "predict nothing"
    opt = torch.optim.AdamW([
        {"params": model.enc.parameters(), "lr": args.lr},
        {"params": list(model.heads.parameters()), "lr": args.head_lr},
    ], weight_decay=0.01)
    steps = args.epochs * (-(-len(tr) // args.bs))
    sched = get_linear_schedule_with_warmup(opt, int(0.06 * steps), steps)
    ce = torch.nn.CrossEntropyLoss(ignore_index=-100)
    bce = torch.nn.BCEWithLogitsLoss(pos_weight=torch.full((MULTI_DIM,), args.pos_weight))

    dev_rows = [syn[i] for i in dev]
    dev_texts = [r["query"] for r in dev_rows]
    dev_gold = np.array([INTENTS.index(r["label"]) for r in dev_rows])
    name = args.model.split("/")[-1] + args.tag
    out_dir = ARTIFACTS / "ft" / name
    best = -1.0

    for ep in range(1, args.epochs + 1):
        model.train()
        order = np.random.RandomState(ep + 100 * args.seed).permutation(tr)
        total = 0.0
        for s in range(0, len(order), args.bs):
            ids_ = order[s:s + args.bs]
            b = tok([prefix + syn[i]["query"] for i in ids_], padding=True, truncation=True, max_length=96,
                    return_tensors="pt")
            T = {k: torch.tensor([targets[i][k] for i in ids_]) for k in ("intent", "route", "lang", "kind", "unit")}
            M = torch.tensor([targets[i]["multi"] for i in ids_])
            o = model(b["input_ids"], b["attention_mask"])
            aux = ce(o["route"], T["route"]) + ce(o["lang"], T["lang"]) + ce(o["kind"], T["kind"])
            if (T["unit"] != -100).any():  # CE over an all-ignored batch is NaN
                aux = aux + ce(o["unit"], T["unit"])
            loss = ce(o["intent"], T["intent"]) + 0.5 * aux + bce(o["multi"], M)
            loss.backward()
            torch.nn.utils.clip_grad_norm_(model.parameters(), 1.0)
            opt.step(); sched.step(); opt.zero_grad()
            total += loss.item() * len(ids_)
        P = predict(model, tok, dev_texts, prefix)
        acc = float(np.mean(P["intent"].argmax(1) == dev_gold))
        print(f"epoch {ep}/{args.epochs} loss={total / len(order):.4f} dev_intent_acc={acc:.3f}", flush=True)
        if acc > best:
            best = acc
            out_dir.mkdir(parents=True, exist_ok=True)
            torch.save(model.state_dict(), out_dir / "model.pt")
            tok.save_pretrained(out_dir)
            (out_dir / "meta.json").write_text(json.dumps({"model": args.model, "prefix": prefix}), encoding="utf-8")

    # ── evaluate the best epoch ──────────────────────────────────────────────
    model.load_state_dict(torch.load(out_dir / "model.pt"))
    P = predict(model, tok, dev_texts, prefix)
    print("\n== dev split (synthetic) ==")
    print(f"intent acc={np.mean(P['intent'].argmax(1) == dev_gold):.3f}  "
          f"route acc={np.mean(P['route'].argmax(1) == [ROUTES.index(r['route_type']) for r in dev_rows]):.3f}  "
          f"lang acc={np.mean(P['lang'].argmax(1) == [LANGS.index(r['language']) for r in dev_rows]):.3f}  "
          f"time_kind acc={np.mean(P['kind'].argmax(1) == [KINDS.index(r['time']['kind']) for r in dev_rows]):.3f}")
    print(classification_report(dev_gold, P["intent"].argmax(1), labels=range(len(INTENTS)),
                                target_names=INTENTS, zero_division=0))
    mp = multi_preds(P["multi"])
    for name, vocab in MULTI.items():
        gold = np.array([[v in r[name] for v in vocab] for r in dev_rows])
        print(f"{name:14s} micro-F1={f1_score(gold, mp[name], average='micro', zero_division=0):.3f}")
    today = date.today()
    ok = sum(
        resolve_time(build_time(r["query"], KINDS[k], TIME_UNITS[u]), today) == resolve_time(r["time"], today)
        for r, k, u in zip(dev_rows, P["kind"].argmax(1), P["unit"].argmax(1)))
    print(f"time_context exact match (dates) = {ok / len(dev_rows):.3f}")

    # temperature scaling fitted on dev: softmax(log p / T) == p^(1/T) renormalised
    def scaled(p, T):
        q = np.exp(np.log(np.clip(p, 1e-9, 1)) / T)
        return q / q.sum(1, keepdims=True)

    temps = np.arange(0.2, 3.01, 0.05)
    nll = [-np.mean(np.log(scaled(P["intent"], T)[np.arange(len(dev_gold)), dev_gold])) for T in temps]
    T_best = float(temps[int(np.argmin(nll))])
    (out_dir / "meta.json").write_text(json.dumps({"model": args.model, "prefix": prefix, "temperature": T_best}), encoding="utf-8")
    print(f"temperature (dev NLL) = {T_best:.2f}")

    Ps = predict(model, tok, [r["query"] for r in seed], prefix)
    Ps["intent"] = scaled(Ps["intent"], T_best)
    pred, conf = Ps["intent"].argmax(1), Ps["intent"].max(1)
    np.savez(ARTIFACTS / f"probs_ft_{out_dir.name}.npz", dev=scaled(P["intent"], T_best), gold=Ps["intent"])
    gold = np.array([INTENTS.index(r["label"]) for r in seed])
    print(f"\n== gold seed intent accuracy = {np.mean(pred == gold):.3f} (n={len(seed)}) ==")
    print("threshold  coverage  accuracy_on_covered")
    for t in (0.5, 0.6, 0.7, 0.8, 0.9, 0.95):
        m = conf >= t
        print(f"{t:9.2f}  {m.mean():8.2f}  {(np.mean(pred[m] == gold[m]) if m.any() else float('nan')):19.3f}")
    wrong = [(r["query"], INTENTS[p], r["label"], c) for r, p, c, g in zip(seed, pred, conf, gold) if p != g]
    print(f"\nmisrouted gold rows ({len(wrong)}):")
    for q, p, g, c in wrong:
        print(f"  {c:.2f} {q!r}: pred={p} gold={g}")

    t_rows = [(r, k, u) for r, k, u in zip(seed, Ps["kind"].argmax(1), Ps["unit"].argmax(1)) if r["time"] is not None]
    t_ok = sum(resolve_time(build_time(r["query"], KINDS[k], TIME_UNITS[u]), today) == resolve_time(r["time"], today)
               for r, k, u in t_rows)
    print(f"\ntime_context on gold seed: {t_ok}/{len(t_rows)} exact")
    mps = multi_preds(Ps["multi"])
    gold_rows = [i for i, r in enumerate(seed) if r["quality"] == "gold"]
    for name, vocab in MULTI.items():
        ok = sum(set(v for v, f in zip(vocab, mps[name][i]) if f) == set(seed[i][name]) for i in gold_rows)
        print(f"{name:14s} exact-set match on gold seed: {ok}/{len(gold_rows)}")
    print(f"\nsaved {out_dir}")


if __name__ == "__main__":
    main()
