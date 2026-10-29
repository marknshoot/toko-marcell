#!/usr/bin/env python3
"""
Deterministic business-scenario evaluation for the Toko Marcell copilot.

This harness turns the four core copilot jobs into executable contracts:

  - guardrails     — labelled ID/EN greeting/off-topic/injection/on-topic cases
  - sizing         — exact size_kind/applicable/suggested/disclaimer assertions
  - budget_outfits — 100% invariant: within_budget and total_idr <= budget_idr
  - policy_rag     — expected policy chunk title retrieved in the top-k

The guardrail cases exercise the deterministic regex layer (`use_llm=False`) so
CI never depends on a provider. Sizing, outfit search and policy retrieval use
the same Postgres seed as the API.

Run:
  python pipelines/eval_copilot.py --out pipelines/results/copilot_eval.json
"""

import argparse
import datetime
import json
import pathlib
import subprocess
import sys

ROOT_DIR = pathlib.Path(__file__).resolve().parent.parent
API_DIR = ROOT_DIR / "api"
sys.path.insert(0, str(API_DIR))

from agent_tools import lookup_store_policy, recommend_size  # noqa: E402
from copilot_tools import ToolContext, _build_outfit_impl  # noqa: E402
from guardrail import classify  # noqa: E402
from sizing import load_size_chart, recommend_size_core  # noqa: E402

DEFAULT_GOLDEN = "pipelines/golden/copilot.json"
DEFAULT_OUT = "pipelines/results/copilot_eval.json"
SUITE_NAMES = ("guardrails", "sizing", "budget_outfits", "policy_rag")
MIN_CASES = {"guardrails": 16, "sizing": 16, "budget_outfits": 8, "policy_rag": 10}
LABELS = ("greeting", "off_topic", "injection", "on_topic")

THRESHOLDS = {
    "guardrail_accuracy_gte": 0.95,
    "sizing_accuracy_gte": 1.0,
    "outfit_invariant_violations_eq": 0,
    "policy_recall_gte": 0.90,
}


def _git_commit() -> str:
    try:
        result = subprocess.run(
            ["git", "rev-parse", "--short", "HEAD"],
            capture_output=True,
            text=True,
            timeout=5,
            check=False,
        )
        return result.stdout.strip() or "unknown"
    except Exception:
        return "unknown"


def _safe_div(numerator: int | float, denominator: int | float) -> float:
    return float(numerator) / float(denominator) if denominator else 0.0


def _ratio(numerator: int, denominator: int) -> float:
    return round(_safe_div(numerator, denominator), 4)


def _normalise_label(label: str | None) -> str:
    return label or "on_topic"


def _evaluate_guardrails(cases: list[dict]) -> tuple[dict, list[dict]]:
    details = []
    confusion = {label: {"tp": 0, "fp": 0, "fn": 0, "support": 0} for label in LABELS}

    for case in cases:
        query = case["query"]
        expected = _normalise_label(case.get("expected"))
        record = {
            "id": case.get("id"),
            "query": query,
            "expected": expected,
            "predicted": None,
            "correct": False,
        }
        try:
            predicted = _normalise_label(classify(query, use_llm=False)[0])
            record["predicted"] = predicted
            record["correct"] = predicted == expected
        except Exception as exc:
            record["error"] = str(exc)

        if record["correct"]:
            confusion[expected]["tp"] += 1
        else:
            confusion[expected]["fn"] += 1
            predicted = record["predicted"]
            if predicted in confusion:
                confusion[predicted]["fp"] += 1
        confusion[expected]["support"] += 1
        details.append(record)

    per_label = {}
    for label, counts in confusion.items():
        tp, fp, fn = counts["tp"], counts["fp"], counts["fn"]
        per_label[label] = {
            "support": counts["support"],
            "precision": _ratio(tp, tp + fp),
            "recall": _ratio(tp, tp + fn),
        }

    passed = sum(1 for record in details if record["correct"])
    accuracy = _ratio(passed, len(details))
    metrics = {
        "total": len(details),
        "cases_passed": passed,
        "accuracy": accuracy,
        "per_label": per_label,
        "gate": {
            "accuracy_gte": THRESHOLDS["guardrail_accuracy_gte"],
            "passed": accuracy >= THRESHOLDS["guardrail_accuracy_gte"],
        },
    }
    return metrics, details


