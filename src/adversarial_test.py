"""
ArthaTrace — Adversarial Stress Test
--------------------------------------
Deliberately feeds the matching engine nastier inputs than the main
synthetic dataset contains, to find weaknesses BEFORE a judge or a
real user does. This is what generates our real "what broke, and
how you got out" story — every case here is a genuine stress test,
not a scripted demo.

Run:
    python3 adversarial_test.py
Writes:
    ../output/adversarial_report.json
"""

import json
import sys
from datetime import date

sys.path.insert(0, ".")
import matching_engine as me


def make_ledger(ledger_id, ref, amount, txn_date, counterparty="TestCo",
                 category="payment_in", true_case="adversarial"):
    return {"ledger_id": ledger_id, "txn_ref": ref, "amount": amount,
            "txn_date": txn_date, "counterparty": counterparty,
            "category": category, "true_case": true_case}


def make_bank(bank_id, narration, amount, value_date, utr="UTR-ADV"):
    return {"bank_id": bank_id, "narration": narration, "amount": amount,
            "value_date": value_date, "utr": utr}


CASES = []

def case(name, expected, ledger_rows, bank_rows):
    CASES.append({"name": name, "expected": expected,
                   "ledger": ledger_rows, "bank": bank_rows})


# Case 1 — lowercase reference in narration (real-world bank feeds are inconsistent)
case(
    "lowercase_ref_in_narration",
    "should match — same ref, amount, date, just different letter case",
    [make_ledger("LGR-A1", "RZP26000999", 12000.00, "2026-08-10")],
    [make_bank("BNK-A1", "neft/rzp26000999/testco", 12000.00, "2026-08-10")],
)

# Case 2 — reference broken up by extra spacing (real bank narrations do this)
case(
    "ref_broken_by_internal_spacing",
    "should match — same underlying ref, just formatted with a space in the middle",
    [make_ledger("LGR-A2", "RZP26000998", 8500.00, "2026-08-11")],
    [make_bank("BNK-A2", "NEFT/RZP26 000998/TESTCO", 8500.00, "2026-08-11")],
)

# Case 3 — date drift exactly at the tolerance boundary (7 days = should match)
case(
    "date_drift_exactly_at_boundary",
    "should match — 7 days is inside the stated tolerance, inclusive",
    [make_ledger("LGR-A3", "RZP26000997", 5000.00, "2026-08-01")],
    [make_bank("BNK-A3", "NEFT/RZP26000997/TESTCO", 5000.00, "2026-08-08")],
)

# Case 4 — date drift one day past the boundary (8 days = should NOT match)
case(
    "date_drift_one_day_past_boundary",
    "should be unresolved — 8 days is outside the stated 7-day tolerance",
    [make_ledger("LGR-A4", "RZP26000996", 5000.00, "2026-08-01")],
    [make_bank("BNK-A4", "NEFT/RZP26000996/TESTCO", 5000.00, "2026-08-09")],
)

# Case 5 — zero-amount transaction (defensive: division-by-zero risk)
case(
    "zero_amount_transaction",
    "should match without crashing — zero-value entries shouldn't break scoring math",
    [make_ledger("LGR-A5", "RZP26000995", 0.00, "2026-08-05")],
    [make_bank("BNK-A5", "NEFT/RZP26000995/TESTCO", 0.00, "2026-08-05")],
)

# Case 6 — triple duplicate reference, only 2 bank legs available (exhaustion case)
case(
    "triple_duplicate_ref_bank_exhausted",
    "2 of 3 ledger entries should resolve; the 3rd should be unresolved "
    "with a clear 'no candidate left' reason, not a crash",
    [
        make_ledger("LGR-A6a", "RZP26000994", 10000.00, "2026-08-01"),
        make_ledger("LGR-A6b", "RZP26000994", 20000.00, "2026-08-02"),
        make_ledger("LGR-A6c", "RZP26000994", 30000.00, "2026-08-03"),
    ],
    [
        make_bank("BNK-A6a", "NEFT/RZP26000994/TESTCO", 10000.00, "2026-08-01"),
        make_bank("BNK-A6b", "NEFT/RZP26000994/TESTCO", 20000.00, "2026-08-02"),
    ],
)


def run_case(c):
    ledger = [dict(l) for l in c["ledger"]]
    bank = [dict(b) for b in c["bank"]]
    try:
        results = me.run_matching(ledger, bank)
        return {"name": c["name"], "expected": c["expected"], "crashed": False,
                "results": results}
    except Exception as e:
        return {"name": c["name"], "expected": c["expected"], "crashed": True,
                "error": f"{type(e).__name__}: {e}"}


if __name__ == "__main__":
    report = []
    for c in CASES:
        outcome = run_case(c)
        report.append(outcome)
        status = "CRASHED" if outcome["crashed"] else "ran"
        print(f"[{status:8s}] {outcome['name']}")

    with open("../output/adversarial_report.json", "w") as f:
        json.dump(report, f, indent=2, default=str)

    print(f"\n{len(CASES)} adversarial cases run. Full report in ../output/adversarial_report.json")