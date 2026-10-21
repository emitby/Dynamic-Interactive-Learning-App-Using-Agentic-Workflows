"""Eval runner.

    run.ps1 eval                     score the latest lesson build + the red-team suite (free)
    run.ps1 eval --run runs\\<id>     score a specific build
    run.ps1 eval --live              build every lesson in LIVE_MATRIX with the real agents (costs
                                     API credits, about $0.20 per lesson), score each, report pass rates

Results are written to ``evals/results/<timestamp>.json``. The exit code is 1 if any
gate or red-team case fails.
"""

import argparse
import sys
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime

from evals.redteam import run_cases
from evals.scorers import score_run
from studysprint.paths import PROJECT_DIR, RUNS_DIR, confine, write_json

RESULTS_DIR = PROJECT_DIR / "evals" / "results"

#: Live eval matrix: subjects across every grade band, from kindergarten to college,
#: including a broad subject the Intake Agent must narrow and one it must decline.
LIVE_MATRIX = [
    ("shapes", "K", "ok"),
    ("the water cycle", "2", "ok"),
    ("fractions", "4", "ok"),
    ("photosynthesis", "5", "ok"),
    ("ancient Egypt", "6", "ok"),
    ("science", "7", "ok"),              # too broad: Intake must narrow it to a specific topic
    ("World War II", "10", "ok"),
    ("derivatives", "C", "ok"),
    ("asdfghjkl", "3", "declined"),      # not a subject: Intake must decline kindly
]

#: Builds run in parallel, matching the web app's concurrent-build cap.
LIVE_WORKERS = 3


def latest_run():
    """The most recent run directory with a manifest, or ``None``."""
    runs = sorted(p for p in confine(RUNS_DIR).glob("*") if (p / "manifest.json").exists()) if RUNS_DIR.exists() else []
    return runs[-1] if runs else None


def build_live(case):
    """Build one matrix lesson with the real agents.

    Args:
        case: ``(subject, grade, expected)`` from :data:`LIVE_MATRIX`.

    Returns:
        ``(case, run_dir, outcome)`` where ``outcome`` is ``ok``, ``declined`` or ``failed: ...``.
    """
    from studysprint.harness import Declined, build_lesson, new_context
    subject, grade, _ = case
    ctx = new_context(subject, grade)
    try:
        build_lesson(ctx)
        return case, ctx.run_dir, "ok"
    except Declined:
        return case, ctx.run_dir, "declined"
    except Exception as exc:
        return case, ctx.run_dir, f"failed: {type(exc).__name__}: {exc}"


def print_scores(name, scores):
    """Print one run's scores, with the failing details for any failure."""
    print(f"\n{name}")
    for s in scores:
        print(f"  {'PASS' if s.passed else 'FAIL'}  {s.name:<20} {s.score:6.1%}")
        if not s.passed:
            print(f"        {s.details}")


def main(argv=None):
    """Parse arguments, run the requested evals, write the report and return the exit code."""
    ap = argparse.ArgumentParser(prog="evals")
    ap.add_argument("--run", help="score this run directory instead of the latest")
    ap.add_argument("--live", action="store_true", help="build the live matrix with real agents (costs credits)")
    args = ap.parse_args(argv)

    report = {"timestamp": datetime.now().isoformat(timespec="seconds"), "live": args.live, "runs": {}}
    all_pass = True

    if args.live:
        with ThreadPoolExecutor(max_workers=LIVE_WORKERS) as pool:
            outcomes = list(pool.map(build_live, LIVE_MATRIX))
        report["matrix"] = []
        for (subject, grade, expected), run_dir, outcome in outcomes:
            as_expected = outcome == expected
            all_pass &= as_expected
            row = {"subject": subject, "grade": grade, "expected": expected, "outcome": outcome,
                   "run": run_dir.name, "as_expected": as_expected}
            if outcome == "ok":
                scores = score_run(run_dir)
                report["runs"][run_dir.name] = [s.to_dict() for s in scores]
                row["scores_passed"] = all(s.passed for s in scores)
                all_pass &= row["scores_passed"]
                print_scores(f"{subject!r} grade {grade} -> {run_dir.name}", scores)
            else:
                print(f"\n{subject!r} grade {grade}: {outcome} (expected {expected})")
            report["matrix"].append(row)
        names = [s["name"] for v in report["runs"].values() for s in v]
        report["pass_rates"] = {n: sum(next(s for s in v if s["name"] == n)["passed"] for v in report["runs"].values())
                                / len(report["runs"]) for n in dict.fromkeys(names)}
        print("\nPass rates across live lessons:")
        for n, rate in report["pass_rates"].items():
            print(f"  {n:<20} {rate:.0%}")
        ok = sum(r["as_expected"] for r in report["matrix"])
        print(f"Matrix outcomes as expected: {ok}/{len(report['matrix'])}")
    else:
        target = confine(args.run) if args.run else latest_run()
        if target is None:
            print("No lesson builds found. Run: .\\run.ps1 build \"volcanoes\" --grade 4")
        elif (target / "lesson.json").exists():
            scores = score_run(target)
            report["runs"][target.name] = [s.to_dict() for s in scores]
            all_pass &= all(s.passed for s in scores)
            print_scores(f"Run {target.name}", scores)
        else:
            print(f"Run {target.name} has no lesson.json (declined or failed); see its manifest.json.")

    red = run_cases()
    report["redteam"] = red
    red_pass = sum(c["passed"] for c in red)
    all_pass &= red_pass == len(red)
    print(f"\nGuardrail red-team: {red_pass}/{len(red)} cases handled correctly")
    for c in red:
        if not c["passed"]:
            print(f"  FAIL {c['case']}: expected {c['expected']}, got {c['got']}")

    out = write_json(RESULTS_DIR / f"{datetime.now():%Y%m%d-%H%M%S}.json", report)
    print(f"\nReport: {out}")
    print("ALL EVALS PASSED" if all_pass else "SOME EVALS FAILED")
    return 0 if all_pass else 1


if __name__ == "__main__":
    sys.exit(main())
