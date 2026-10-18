"""AI rating of blind dossiers, with every decision and justification on record.

The span verdict (a heavy cluster of four or more methods is sprawl worth refactoring; a
smaller one is a contract to leave) is a rule, not a measurement. `sinew dossier --blind`
writes the concern clusters, the small clusters and decoys (methods that co-change but
thread nothing) under shuffled numbers with the truth in a key file. This module has a model
read each dossier and answer one neutral question -- would you consolidate the data these
methods pass around -- recording the model, the prompt (by hash), the raw response, the
structured decision and the justification, so anyone can re-run or audit the ratings. Scoring
then unblinds: hit rates by truth and by model, agreement between models (Cohen's kappa) and
each model's self-consistency across repeated runs.

Design choices that keep the rating honest:
  * the prompt never mentions the method's vocabulary (sinew, span, thread, forward, verdict),
    and the blind dossiers carry no instrument metrics, so the model is not pattern-matching
    the theory back to us;
  * the decision is a structured field asked for BEFORE the justification and is the only
    thing scored, so a long or eloquent answer earns nothing;
  * several model families can rate the same dossiers and are treated as independent raters;
    repeated runs of one model measure its self-consistency, not independence;
  * rating and scoring are separate commands, so a dossier set can be rated before anyone
    looks at the key.

An AI rater is an instrument, not ground truth: the paper should report its agreement with a
human-rated subset. Requires the optional dependency `anthropic` (pinned in deps.OPTIONAL).
"""
from __future__ import annotations

import datetime
import glob
import hashlib
import json
import os
from collections import Counter, defaultdict

DEFAULT_MODELS = ["claude-opus-5-5"]
DEFAULT_EFFORT = "high"
DEFAULT_RUNS = 1
CONFIG = "rater.json"          # the chosen raters, written by `sinew rate --check --save`


def load_config(path: str | None = None) -> dict | None:
    """The rater configuration ({models, runs, effort, verified}) if one is saved."""
    path = path or CONFIG
    if not os.path.exists(path):
        return None
    with open(path) as fh:
        return json.load(fh)


def save_config(models: list, runs: int, effort: str, verified: dict,
                path: str | None = None) -> str:
    path = path or CONFIG
    with open(path, "w") as fh:
        json.dump({"models": models, "runs": runs, "effort": effort, "verified": verified},
                  fh, indent=2)
        fh.write("\n")
    return path

# Words that would leak the method into the rater's prompt. Checked by the tests against
# SYSTEM and QUESTION, and by `rate` against every dossier it sends.
LEAK_WORDS = ("sinew", "span", "thread", "forward", "verdict", "concern", "decoy", "contract",
              "conduit", "heavy", "co-change", "cochange")

SYSTEM = """You are an experienced software engineer asked for an independent opinion on a \
small group of methods taken from one codebase. You will be shown each method's source, its \
location, and a list of commits in which two or more of these methods were edited together. \
Judge only what you can see. Do not assume the group was selected for any particular reason."""

QUESTION = """Look at how these methods relate to each other and at the data they accept and \
hand on to one another.

Question: if you maintained this codebase, would you consolidate the data these methods pass \
around -- for example into a single parameter object, context object, or shared abstraction -- \
and refactor the group accordingly? Answer "consolidate" if you would make that change a \
priority, or "leave" if the group is fine as it is or the change would not be worth it.

Give your decision first, then your confidence (1 = a guess, 5 = certain), the single best \
description of why these methods change together, the names of the data items (parameters or \
values) you see being passed from one of these methods to another, and a justification of at \
most 150 words that cites specific methods."""

CAUSES = ["shared data that should be one object", "an intrinsic contract (they must change together)",
          "copy-paste duplication", "a cross-cutting concern handled by hand",
          "unrelated changes that happened to land together", "cannot tell"]

SCHEMA = {
    "type": "object",
    "properties": {
        "decision": {"type": "string", "enum": ["consolidate", "leave"]},
        "confidence": {"type": "integer", "enum": [1, 2, 3, 4, 5]},
        "cause": {"type": "string", "enum": CAUSES},
        "data_passed": {"type": "array", "items": {"type": "string"}},
        "justification": {"type": "string"},
    },
    "required": ["decision", "confidence", "cause", "data_passed", "justification"],
    "additionalProperties": False,
}


def prompt_hash() -> str:
    h = hashlib.sha256()
    h.update(SYSTEM.encode())
    h.update(QUESTION.encode())
    h.update(json.dumps(SCHEMA, sort_keys=True).encode())
    return h.hexdigest()[:16]


def leaks(text: str) -> list:
    """Leak words in a prompt text."""
    low = text.lower()
    return [w for w in LEAK_WORDS if w in low]


