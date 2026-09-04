"""
ArthaTrace — Full Pipeline Orchestrator
------------------------------------------
Runs the entire deterministic + LLM pipeline in one command and
writes a single consolidated dashboard_data.json that the frontend
reads. This is the file you actually run for a full demo.

Run:
    python3 run_pipeline.py
Reads:
    ../data/ledger.csv
    ../data/bank_statement.csv
Writes:
    ../output/match_results.json
    ../output/exception_report.json
    ../output/dashboard_data.json
"""

import json
import time
from datetime import timedelta

import matching_engine as me
import exception_engine as ee
import llm_layer as llm

# We deliberately do NOT call the LLM for every single exception.
# On a real batch this could be dozens of exceptions — burning API
# calls (and hitting free-tier rate limits) on all of them adds
# little value over a clear rule-based summary. We reserve the LLM
# for the exceptions that matter most: the costliest ones. This is
# an explicit "AI judgment" call, not an oversight — worth saying
# out loud in the README and pitch video.
LLM_EXPLANATION_TOP_N = 5


def run_full_pipeline():
    print("Step A: loading source data...")
    ledger = me.load_csv("../data/ledger.csv")
    bank = me.load_csv("../data/bank_statement.csv")

    print("Step B: running deterministic matching engine...")
    match_results = me.run_matching(ledger, bank)
    match_summary = me.summarize(match_results, len(ledger), len(bank))
    with open("../output/match_results.json", "w") as f:
        json.dump({"summary": match_summary, "results": match_results}, f, indent=2, default=str)
    print(f"  matched={match_summary['matched_count']} unresolved={match_summary['unresolved_count']}")

    print("Step C: building exception queue with aging + cost of delay...")
    ledger_idx = ee.load_csv_indexed("../data/ledger.csv", "ledger_id")
    bank_idx = ee.load_csv_indexed("../data/bank_statement.csv", "bank_id")
    all_dates = [ee.parse_date(r["txn_date"]) for r in ledger_idx.values()]
    as_of = max(all_dates) + timedelta(days=1)
    exceptions = ee.build_exceptions(match_results, ledger_idx, bank_idx, as_of)
    exc_summary = ee.summarize(exceptions)

    print(f"Step D: generating LLM explanations for top {LLM_EXPLANATION_TOP_N} costliest exceptions...")
    llm.load_env()
    for i, exc in enumerate(exceptions):
        if i < LLM_EXPLANATION_TOP_N:
            exc["llm_explanation"] = llm.explain_exception(exc)
            time.sleep(1)  # be polite to the free-tier rate limit
        else:
            exc["llm_explanation"] = None  # deliberately not generated — see note above

    exception_report = {"as_of_date": str(as_of), "summary": exc_summary, "exceptions": exceptions}
    with open("../output/exception_report.json", "w") as f:
        json.dump(exception_report, f, indent=2, default=str)

    print("Step E: writing consolidated dashboard_data.json...")
    dashboard_data = {
        "generated_at": str(as_of),
        "match_summary": match_summary,
        "exception_summary": exc_summary,
        "exceptions": exceptions,
        "llm_explanation_note": (
            f"LLM explanations generated only for the top {LLM_EXPLANATION_TOP_N} "
            f"costliest exceptions by design — see run_pipeline.py comments."
        ),
    }
    with open("../output/dashboard_data.json", "w") as f:
        json.dump(dashboard_data, f, indent=2, default=str)

    print("\nPipeline complete. Files written to ../output/:")
    print("  match_results.json, exception_report.json, dashboard_data.json")


if __name__ == "__main__":
    run_full_pipeline()