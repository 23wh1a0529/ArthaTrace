"""
ArthaTrace — Matching Engine
-----------------------------
Deterministic, rule-based reconciliation between ledger.csv and
bank_statement.csv. Deliberately NO LLM anywhere in this file —
matching decisions must be explainable and reproducible, not
probabilistic. (The LLM's job, later, is only to narrate what
this engine already decided — never to decide.)

Tiers:
  1. EXACT     - same reference, amount matches to the paisa, same date
  2. FUZZY     - same reference, amount/date within defined tolerance
  3. SPLIT     - one ledger entry == sum of multiple bank entries (same ref)
  4. DUPLICATE - same reference appears on >1 ledger entry; resolved by
                 picking the closest candidate on amount+date, with the
                 collision itself flagged (never silently resolved)
  5. UNRESOLVED - no acceptable candidate. We still record the closest
                 candidate considered and exactly why it was rejected —
                 this is the "near-miss" record, and it's the core of
                 what makes this project different from a plain matcher.

Run:
    python3 matching_engine.py
Reads:
    ../data/ledger.csv
    ../data/bank_statement.csv
Writes:
    ../output/match_results.json
"""

import csv
import json
import re
from datetime import datetime

REF_PATTERN = re.compile(r"RZP26\s*(\d{6})", re.IGNORECASE)
# Tolerances — deliberately explicit and named, never magic numbers buried
# in the logic. A reviewer should be able to read these and agree/disagree.
AMOUNT_ABS_TOLERANCE = 3.00       # rupees, covers rounding/FX noise
AMOUNT_PCT_TOLERANCE = 0.005      # 0.5% of amount, covers larger-value noise
DATE_TOLERANCE_DAYS = 7           # covers typical settlement lag


def load_csv(path):
    with open(path, newline="") as f:
        return list(csv.DictReader(f))


def parse_date(s):
    return datetime.strptime(s, "%Y-%m-%d").date()


def extract_ref(narration):
    m = REF_PATTERN.search(narration)
    return f"RZP26{m.group(1)}" if m else None


def amount_ok(a, b):
    diff = abs(a - b)
    tolerance = max(AMOUNT_ABS_TOLERANCE, AMOUNT_PCT_TOLERANCE * max(a, b))
    return diff <= tolerance, diff


def date_ok(d1, d2):
    diff = abs((d1 - d2).days)
    return diff <= DATE_TOLERANCE_DAYS, diff


def closeness_score(amount_diff, date_diff, amount_ref):
    """Lower is better. Normalized so amount and date are comparable."""
    amt_component = amount_diff / max(amount_ref, 1)
    date_component = date_diff / 30.0  # scale date drift against a month
    return round(amt_component * 0.7 + date_component * 0.3, 5)


def rejection_reason(amount_diff, date_diff, amount_ref):
    reasons = []
    amt_pct = (amount_diff / amount_ref * 100) if amount_ref else 0
    if not amount_ok(amount_ref, amount_ref - amount_diff)[0]:
        reasons.append(f"amount off by ₹{amount_diff:,.2f} ({amt_pct:.1f}%) — outside tolerance")
    if date_diff > DATE_TOLERANCE_DAYS:
        reasons.append(f"date off by {date_diff} days — outside {DATE_TOLERANCE_DAYS}-day settlement window")
    return "; ".join(reasons) if reasons else "within tolerance on both, but a stronger candidate existed"