def _compare_sizing(actual: dict, expected: dict, disclaimer: str) -> dict[str, bool]:
    checks = {
        "applicable": actual.get("applicable") == expected["applicable"],
        "size_kind": actual.get("size_kind") == expected["size_kind"],
        "suggested": actual.get("suggested") == expected.get("suggested"),
        "disclaimer": actual.get("disclaimer") == disclaimer,
    }

    for key in ("source", "confidence", "brand"):
        if key in expected:
            checks[key] = actual.get(key) == expected[key]

    if "reason_contains" in expected:
        checks["reason"] = expected["reason_contains"] in (actual.get("reason") or "")

    if "brand_offset" in expected:
        offset = actual.get("brand_offset") or {}
        checks["brand_offset"] = all(
            offset.get(key) == value for key, value in expected["brand_offset"].items()
        )

    return checks


def _evaluate_sizing(cases: list[dict], db_url: str | None) -> tuple[dict, list[dict]]:
    disclaimer = load_size_chart()["_meta"]["disclaimer"]
    details = []

    for case in cases:
        inputs = dict(case.get("inputs") or {})
        expected = case["expected"]
        record = {
            "id": case.get("id"),
            "description": case.get("description"),
            "asin": case.get("asin"),
            "inputs": inputs,
            "expected": expected,
            "actual": None,
            "checks": {},
            "passed": False,
        }
        try:
            if case.get("asin"):
                actual = recommend_size(
                    asin=case["asin"], db_url=db_url, **inputs
                )
            else:
                actual = recommend_size_core(**inputs)
            checks = _compare_sizing(actual, expected, disclaimer)
            record["checks"] = checks
            record["passed"] = all(checks.values())
            record["actual"] = {
                key: actual.get(key)
                for key in (
                    "applicable",
                    "size_kind",
                    "suggested",
                    "source",
                    "confidence",
                    "reason",
                    "brand",
                    "brand_offset",
                )
            }
        except Exception as exc:
            record["error"] = str(exc)
        details.append(record)

    passed = sum(1 for record in details if record["passed"])
    accuracy = _ratio(passed, len(details))
    metrics = {
        "total": len(details),
        "cases_passed": passed,
        "accuracy": accuracy,
        "gate": {
            "accuracy_gte": THRESHOLDS["sizing_accuracy_gte"],
            "passed": accuracy >= THRESHOLDS["sizing_accuracy_gte"],
        },
    }
    return metrics, details


def _evaluate_outfits(cases: list[dict], db_url: str | None) -> tuple[dict, list[dict]]:
    details = []

    for case in cases:
        budget = int(case["budget_idr"])
        record = {
            "id": case.get("id"),
            "query": case["query"],
            "budget_idr": budget,
            "department": case.get("department"),
            "within_budget": None,
            "total_idr": None,
            "item_count": 0,
            "items": {},
            "invariant_violation": True,
            "passed": False,
        }
        try:
            ctx = ToolContext(db_url=db_url)
            actual = _build_outfit_impl(
                ctx=ctx,
                query=case["query"],
                budget_idr=budget,
                department=case.get("department"),
            )
            total = actual.get("total_idr")
            within = actual.get("within_budget") is True
            total_within = isinstance(total, int | float) and total <= budget
            violation = not (within and total_within)
            items = actual.get("items") or {}
            record.update(
                {
                    "within_budget": within,
                    "total_idr": total,
                    "item_count": len(items),
                    "items": {
                        slot: {
                            "asin": item.get("asin"),
                            "title": item.get("title"),
                            "price_idr": item.get("priceIdr"),
                        }
                        for slot, item in items.items()
                    },
                    "invariant_violation": violation,
                    "passed": not violation,
                }
            )
        except Exception as exc:
            record["error"] = str(exc)
        details.append(record)

    passed = sum(1 for record in details if record["passed"])
    violations = sum(1 for record in details if record["invariant_violation"])
    metrics = {
        "total": len(details),
        "cases_passed": passed,
        "invariant_violations": violations,
        "gate": {
            "violations_eq": THRESHOLDS["outfit_invariant_violations_eq"],
            "passed": violations == THRESHOLDS["outfit_invariant_violations_eq"],
        },
    }
    return metrics, details


def _evaluate_policy(cases: list[dict], db_url: str | None, k: int) -> tuple[dict, list[dict]]:
    details = []

    for case in cases:
        expected_title = case["expected_title"]
        record = {
            "id": case.get("id"),
            "query": case["query"],
            "language": case.get("language"),
            "topic": case.get("topic"),
            "expected_title": expected_title,
            "retrieved_titles": [],
            "rank": None,
            "hit": False,
        }
        try:
            results = lookup_store_policy(case["query"], limit=k, db_url=db_url)
            titles = [result.get("title", "") for result in results]
            record["retrieved_titles"] = titles
            if expected_title in titles:
                record["rank"] = titles.index(expected_title) + 1
                record["hit"] = True
        except Exception as exc:
            record["error"] = str(exc)
        details.append(record)

    hits = sum(1 for record in details if record["hit"])
    recall = _ratio(hits, len(details))
    metrics = {
        "total": len(details),
        "cases_passed": hits,
        "recall": recall,
        "k": k,
        "gate": {
            "recall_gte": THRESHOLDS["policy_recall_gte"],
            "passed": recall >= THRESHOLDS["policy_recall_gte"],
        },
    }
    return metrics, details


