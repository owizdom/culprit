"""A hard ceiling on money spent on model calls, shared by the agent and the evaluation.

Every call is priced from its reported usage and appended to .cache/spend.json. A call is refused
before it is made once the total has reached CULPRIT_BUDGET_USD.
"""
import json
import os
import threading
from datetime import datetime, timezone

from paths import HOME

LEDGER = HOME / ".cache" / "spend.json"
PRICE_PER_MTOK = {"claude-opus-5": (5.0, 25.0), "claude-sonnet-5": (2.0, 10.0), "claude-haiku-4-5": (1.0, 5.0)}
_lock = threading.Lock()


class OverBudget(Exception):
    pass


def _env():
    env_file = HOME / ".env"
    if env_file.exists():
        for line in env_file.read_text().splitlines():
            if "=" in line and not line.lstrip().startswith("#"):
                key, value = line.split("=", 1)
                os.environ.setdefault(key.strip(), value.strip())


def limit():
    _env()
    return float(os.environ.get("CULPRIT_BUDGET_USD", "6.0"))


def spent():
    if not LEDGER.exists():
        return 0.0
    return sum(entry["usd"] for entry in json.loads(LEDGER.read_text()))


def check(purpose):
    total, cap = spent(), limit()
    if total >= cap:
        raise OverBudget(f"model budget reached: ${total:.2f} of ${cap:.2f} spent; refusing {purpose}")


def record(model, usage, purpose):
    price_in, price_out = PRICE_PER_MTOK[model]
    usd = (usage.input_tokens * price_in + usage.output_tokens * price_out) / 1e6
    entry = {"t": datetime.now(timezone.utc).isoformat(timespec="seconds"), "model": model, "purpose": purpose,
             "input_tokens": usage.input_tokens, "output_tokens": usage.output_tokens, "usd": round(usd, 5)}
    with _lock:
        LEDGER.parent.mkdir(parents=True, exist_ok=True)
        entries = json.loads(LEDGER.read_text()) if LEDGER.exists() else []
        entries.append(entry)
        LEDGER.write_text(json.dumps(entries, indent=2))
    return usd
