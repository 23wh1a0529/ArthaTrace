"""
Reconciler — Synthetic Data Generator
--------------------------------------
Generates two sources that a real finance team would reconcile:
  - ledger.csv          (internal system of record)
  - bank_statement.csv  (external bank feed)

The data is DELIBERATELY messy. Clean 1:1 data proves nothing —
the whole point of Reconciler is to be judged on how it handles
the mess, not on a trivial 100% match rate.

Categories injected (tracked via `true_case` column, kept ONLY in
the ledger file for later scoring/eval — the matcher never sees it):

  exact           - clean 1:1 match, no drift at all
  date_drift      - same txn, bank settles 1-7 days later
  rounding_noise  - tiny amount mismatch (fees/rounding/FX)
  split_payment   - one ledger entry == sum of two bank entries
  duplicate_ref   - two unrelated ledger entries share a reference
                    (a real-world collision risk, not a generator bug)
  timezone_shift  - bank date off by 1 day due to UTC/IST cutoff
  partial_refund  - bank entry only partially offsets ledger amount
  orphan_ledger   - ledger entry with no bank-side counterpart
  orphan_bank     - bank entry with no ledger-side counterpart

Run:
    python3 generate_data.py
Outputs:
    ../data/ledger.csv
    ../data/bank_statement.csv
"""

import csv
import random
from datetime import date, timedelta

random.seed(42)  # reproducible dataset

OUT_DIR = "../data"
BASE_DATE = date(2026, 8, 1)
COUNTERPARTIES = [
    "Acme Retail Pvt Ltd", "Nimbus Traders", "BlueDart Logistics",
    "Sundaram Textiles", "Orbit Foods", "Kaveri Electronics",
    "Prime Consulting LLP", "Vega Software Solutions",
]
CATEGORIES = ["payment_in", "refund", "payout", "fee"]


def rand_date(base, spread_days=45):
    return base + timedelta(days=random.randint(0, spread_days))


def make_ref(i):
    return f"RZP26{i:06d}"


def fmt(d):
    return d.strftime("%Y-%m-%d")