def run_matching(ledger, bank):
    for l in ledger:
        l["amount"] = float(l["amount"])
        l["txn_date"] = parse_date(l["txn_date"])
    for b in bank:
        b["amount"] = float(b["amount"])
        b["value_date"] = parse_date(b["value_date"])
        b["ref"] = extract_ref(b["narration"])

    ledger_by_ref = {}
    for l in ledger:
        ledger_by_ref.setdefault(l["txn_ref"], []).append(l)

    bank_by_ref = {}
    for b in bank:
        if b["ref"]:
            bank_by_ref.setdefault(b["ref"], []).append(b)

    results = []
    matched_ledger_ids = set()
    matched_bank_ids = set()

    all_refs = set(ledger_by_ref) | set(bank_by_ref)

    for ref in sorted(all_refs):
        l_entries = ledger_by_ref.get(ref, [])
        b_entries = bank_by_ref.get(ref, [])

        # --- Case: clean 1:1 ---
        if len(l_entries) == 1 and len(b_entries) == 1:
            l, b = l_entries[0], b_entries[0]
            a_ok, a_diff = amount_ok(l["amount"], b["amount"])
            d_ok, d_diff = date_ok(l["txn_date"], b["value_date"])
            if a_diff == 0 and d_diff == 0:
                tier = "exact"
            elif a_ok and d_ok:
                tier = "fuzzy"
            else:
                tier = None

            if tier:
                results.append({
                    "ledger_id": l["ledger_id"], "bank_id": b["bank_id"], "ref": ref,
                    "status": "matched", "tier": tier,
                    "amount_diff": round(a_diff, 2), "date_diff_days": d_diff,
                    "true_case": l["true_case"],
                })
                matched_ledger_ids.add(l["ledger_id"])
                matched_bank_ids.add(b["bank_id"])
            else:
                score = closeness_score(a_diff, d_diff, l["amount"])
                results.append({
                    "ledger_id": l["ledger_id"], "bank_id": None, "ref": ref,
                    "status": "unresolved",
                    "near_miss_candidate": b["bank_id"],
                    "near_miss_score": score,
                    "rejection_reason": rejection_reason(a_diff, d_diff, l["amount"]),
                    "true_case": l["true_case"],
                })
                results.append({
                    "ledger_id": None, "bank_id": b["bank_id"], "ref": ref,
                    "status": "unresolved",
                    "near_miss_candidate": l["ledger_id"],
                    "near_miss_score": score,
                    "rejection_reason": rejection_reason(a_diff, d_diff, l["amount"]),
                    "true_case": None,
                })

        # --- Case: split payment (1 ledger, 2+ bank) ---
        elif len(l_entries) == 1 and len(b_entries) > 1:
            l = l_entries[0]
            total_bank = sum(b["amount"] for b in b_entries)
            a_ok, a_diff = amount_ok(l["amount"], total_bank)
            if a_ok:
                results.append({
                    "ledger_id": l["ledger_id"],
                    "bank_id": [b["bank_id"] for b in b_entries],
                    "ref": ref, "status": "matched", "tier": "split_payment",
                    "amount_diff": round(a_diff, 2),
                    "true_case": l["true_case"],
                })
                matched_ledger_ids.add(l["ledger_id"])
                for b in b_entries:
                    matched_bank_ids.add(b["bank_id"])
            else:
                score = closeness_score(a_diff, 0, l["amount"])
                results.append({
                    "ledger_id": l["ledger_id"], "bank_id": None, "ref": ref,
                    "status": "unresolved",
                    "near_miss_candidate": [b["bank_id"] for b in b_entries],
                    "near_miss_score": score,
                    "rejection_reason": f"sum of {len(b_entries)} bank legs (₹{total_bank:,.2f}) "
                                         f"doesn't reconcile to ledger amount (₹{l['amount']:,.2f})",
                    "true_case": l["true_case"],
                })

        # --- Case: duplicate reference (2+ ledger entries share a ref) ---
        elif len(l_entries) > 1:
            remaining_bank = list(b_entries)
            for l in l_entries:
                if not remaining_bank:
                    results.append({
                        "ledger_id": l["ledger_id"], "bank_id": None, "ref": ref,
                        "status": "unresolved",
                        "rejection_reason": f"reference '{ref}' collides across {len(l_entries)} ledger "
                                             f"entries; no bank candidate left to assign after resolving the others",
                        "true_case": l["true_case"],
                    })
                    continue
                scored = []
                for b in remaining_bank:
                    a_ok, a_diff = amount_ok(l["amount"], b["amount"])
                    d_ok, d_diff = date_ok(l["txn_date"], b["value_date"])
                    scored.append((closeness_score(a_diff, d_diff, l["amount"]), b, a_diff, d_diff, a_ok, d_ok))
                scored.sort(key=lambda x: x[0])
                best_score, best_b, a_diff, d_diff, a_ok, d_ok = scored[0]

                results.append({
                    "ledger_id": l["ledger_id"], "bank_id": best_b["bank_id"], "ref": ref,
                    "status": "matched", "tier": "duplicate_ref_resolved",
                    "amount_diff": round(a_diff, 2), "date_diff_days": d_diff,
                    "collision_flag": f"reference '{ref}' shared by {len(l_entries)} ledger entries — "
                                       f"resolved by closest amount+date match, human review recommended",
                    "true_case": l["true_case"],
                })
                matched_ledger_ids.add(l["ledger_id"])
                matched_bank_ids.add(best_b["bank_id"])
                remaining_bank.remove(best_b)

        # --- Case: orphan ledger (ref only on ledger side) ---
        elif l_entries and not b_entries:
            for l in l_entries:
                best = find_best_cross_ref_candidate(l, bank, matched_bank_ids, pool_is_bank=True)
                results.append({
                    "ledger_id": l["ledger_id"], "bank_id": None, "ref": ref,
                    "status": "unresolved",
                    "near_miss_candidate": best["bank_id"] if best else None,
                    "near_miss_score": best["score"] if best else None,
                    "rejection_reason": (
                        f"no bank record found with matching reference; closest overall bank "
                        f"candidate ({best['bank_id']}) had no reference match at all"
                        if best else "no bank record found with matching reference, and no plausible "
                                      "amount/date candidate exists in the batch either"
                    ),
                    "true_case": l["true_case"],
                })

        # --- Case: orphan bank (ref only on bank side) ---
        elif b_entries and not l_entries:
            for b in b_entries:
                best = find_best_cross_ref_candidate(b, ledger, matched_ledger_ids, pool_is_bank=False)
                results.append({
                    "ledger_id": None, "bank_id": b["bank_id"], "ref": ref,
                    "status": "unresolved",
                    "near_miss_candidate": best["ledger_id"] if best else None,
                    "near_miss_score": best["score"] if best else None,
                    "rejection_reason": (
                        f"no ledger record found with matching reference; closest overall ledger "
                        f"candidate ({best['ledger_id']}) had no reference match at all"
                        if best else "no ledger record found with matching reference, and no plausible "
                                      "amount/date candidate exists in the batch either"
                    ),
                    "true_case": None,
                })

    return results