def dossier_leaks(text: str) -> list:
    """Leak words in the parts of a dossier the generator wrote -- its headings and metadata
    bullets -- not in the members' source or the commit messages, which say what they say."""
    found: set = set()
    fence = False
    for line in text.split("\n"):
        if line.startswith("```"):
            fence = not fence
            continue
        if fence or line.startswith("- `") or line.startswith("parameters:"):
            continue
        if line.startswith("#") or line.startswith("- **"):
            found.update(leaks(line))
    return sorted(found)


def claude_caller(effort: str = DEFAULT_EFFORT):
    """A `call(model, system, user) -> dict` over the Anthropic SDK: structured JSON output,
    adaptive thinking at `effort`, and the server-side default fallback so a safety decline
    is re-run on Anthropic's recommended model instead of becoming a missing rating."""
    from . import deps
    deps.validate_optional("anthropic")
    import anthropic

    client = anthropic.Anthropic()

    def call(model: str, system: str, user: str) -> dict:
        try:
            r = client.beta.messages.create(
                model=model, max_tokens=4096, system=system,
                messages=[{"role": "user", "content": user}],
                thinking={"type": "adaptive"},
                output_config={"effort": effort,
                               "format": {"type": "json_schema", "schema": SCHEMA}},
                betas=["server-side-fallback-2026-07-01"], fallbacks="default",
            )
        except anthropic.RateLimitError as e:
            return {"error": f"rate limited: {e.message}"}
        except anthropic.APIStatusError as e:
            return {"error": f"api {e.status_code}: {e.message}"}
        except anthropic.APIConnectionError as e:
            return {"error": f"connection: {e}"}
        if r.stop_reason == "refusal":
            cat = r.stop_details.category if r.stop_details else None
            return {"error": f"refusal ({cat})", "request_id": r._request_id}
        text = next((b.text for b in r.content if b.type == "text"), "")
        return {"text": text, "request_id": r._request_id, "served_by": r.model,
                "stop_reason": r.stop_reason,
                "usage": {"input": r.usage.input_tokens, "output": r.usage.output_tokens}}
    return call


def _done(path: str) -> set:
    done = set()
    if os.path.exists(path):
        with open(path) as fh:
            for line in fh:
                if line.strip():
                    rec = json.loads(line)
                    if not rec.get("error"):
                        done.add((rec["file"], rec["model"], rec["run"]))
    return done


def dossier_files(dossier_dir: str) -> list:
    return sorted(os.path.basename(p) for p in glob.glob(os.path.join(dossier_dir, "*.md")))


def rate(dossier_dir: str, models: list | None = None, runs: int | None = None, call=None,
         effort: str | None = None, log=print, config: str | None = None) -> str:
    """Rate every dossier in `dossier_dir` with every model, `runs` times each, appending one
    record per rating to <dossier_dir>/ratings.jsonl. Resumable: ratings already on record
    are skipped. Returns the log path. A dossier whose text contains a leak word is refused
    (write it with `sinew dossier --blind`)."""
    cfg = load_config(config) or {}
    models = models or cfg.get("models") or DEFAULT_MODELS
    runs = runs or cfg.get("runs") or DEFAULT_RUNS
    effort = effort or cfg.get("effort") or DEFAULT_EFFORT
    call = call or claude_caller(effort)
    out = os.path.join(dossier_dir, "ratings.jsonl")
    done = _done(out)
    files = dossier_files(dossier_dir)
    if not files:
        raise SystemExit(f"no dossiers (*.md) in {dossier_dir}")
    ph = prompt_hash()
    n_new = 0
    for name in files:
        with open(os.path.join(dossier_dir, name)) as fh:
            text = fh.read()
        bad = dossier_leaks(text)
        if bad:
            raise SystemExit(f"{name} contains {bad}: not a blind dossier. Write dossiers with "
                             f"`sinew dossier --blind` before rating.")
        user = text + "\n\n---\n\n" + QUESTION
        for model in models:
            for run in range(1, runs + 1):
                if (name, model, run) in done:
                    continue
                res = call(model, SYSTEM, user)
                rec = {"file": name, "model": model, "run": run, "effort": effort,
                       "prompt_sha256_16": ph,
                       "rated_utc": datetime.datetime.now(datetime.timezone.utc)
                       .strftime("%Y-%m-%dT%H:%M:%SZ")}
                if res.get("error"):
                    rec["error"] = res["error"]
                    rec["request_id"] = res.get("request_id")
                else:
                    try:
                        data = json.loads(res["text"])
                    except ValueError:
                        data = None
                    rec.update({"request_id": res.get("request_id"),
                                "served_by": res.get("served_by"), "usage": res.get("usage"),
                                "response": res["text"]})
                    if not data or data.get("decision") not in ("consolidate", "leave"):
                        rec["error"] = "unparseable response"
                    else:
                        rec.update({"decision": data["decision"],
                                    "confidence": data.get("confidence"),
                                    "cause": data.get("cause"),
                                    "data_passed": data.get("data_passed"),
                                    "justification": data.get("justification")})
                with open(out, "a") as fh:
                    fh.write(json.dumps(rec) + "\n")
                n_new += 1
                log(f"  {name} {model} run {run}: "
                    + (rec.get("decision") or f"ERROR {rec.get('error')}"))
    log(f"{n_new} new ratings -> {out}")
    return out