def build_dataset(n_base=55):
    ledger_rows = []
    bank_rows = []
    lid, bid = 1, 1

    # Decide how many of each messiness category to inject
    plan = (
        ["exact"] * 24
        + ["date_drift"] * 8
        + ["rounding_noise"] * 6
        + ["split_payment"] * 4
        + ["duplicate_ref"] * 3
        + ["timezone_shift"] * 5
        + ["partial_refund"] * 3
        + ["orphan_ledger"] * 4
        + ["orphan_bank"] * 4
    )
    random.shuffle(plan)

    ref_pool_for_dupes = []  # refs we'll intentionally reuse

    for i, case in enumerate(plan, start=1):
        ref = make_ref(i)
        txn_date = rand_date(BASE_DATE)
        amount = round(random.uniform(500, 85000), 2)
        counterparty = random.choice(COUNTERPARTIES)
        category = random.choice(CATEGORIES)

        if case == "exact":
            ledger_rows.append([f"LGR-{lid:04d}", ref, amount, fmt(txn_date),
                                 counterparty, category, case])
            lid += 1
            bank_rows.append([f"BNK-{bid:04d}", f"NEFT/{ref}/{counterparty}",
                               amount, fmt(txn_date), f"UTR{bid:08d}"])
            bid += 1

        elif case == "date_drift":
            ledger_rows.append([f"LGR-{lid:04d}", ref, amount, fmt(txn_date),
                                 counterparty, category, case])
            lid += 1
            drift = timedelta(days=random.randint(1, 7))
            bank_rows.append([f"BNK-{bid:04d}", f"NEFT/{ref}/{counterparty}",
                               amount, fmt(txn_date + drift), f"UTR{bid:08d}"])
            bid += 1

        elif case == "rounding_noise":
            ledger_rows.append([f"LGR-{lid:04d}", ref, amount, fmt(txn_date),
                                 counterparty, category, case])
            lid += 1
            noise = round(random.uniform(-2.5, 2.5), 2)
            bank_rows.append([f"BNK-{bid:04d}", f"NEFT/{ref}/{counterparty}",
                               round(amount + noise, 2), fmt(txn_date), f"UTR{bid:08d}"])
            bid += 1

        elif case == "split_payment":
            ledger_rows.append([f"LGR-{lid:04d}", ref, amount, fmt(txn_date),
                                 counterparty, category, case])
            lid += 1
            part1 = round(amount * random.uniform(0.3, 0.6), 2)
            part2 = round(amount - part1, 2)
            bank_rows.append([f"BNK-{bid:04d}", f"NEFT/{ref}/PART1/{counterparty}",
                               part1, fmt(txn_date), f"UTR{bid:08d}"])
            bid += 1
            bank_rows.append([f"BNK-{bid:04d}", f"NEFT/{ref}/PART2/{counterparty}",
                               part2, fmt(txn_date), f"UTR{bid:08d}"])
            bid += 1

        elif case == "duplicate_ref":
            # reuse an earlier ref on a totally unrelated txn -> collision risk
            if ref_pool_for_dupes and random.random() < 0.6:
                ref = random.choice(ref_pool_for_dupes)
            else:
                ref_pool_for_dupes.append(ref)
            ledger_rows.append([f"LGR-{lid:04d}", ref, amount, fmt(txn_date),
                                 counterparty, category, case])
            lid += 1
            bank_rows.append([f"BNK-{bid:04d}", f"NEFT/{ref}/{counterparty}",
                               amount, fmt(txn_date), f"UTR{bid:08d}"])
            bid += 1

        elif case == "timezone_shift":
            ledger_rows.append([f"LGR-{lid:04d}", ref, amount, fmt(txn_date),
                                 counterparty, category, case])
            lid += 1
            shift = timedelta(days=1)
            bank_rows.append([f"BNK-{bid:04d}", f"NEFT/{ref}/{counterparty}",
                               amount, fmt(txn_date - shift), f"UTR{bid:08d}"])
            bid += 1

        elif case == "partial_refund":
            ledger_rows.append([f"LGR-{lid:04d}", ref, amount, fmt(txn_date),
                                 counterparty, "refund", case])
            lid += 1
            partial = round(amount * random.uniform(0.4, 0.8), 2)
            bank_rows.append([f"BNK-{bid:04d}", f"NEFT/{ref}/REFUND/{counterparty}",
                               partial, fmt(txn_date), f"UTR{bid:08d}"])
            bid += 1

        elif case == "orphan_ledger":
            # exists only in books — bank leg missing (e.g. payout stuck in processing)
            ledger_rows.append([f"LGR-{lid:04d}", ref, amount, fmt(txn_date),
                                 counterparty, category, case])
            lid += 1

        elif case == "orphan_bank":
            # money moved in bank with no matching book entry (e.g. unrecorded fee)
            bank_rows.append([f"BNK-{bid:04d}", f"NEFT/{ref}/{counterparty}",
                               amount, fmt(txn_date), f"UTR{bid:08d}"])
            bid += 1

    random.shuffle(ledger_rows)
    random.shuffle(bank_rows)
    return ledger_rows, bank_rows


def write_csv(path, header, rows):
    with open(path, "w", newline="") as f:
        w = csv.writer(f)
        w.writerow(header)
        w.writerows(rows)


if __name__ == "__main__":
    ledger_rows, bank_rows = build_dataset()

    write_csv(f"{OUT_DIR}/ledger.csv",
              ["ledger_id", "txn_ref", "amount", "txn_date", "counterparty",
               "category", "true_case"],
              ledger_rows)

    write_csv(f"{OUT_DIR}/bank_statement.csv",
              ["bank_id", "narration", "amount", "value_date", "utr"],
              bank_rows)

    print(f"Ledger rows:  {len(ledger_rows)}")
    print(f"Bank rows:    {len(bank_rows)}")
    from collections import Counter
    print("Case breakdown (ground truth, ledger-side only):")
    for case, count in Counter(r[6] for r in ledger_rows).most_common():
        print(f"  {case:16s} {count}")
