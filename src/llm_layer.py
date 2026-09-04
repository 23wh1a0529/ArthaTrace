"""
ArthaTrace — LLM Explanation & Citation-Only Q&A Layer
---------------------------------------------------------
The ONLY file in this project that talks to an LLM. It never makes
matching or exception decisions — those are already final by the
time anything here runs. Its job is narrow: explain what the
deterministic engine decided, in plain English, and answer
questions strictly grounded in retrieved records.

Uses Google Gemini's free API tier (no card required) via direct
REST calls — no extra SDK install needed.

Setup:
    1. Go to aistudio.google.com, sign in, click "Get API key".
    2. Create a file named .env in the PROJECT ROOT with:
           GEMINI_API_KEY=your_actual_key_here
    3. Add .env to your .gitignore.

Run:
    python3 llm_layer.py
"""

import json
import os
import urllib.request
import urllib.error

MODEL = "gemini-3.6-flash"
API_URL = f"https://generativelanguage.googleapis.com/v1beta/models/{MODEL}:generateContent"


def load_env(path="../.env"):
    if not os.path.exists(path):
        return
    with open(path) as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, _, value = line.partition("=")
            os.environ.setdefault(key.strip(), value.strip())


def call_gemini(prompt, system=None, max_output_tokens=800):
    api_key = os.environ.get("GEMINI_API_KEY")
    if not api_key:
        return f"[DRY RUN — no GEMINI_API_KEY set] Would send prompt:\n{prompt[:200]}..."

    body = {"contents": [{"role": "user", "parts": [{"text": prompt}]}]}
    if system:
        body["system_instruction"] = {"parts": [{"text": system}]}
        body["generationConfig"] = {
        "maxOutputTokens": max_output_tokens,
        "thinkingConfig": {"thinkingLevel": "minimal"},
    }
    req = urllib.request.Request(
        API_URL,
        data=json.dumps(body).encode("utf-8"),
        headers={
            "x-goog-api-key": api_key,
            "Content-Type": "application/json",
        },
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=60) as resp:

            data = json.loads(resp.read().decode("utf-8"))

            return data["candidates"][0]["content"]["parts"][0]["text"]
            
    except urllib.error.HTTPError as e:
        return f"[API ERROR {e.code}] {e.read().decode('utf-8')[:300]}"
    except Exception as e:
        return f"[API ERROR] {type(e).__name__}: {e}"


def explain_exception(exc: dict) -> str:
    system = (
        "You explain finance reconciliation exceptions in plain English for a "
        "non-technical stakeholder. Use ONLY the facts given below. Do not invent "
        "amounts, dates, or reasons not present in the data. Keep it to 2-3 sentences."
    )
    prompt = f"Exception record:\n{json.dumps(exc, indent=2, default=str)}\n\nExplain this exception."
    return call_gemini(prompt, system=system, max_output_tokens=400)


def find_relevant_records(question: str, exception_report: dict, match_results: dict):
    q = question.lower()
    hits = []
    for exc in exception_report.get("exceptions", []):
        haystack = " ".join(str(v) for v in [
            exc.get("exception_id"), exc.get("record_id"), exc.get("ref"),
            exc.get("counterparty"), exc.get("category"),
        ]).lower()
        tokens = [t for t in q.replace("?", "").replace("#", "").split() if len(t) > 2]
        if any(t in haystack for t in tokens):
            hits.append(exc)
    return hits


def answer_question(question: str, exception_report: dict, match_results: dict) -> str:
    hits = find_relevant_records(question, exception_report, match_results)

    if not hits:
        return (
            "I can't trace this to source data — no exception or record in this "
            "batch matches what you're asking about. I won't guess."
        )

    context = json.dumps(hits, indent=2, default=str)
    system = (
        "You answer questions about finance reconciliation exceptions. Use ONLY "
        "the records provided below. You MUST cite the exact exception_id or "
        "record_id for every claim you make. If the provided records don't "
        "actually answer the question, say so explicitly instead of guessing."
    )
    prompt = f"Relevant records:\n{context}\n\nQuestion: {question}"
    answer = call_gemini(prompt, system=system, max_output_tokens=400)

    cited_ids = [h.get("exception_id") or h.get("record_id") for h in hits]
    if not any(str(cid) in answer for cid in cited_ids if cid) and "DRY RUN" not in answer and "API ERROR" not in answer:
        return (f"[Grounded fallback — model answer didn't cite a record ID, so showing raw match instead]\n"
                f"Found {len(hits)} relevant record(s): {', '.join(str(c) for c in cited_ids)}.\n{context[:500]}")
    return answer


if __name__ == "__main__":
    load_env()

    with open("../output/exception_report.json") as f:
        exception_report = json.load(f)
    with open("../output/match_results.json") as f:
        match_results = json.load(f)

    print("=== Explaining the costliest exception ===")
    if exception_report["exceptions"]:
        print(explain_exception(exception_report["exceptions"][0]))

    print("\n=== Q&A test ===")
    top_id = exception_report["exceptions"][0]["record_id"] if exception_report["exceptions"] else "NOTHING"
    print(f"Q: Why is {top_id} unresolved?")
    print("A:", answer_question(f"Why is {top_id} unresolved?", exception_report, match_results))

    print("\nQ: What happened to invoice XYZ999 in Antarctica?")
    print("A:", answer_question("What happened to invoice XYZ999 in Antarctica?", exception_report, match_results))