# ---------------------------------------------------------------------------
# scoring: unblind, hit rates, agreement
# ---------------------------------------------------------------------------
def kappa(a: list, b: list):
    """Cohen's kappa between two equal-length lists of labels; None if undefined."""
    n = len(a)
    if n == 0:
        return None
    po = sum(1 for x, y in zip(a, b) if x == y) / n
    labels = set(a) | set(b)
    pe = sum((a.count(l) / n) * (b.count(l) / n) for l in labels)
    if pe >= 1.0:
        return None
    return round((po - pe) / (1 - pe), 3)


def _majority(decisions: list) -> str:
    c = Counter(decisions)
    top = c.most_common()
    if len(top) > 1 and top[0][1] == top[1][1]:
        return "tie"
    return top[0][0]


def _load_dir(dossier_dir: str, prefix: str = ""):
    """(key, rating records) of one dossier dir, file names prefixed to keep dirs apart."""
    key: dict = {}
    for kp in glob.glob(os.path.join(dossier_dir, "*.key.json")):
        with open(kp) as fh:
            for k in json.load(fh):
                key[prefix + k["file"]] = k
    recs = []
    ratings_path = os.path.join(dossier_dir, "ratings.jsonl")
    if os.path.exists(ratings_path):
        with open(ratings_path) as fh:
            for line in fh:
                if line.strip():
                    r = json.loads(line)
                    r["file"] = prefix + r["file"]
                    recs.append(r)
    return key, recs


def _scorecard(key: dict, recs: list) -> dict:
    by: dict = defaultdict(list)                  # (file, model) -> decisions over runs
    errors = Counter()
    conf: dict = defaultdict(list)
    causes: dict = defaultdict(Counter)
    for r in recs:
        if r.get("error"):
            errors[r["model"]] += 1
            continue
        by[(r["file"], r["model"])].append(r["decision"])
        truth = key.get(r["file"], {}).get("truth")
        conf[(r["model"], truth)].append(r["confidence"])
        causes[(r["model"], truth)][r["cause"]] += 1
    models = sorted({m for _, m in by})
    truths = sorted({k["truth"] for k in key.values()})
    maj = {(f, m): _majority(d) for (f, m), d in by.items()}
    per = {}
    for m in models:
        row = {}
        for t in truths:
            files = [f for f, k in key.items() if k["truth"] == t and (f, m) in maj]
            yes = sum(1 for f in files if maj[(f, m)] == "consolidate")
            cs = conf[(m, t)]
            row[t] = {"n": len(files), "consolidate": yes,
                      "rate": round(yes / len(files), 3) if files else None,
                      "mean_confidence": round(sum(cs) / len(cs), 2) if cs else None,
                      "causes": dict(causes[(m, t)].most_common())}
        multi = [d for (f, mm), d in by.items() if mm == m and len(d) > 1]
        row["self_consistency"] = (round(sum(1 for d in multi if len(set(d)) == 1) / len(multi), 3)
                                   if multi else None)
        row["errors"] = errors.get(m, 0)
        rc, rd = row.get("concern", {}).get("rate"), row.get("decoy", {}).get("rate")
        row["concern_minus_decoy"] = round(rc - rd, 3) if rc is not None and rd is not None else None
        per[m] = row
    agreement = {}
    for i, a in enumerate(models):
        for b in models[i + 1:]:
            files = [f for f in key if (f, a) in maj and (f, b) in maj
                     and maj[(f, a)] != "tie" and maj[(f, b)] != "tie"]
            agreement[f"{a} vs {b}"] = {"n": len(files),
                                        "kappa": kappa([maj[(f, a)] for f in files],
                                                       [maj[(f, b)] for f in files])}
    return {"dossiers": len(key), "rated": len({f for f, _ in by}), "models": models,
            "truths": {t: sum(1 for k in key.values() if k["truth"] == t) for t in truths},
            "per_model": per, "agreement": agreement, "prompt_sha256_16": prompt_hash()}


