"""
ArthaTrace — Exception Engine
-------------------------------
Reads the matching engine's output, categorizes every unresolved
record by root cause (from the rejection reason text — no hidden
ground truth used), and ranks exceptions by ₹ "cost of delay" so
the queue reads as business urgency, not just a static list.

Run:
    python3 exception_engine.py
Reads:
    ../output/match_results.json
    ../data/ledger.csv
    ../data/bank_statement.csv
Writes:
    ../output/exception_report.json
"""

import csv
import json
import re
from datetime import datetime, date

ANNUAL_COST_OF_CAPITAL = 0.12  # assumption, stated openly — 12%/yr
DAILY_RATE = ANNUAL_COST_OF_CAPITAL / 365

CATEGORY_PATTERNS = [
    (r"amount off by",                         "amount_mismatch"),
    (r"date off by",                           "date_drift_exceeded"),
    (r"no bank record found",                  "missing_bank_counterpart"),
    (r"no ledger record found",                "missing_ledger_counterpart"),
    (r"reference .* collides",                 "duplicate_reference_unresolved"),
    (r"sum of \d+ bank legs",                  "split_payment_mismatch"),
]


def categorize(reason: str) -> str:
    if not reason:
        return "uncategorized"
    for pattern, label in CATEGORY_PATTERNS:
        if re.search(pattern, reason):
            return label
    return "uncategorized"


def load_csv_indexed(path, id_field):
    with open(path, newline="") as f:
        rows = list(csv.DictReader(f))
    return {r[id_field]: r for r in rows}


def parse_date(s):
    return datetime.strptime(s, "%Y-%m-%d").date()


def build_exceptions(match_results, ledger_idx, bank_idx, as_of: date):
    exceptions = []

    for r in match_results:
        if r["status"] != "unresolved":
            continue

        if r["ledger_id"]:
            side = "ledger"
            record_id = r["ledger_id"]
            src = ledger_idx.get(record_id)
            if not src:
                continue
            amount = float(src["amount"])
            record_date = parse_date(src["txn_date"])
            counterparty = src["counterparty"]
        elif r["bank_id"]:
            side = "bank"
            record_id = r["bank_id"] if isinstance(r["bank_id"], str) else r["bank_id"][0]
            src = bank_idx.get(record_id)
            if not src:
                continue
            amount = float(src["amount"])
            record_date = parse_date(src["value_date"])
            counterparty = None
        else:
            continue

        days_outstanding = max((as_of - record_date).days, 0)
        cost_of_delay = round(amount * DAILY_RATE * days_outstanding, 2)
        category = categorize(r.get("rejection_reason", ""))

        exceptions.append({
            "exception_id": f"EXC-{side.upper()}-{record_id}",
            "side": side,
            "record_id": record_id,
            "ref": r.get("ref"),
            "category": category,
            "amount": amount,
            "counterparty": counterparty,
            "record_date": str(record_date),
            "days_outstanding": days_outstanding,
            "cost_of_delay_inr": cost_of_delay,
            "rejection_reason": r.get("rejection_reason"),
            "near_miss_candidate": r.get("near_miss_candidate"),
            "near_miss_score": r.get("near_miss_score"),
        })

    exceptions.sort(key=lambda e: e["cost_of_delay_inr"], reverse=True)
    return exceptions


def summarize(exceptions):
    total_at_risk = round(sum(e["amount"] for e in exceptions), 2)
    total_cost_of_delay = round(sum(e["cost_of_delay_inr"] for e in exceptions), 2)

    by_category = {}
    for e in exceptions:
        c = e["category"]
        by_category.setdefault(c, {"count": 0, "amount_at_risk": 0.0})
        by_category[c]["count"] += 1
        by_category[c]["amount_at_risk"] += e["amount"]
    for c in by_category:
        by_category[c]["amount_at_risk"] = round(by_category[c]["amount_at_risk"], 2)

    return {
        "total_exceptions": len(exceptions),
        "total_amount_at_risk_inr": total_at_risk,
        "total_cost_of_delay_inr": total_cost_of_delay,
        "assumed_annual_cost_of_capital": ANNUAL_COST_OF_CAPITAL,
        "by_category": by_category,
        "top_5_costliest": exceptions[:5],
    }


if __name__ == "__main__":
    with open("../output/match_results.json") as f:
        match_data = json.load(f)

    ledger_idx = load_csv_indexed("../data/ledger.csv", "ledger_id")
    bank_idx = load_csv_indexed("../data/bank_statement.csv", "bank_id")

    # "as of" date = latest date seen in the batch + 1 day, so aging is
    # always relative to the data itself, not the machine's real clock
    all_dates = [parse_date(r["txn_date"]) for r in ledger_idx.values()]
    as_of = max(all_dates) + __import__("datetime").timedelta(days=1)

    exceptions = build_exceptions(match_data["results"], ledger_idx, bank_idx, as_of)
    summary = summarize(exceptions)

    output = {"as_of_date": str(as_of), "summary": summary, "exceptions": exceptions}
    with open("../output/exception_report.json", "w") as f:
        json.dump(output, f, indent=2, default=str)

    print(json.dumps(summary, indent=2, default=str))
    print(f"\nFull report written to ../output/exception_report.json")