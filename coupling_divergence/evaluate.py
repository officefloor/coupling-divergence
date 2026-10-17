"""Does the class-cumulated coupling-divergence outlier predict a textbook god class
BEFORE it forms? (Apples-to-apples: both signals are CLASS-level. The class-generation
outlier -- a file whose cumulated method gain is a modified-z outlier among files -- is
compared to the file crossing WMC >= 47, not a method signal against a class god.)

A file "becomes a god" the first sampled commit its WMC crosses 47 (Lanza-Marinescu size
threshold, DI-immune, language-neutral). The prediction fires the first sampled commit the
file is a class-generation outlier. The metric PREDICTS when the outlier appears at or
before the WMC crossing. We report, over every file that is a god at HEAD, how often the
class-generation signal led, by how many sampled steps, and the false-positive rate.

A file that is already a god at the FIRST sampled commit is left-censored: its formation
happened before the window, so neither signal can be seen to lead. Such files are reported
as `censored` and excluded from the lead statistics; counting them as "predicted at or
before" (both at step 0) would credit the signal with a tie it never had to win."""
from __future__ import annotations

from . import history
from .extract import WMC_GOD


def _first(pred, series):
    for s in series:
        if pred(s):
            return s["i"]
    return None


def evaluate_repo(repo: str, samples: int = 40, include_tests: bool = False) -> dict:
    series = history.walk(repo, samples, include_tests)
    if not series:
        return {"repo": repo, "error": "no history"}
    head = series[-1]
    head_gods = sorted(f for f, w in head["wmc"].items() if w >= WMC_GOD)

    per_god = []
    first_i = series[0]["i"]
    for gfile in head_gods:
        wmc_i = _first(lambda s: s["wmc"].get(gfile, 0) >= WMC_GOD, series)
        gen_i = _first(lambda s: gfile in s["class_outlier_files"], series)
        censored = wmc_i == first_i
        lead = (wmc_i - gen_i) if (wmc_i is not None and gen_i is not None
                                   and not censored) else None
        per_god.append({"file": gfile, "wmc_cross_i": wmc_i, "outlier_i": gen_i,
                        "censored": censored,
                        "lead_samples": lead, "head_wmc": head["wmc"].get(gfile, 0)})
    observed = [g for g in per_god if not g["censored"]]

    # files that ever held a class-generation outlier but are not gods at HEAD: FP view
    ever_outlier = {f for s in series for f in s["class_outlier_files"]}
    fp_files = sorted(f for f in ever_outlier if f not in set(head_gods))

    leads = [g["lead_samples"] for g in per_god if g["lead_samples"] is not None]
    predicted = [g for g in observed if g["outlier_i"] is not None
                 and g["wmc_cross_i"] is not None and g["outlier_i"] <= g["wmc_cross_i"]]
    led_strict = [g for g in predicted if g["outlier_i"] < g["wmc_cross_i"]]
    missed = [g for g in observed if g["outlier_i"] is None]
    return {
        "repo": repo,
        "samples": len(series),
        "head_gods": len(head_gods),
        "censored": len(per_god) - len(observed),
        "observed_gods": len(observed),
        "predicted_at_or_before": len(predicted),
        "led_strictly_before": len(led_strict),
        "mean_lead_samples": round(sum(leads) / len(leads), 2) if leads else None,
        "median_lead_samples": sorted(leads)[len(leads) // 2] if leads else None,
        "never_flagged": len(missed),
        "false_positive_files": len(fp_files),
        "per_god": per_god,
    }


def summarise(results: list[dict]) -> str:
    lines = ["# GodWatch lead-lag: does divergence predict the god before it forms?",
             "",
             "| repo | samples | HEAD gods | censored | predicted (<=) | led (<) | mean lead | "
             "never flagged | FP files |",
             "|---|--:|--:|--:|--:|--:|--:|--:|--:|"]
    tot_g = tot_p = tot_l = 0
    for r in results:
        if r.get("error"):
            lines.append(f"| {r['repo']} | ERROR: {r['error']} |")
            continue
        tot_g += r["observed_gods"]; tot_p += r["predicted_at_or_before"]; tot_l += r["led_strictly_before"]
        lines.append(f"| {r['repo'].split('/')[-1]} | {r['samples']} | {r['head_gods']} | "
                     f"{r['censored']} | {r['predicted_at_or_before']} | {r['led_strictly_before']} | "
                     f"{r['mean_lead_samples']} | {r['never_flagged']} | "
                     f"{r['false_positive_files']} |")
    if tot_g:
        lines += ["",
                  f"**Pooled:** of {tot_g} HEAD god files that formed inside the sampled "
                  f"window (gods already at the first sample are censored), the divergence "
                  f"outlier appeared "
                  f"at or before the WMC-47 crossing in {tot_p} "
                  f"({round(100*tot_p/tot_g)}%), strictly before in {tot_l} "
                  f"({round(100*tot_l/tot_g)}%)."]
    return "\n".join(lines)
