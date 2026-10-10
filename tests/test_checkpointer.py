"""Test save_checkpoint / load_checkpoint round-trip."""
import json
import uuid

from agents.checkpointer import save_checkpoint, load_checkpoint


def test_save_load():
    sid = str(uuid.uuid4())
    save_checkpoint(sid, "FPT", {"ticker": "FPT", "risk_verdict": "OK", "step_count": 3})
    row = load_checkpoint(sid)
    assert row is not None
    assert row["ticker"] == "FPT"
    assert row["status"] == "pending"
    state = row["state"]
    if isinstance(state, str):
        state = json.loads(state)
    assert state["ticker"] == "FPT"
    assert state["risk_verdict"] == "OK"
    print(f"OK: session_id={sid[:8]}... ticker={row['ticker']} status={row['status']}")


if __name__ == "__main__":
    test_save_load()
    print("All tests passed.")