def _load_golden(path: pathlib.Path) -> dict:
    data = json.loads(path.read_text())
    missing = [name for name in SUITE_NAMES if not isinstance(data.get(name), list)]
    if missing:
        raise ValueError(f"golden file missing list suites: {', '.join(missing)}")
    short = [
        f"{name} ({len(data[name])} < {minimum})"
        for name, minimum in MIN_CASES.items()
        if len(data[name]) < minimum
    ]
    if short:
        raise ValueError(f"golden file below minimum case counts: {', '.join(short)}")
    return data


def _status(gate_passed: bool) -> str:
    return "pass" if gate_passed else "fail"


def _print_summary(result: dict) -> None:
    suites = result["summary"]["suites"]
    print("\nCopilot Business Scenario Benchmark")
    print("=" * 84)
    print(f"{'Suite':<20} {'Cases':>7} {'Passed':>7} {'Metric':>12} {'Gate':>8}")
    print("-" * 84)
    rows = (
        ("guardrails", "accuracy"),
        ("sizing", "accuracy"),
        ("budget_outfits", "invariant_violations"),
        ("policy_rag", "recall"),
    )
    for name, metric in rows:
        suite = suites[name]
        value = suite[metric]
        value_text = f"{value:>12d}" if isinstance(value, int) else f"{value:>12.4f}"
        print(
            f"{name:<20} {suite['total']:>7} {suite['cases_passed']:>7} "
            f"{value_text} {suite['status']:>8}"
        )
    print("=" * 84)
    if result["summary"]["failures"]:
        print("Failed cases: " + ", ".join(result["summary"]["failures"]))
    print(f"Overall: {'PASS' if result['summary']['passed'] else 'FAIL'}")


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Deterministic business-scenario evaluation for the Toko Marcell copilot."
    )
    parser.add_argument("--golden", default=DEFAULT_GOLDEN)
    parser.add_argument("--out", default=DEFAULT_OUT)
    parser.add_argument("--k", type=int, default=3, help="policy RAG top-k (default: 3)")
    parser.add_argument(
        "--db-url",
        default=None,
        help="Postgres URL; defaults to DATABASE_URL / api config",
    )
    args = parser.parse_args()

    golden_path = pathlib.Path(args.golden)
    try:
        golden = _load_golden(golden_path)
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        print(f"Could not load golden set {golden_path}: {exc}", file=sys.stderr)
        return 2

    guardrails, guardrail_details = _evaluate_guardrails(golden["guardrails"])
    sizing, sizing_details = _evaluate_sizing(golden["sizing"], args.db_url)
    outfits, outfit_details = _evaluate_outfits(golden["budget_outfits"], args.db_url)
    policy, policy_details = _evaluate_policy(golden["policy_rag"], args.db_url, args.k)

    for suite in (guardrails, sizing, outfits, policy):
        suite["status"] = _status(suite["gate"]["passed"])

    failures = [
        detail["id"]
        for detail in (*guardrail_details, *sizing_details, *outfit_details, *policy_details)
        if not detail.get("passed", detail.get("correct", detail.get("hit", False)))
    ]
    passed = all(
        suite["gate"]["passed"] for suite in (guardrails, sizing, outfits, policy)
    )

    result = {
        "timestamp": datetime.datetime.now(datetime.UTC).isoformat(),
        "git_commit": _git_commit(),
        "framework": "deterministic-business-scenarios",
        "guardrail_mode": "regex_only",
        "golden_file": args.golden,
        "query_count": sum(
            len(golden[name]) for name in SUITE_NAMES
        ),
        "summary": {
            "passed": passed,
            "failures": failures,
            "thresholds": THRESHOLDS,
            "suites": {
                "guardrails": guardrails,
                "sizing": sizing,
                "budget_outfits": outfits,
                "policy_rag": policy,
            },
        },
        "details": {
            "guardrails": guardrail_details,
            "sizing": sizing_details,
            "budget_outfits": outfit_details,
            "policy_rag": policy_details,
        },
    }

    out_path = pathlib.Path(args.out)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(result, indent=2, ensure_ascii=False) + "\n")

    _print_summary(result)
    print(f"Wrote {out_path}")
    return 0 if passed else 1


if __name__ == "__main__":
    sys.exit(main())