def _print_card(card: dict, log) -> None:
    truths = sorted(card["truths"])
    log(f"\nSCORECARD  {card['dossiers']} dossiers ({card['truths']}), {card['rated']} rated")
    for m, row in card["per_model"].items():
        parts = [f"{t}: {row[t]['consolidate']}/{row[t]['n']}" for t in truths]
        log(f"  {m:22} {'  '.join(parts)}  concern-decoy={row['concern_minus_decoy']}  "
            f"self-consistency={row['self_consistency']}  errors={row['errors']}")
    for k, v in card["agreement"].items():
        log(f"  {k}: kappa={v['kappa']} (n={v['n']})")


def score(dossier_dir: str, log=print) -> dict:
    """Join ratings.jsonl to the key files and compute: per model and truth, the share rated
    "consolidate"; per model, self-consistency across runs; between models, Cohen's kappa on
    the majority decision per dossier. Written to <dossier_dir>/scorecard.json."""
    key, recs = _load_dir(dossier_dir)
    if not key:
        raise SystemExit(f"no *.key.json in {dossier_dir}: the dossiers were not written blind")
    if not recs:
        raise SystemExit(f"no ratings.jsonl in {dossier_dir}: run `sinew rate` first")
    card = _scorecard(key, recs)
    with open(os.path.join(dossier_dir, "scorecard.json"), "w") as fh:
        json.dump(card, fh, indent=2)
    _print_card(card, log)
    return card


def score_many(dirs: list, out_path: str, log=print) -> dict:
    """One pooled scorecard over several projects' dossier dirs."""
    key: dict = {}
    recs: list = []
    for d in dirs:
        k, r = _load_dir(d, os.path.basename(d.rstrip("/")) + "/")
        key.update(k)
        recs += r
    card = _scorecard(key, recs) if key else {"dossiers": 0, "rated": 0, "models": [],
                                               "truths": {}, "per_model": {}, "agreement": {}}
    card["projects"] = [os.path.basename(d.rstrip("/")) for d in dirs]
    os.makedirs(os.path.dirname(os.path.abspath(out_path)), exist_ok=True)
    with open(out_path, "w") as fh:
        json.dump(card, fh, indent=2)
    if key:
        _print_card(card, log)
    return card


# ---------------------------------------------------------------------------
# the check: rate the planted history's blind dossiers and demand discrimination
# ---------------------------------------------------------------------------
def check(work_dir: str, models: list | None = None, runs: int | None = None,
          effort: str | None = None, call=None, log=print) -> dict:
    """Build the planted history (sinew/planted.py), mine it, write its blind dossiers, have
    the raters rate them, and score. Passes when every model rates the planted chain
    "consolidate" more often than the decoy and no rating errored. Returns the scorecard
    with a `passed` flag and the raters used."""
    from . import analyze as A, mine as mine_mod, planted, sinew_study
    models = models or DEFAULT_MODELS
    runs = runs or DEFAULT_RUNS
    effort = effort or DEFAULT_EFFORT
    os.makedirs(work_dir, exist_ok=True)
    repo, _ = planted.plant(os.path.join(work_dir, "repo"))
    base = os.path.join(work_dir, "mine")
    for suffix in (".methods.jsonl", ".commits.jsonl"):
        if os.path.exists(base + suffix):
            os.remove(base + suffix)
    mine_mod.mine(repo.root, base)
    rows, commits = A.head_rows(base), A._commits(base)
    s = A.sinew(base, rows=rows, commits=commits, null_samples=0, bootstrap=0)
    out = os.path.join(work_dir, "blind")
    for old in glob.glob(os.path.join(out, "*")):
        os.remove(old)
    n = sinew_study.dossier(base, out, repo=repo.root, blind=True, s=s, rows=rows,
                            commits=commits)
    log(f"planted history: {len(rows)} methods, {len(commits)} commits, {n} blind dossiers "
        f"-> {out}")
    rate(out, models, runs, call=call, effort=effort, log=log)
    card = score(out, log=log)
    passed = bool(card["per_model"]) and all(
        (row.get("concern_minus_decoy") or 0) > 0 and row["errors"] == 0
        for row in card["per_model"].values()) and set(card["per_model"]) == set(models)
    card.update({"passed": passed, "models": models, "runs": runs, "effort": effort,
                 "work_dir": os.path.abspath(work_dir)})
    if passed:
        log("\nRATER CHECK PASSED: each model told the planted chain from the decoy. Read the "
            f"justifications in {os.path.join(out, 'ratings.jsonl')}.")
    else:
        log("\nRATER CHECK FAILED: every model must rate the planted chain 'consolidate' more "
            "often than the decoy, with no errors. Try another model, more runs, or higher "
            "effort; the ratings and justifications are in "
            f"{os.path.join(out, 'ratings.jsonl')}.")
    return card