def find_best_cross_ref_candidate(record, pool, already_matched_ids, pool_is_bank=False):
    """When reference matching fails entirely, still look for the closest
    amount+date candidate across the WHOLE opposite pool, purely so we can
    give an honest near-miss explanation instead of a bare 'no match'.

    `pool` is always the OPPOSITE side's full record list from `record`.
    `pool_is_bank` tells us whether `pool` contains bank records (True)
    or ledger records (False) — that determines which keys to read.
    """
    best = None
    record_is_bank = "bank_id" in record
    for cand in pool:
        cid = cand["bank_id"] if pool_is_bank else cand["ledger_id"]
        if cid in already_matched_ids:
            continue
        c_amount = cand["amount"]
        c_date = cand["value_date"] if pool_is_bank else cand["txn_date"]
        r_amount = record["amount"]
        r_date = record["value_date"] if record_is_bank else record["txn_date"]
        a_diff = abs(c_amount - r_amount)
        d_diff = abs((c_date - r_date).days)
        score = closeness_score(a_diff, d_diff, r_amount)
        if best is None or score < best["score"]:
            best = {"bank_id": cand.get("bank_id"), "ledger_id": cand.get("ledger_id"), "score": score}
    return best


def summarize(results, total_ledger, total_bank):
    matched = [r for r in results if r["status"] == "matched"]
    unresolved = [r for r in results if r["status"] == "unresolved"]

    tier_counts = {}
    for r in matched:
        tier_counts[r["tier"]] = tier_counts.get(r["tier"], 0) + 1

    # accuracy sanity-check against ground truth (true_case) — internal eval only
    correct = 0
    checkable = 0
    for r in results:
        if r.get("true_case"):
            checkable += 1
            should_match = r["true_case"] not in ("orphan_ledger", "orphan_bank")
            did_match = r["status"] == "matched"
            if should_match == did_match:
                correct += 1

    return {
        "total_ledger_records": total_ledger,
        "total_bank_records": total_bank,
        "matched_count": len(matched),
        "unresolved_count": len(unresolved),
        "tier_breakdown": tier_counts,
        "internal_accuracy_check": f"{correct}/{checkable} ({correct/checkable*100:.1f}%)" if checkable else "n/a",
    }


if __name__ == "__main__":
    ledger = load_csv("../data/ledger.csv")
    bank = load_csv("../data/bank_statement.csv")

    results = run_matching(ledger, bank)
    summary = summarize(results, len(ledger), len(bank))

    output = {"summary": summary, "results": results}
    with open("../output/match_results.json", "w") as f:
        json.dump(output, f, indent=2, default=str)

    print(json.dumps(summary, indent=2))
    print(f"\nFull results written to ../output/match_results.json")
