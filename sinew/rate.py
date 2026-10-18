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
human-rated subset. Rates through `claude -p` (Claude Code on the user's subscription); the
CLI version is recorded with every rating.
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
HUMAN_CSV = "human-ratings.csv"   # human ratings of the same blind dossiers, scored beside the models
# The qualification gate on the planted history: a rater must say "consolidate" for at least
# this share of the planted chains and at most this share of the planted decoys.
GATE = {"concern_min": 0.8, "decoy_max": 0.2}


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


def claude_version() -> str | None:
    """The Claude Code CLI's version string, recorded with every rating."""
    import subprocess
    try:
        r = subprocess.run(["claude", "--version"], capture_output=True, text=True, timeout=60)
    except (OSError, subprocess.TimeoutExpired):
        return None
    return r.stdout.strip() or None


def claude_caller(effort: str = DEFAULT_EFFORT, timeout: int = 900):
    """A `call(model, system, user) -> dict` over `claude -p` on the user's Claude
    subscription: structured JSON output and thinking at `effort`, with no tools, settings,
    MCP servers or session on disk, run from an empty directory so no CLAUDE.md or memory
    reaches the rater. API keys are removed from the environment so the subscription login
    is what serves the rating. The session id stands in for the request id."""
    import shutil
    import subprocess
    import tempfile
    if not shutil.which("claude"):
        raise SystemExit("`claude` (Claude Code) is not on PATH: install it and log in with "
                         "`claude` (or `claude setup-token` for CLAUDE_CODE_OAUTH_TOKEN)")
    version = claude_version()
    env = {k: v for k, v in os.environ.items()
           if k not in ("ANTHROPIC_API_KEY", "ANTHROPIC_AUTH_TOKEN")}
    schema = json.dumps(SCHEMA)

    def call(model: str, system: str, user: str) -> dict:
        cmd = ["claude", "-p", "--model", model, "--effort", effort,
               "--system-prompt", system, "--json-schema", schema, "--output-format", "json",
               "--tools", "", "--setting-sources", "", "--strict-mcp-config",
               "--no-session-persistence"]
        with tempfile.TemporaryDirectory(prefix="sinew-rate-") as cwd:
            try:
                p = subprocess.run(cmd, input=user, capture_output=True, text=True, cwd=cwd,
                                   env=env, timeout=timeout)
            except subprocess.TimeoutExpired:
                return {"error": f"timeout after {timeout}s", "cli": version}
        try:
            r = json.loads(p.stdout)
        except ValueError:
            return {"error": f"claude exit {p.returncode}: "
                             f"{(p.stderr or p.stdout).strip()[:500]}", "cli": version}
        sid = r.get("session_id")
        if r.get("is_error") or p.returncode:
            status = r.get("api_error_status")
            return {"error": f"{r.get('subtype')}" + (f" api {status}" if status else "")
                             + f": {str(r.get('result'))[:500]}",
                    "request_id": sid, "cli": version}
        if r.get("stop_reason") == "refusal":
            return {"error": "refusal", "request_id": sid, "cli": version}
        out = r.get("structured_output")
        text = json.dumps(out) if out is not None else (r.get("result") or "")
        used = r.get("modelUsage") or {}
        served = model if model in used else max(
            used, key=lambda m: used[m].get("outputTokens", 0), default=None)
        u = r.get("usage") or {}
        return {"text": text, "request_id": sid, "served_by": served, "cli": version,
                "stop_reason": r.get("stop_reason"),
                "usage": {"input": (u.get("input_tokens", 0)
                                    + u.get("cache_creation_input_tokens", 0)
                                    + u.get("cache_read_input_tokens", 0)),
                          "output": u.get("output_tokens", 0)}}
    return call


def chance_pass(n_concern: int, n_decoy: int, gate: dict = GATE) -> float:
    """The probability that a rater deciding by coin flip passes the gate: P(Bin(n_c, 1/2)
    >= ceil(concern_min * n_c)) * P(Bin(n_d, 1/2) <= floor(decoy_max * n_d))."""
    import math
    from math import comb
    if not n_concern or not n_decoy:
        return 1.0
    kc = math.ceil(gate["concern_min"] * n_concern - 1e-9)
    kd = math.floor(gate["decoy_max"] * n_decoy + 1e-9)
    pc = sum(comb(n_concern, i) for i in range(kc, n_concern + 1)) / 2 ** n_concern
    pd = sum(comb(n_decoy, i) for i in range(0, kd + 1)) / 2 ** n_decoy
    return pc * pd


def human_template(dossier_dir: str) -> str:
    """Write <dossier_dir>/human-ratings.csv with one row per dossier for a human rater to
    fill in (decision: consolidate | leave; confidence 1-5; rater: an initial or id). Several
    raters append rows under their own id. Scored by `sinew rate --score` as raters named
    human:<id>."""
    import csv
    path = os.path.join(dossier_dir, HUMAN_CSV)
    if os.path.exists(path):
        return path
    with open(path, "w", newline="") as fh:
        w = csv.writer(fh)
        w.writerow(["file", "rater", "decision", "confidence"])
        for name in dossier_files(dossier_dir):
            w.writerow([name, "", "", ""])
    return path


def _human_records(dossier_dir: str, prefix: str = "") -> list:
    import csv
    path = os.path.join(dossier_dir, HUMAN_CSV)
    if not os.path.exists(path):
        return []
    recs = []
    with open(path, newline="") as fh:
        for row in csv.DictReader(fh):
            d = (row.get("decision") or "").strip().lower()
            if d not in ("consolidate", "leave"):
                continue
            try:
                conf = int(row.get("confidence") or 0) or None
            except ValueError:
                conf = None
            recs.append({"file": prefix + row["file"], "model": f"human:{row.get('rater') or '?'}",
                         "run": 1, "decision": d, "confidence": conf, "cause": "cannot tell"})
    return recs


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
                if res.get("cli"):
                    rec["cli"] = res["cli"]
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
    recs += _human_records(dossier_dir, prefix)
    return key, recs


def _rater_id(r: dict) -> str:
    """The rater a record is scored under: the model that actually served it. A rating served
    by another model than the one requested is scored as its own rater (`requested -> served`),
    never mixed into the requested model's rates."""
    served = r.get("served_by")
    if served and served != r["model"]:
        return f"{r['model']} -> {served}"
    return r["model"]


def _scorecard(key: dict, recs: list) -> dict:
    by: dict = defaultdict(list)                  # (file, rater) -> decisions over runs
    errors = Counter()
    conf: dict = defaultdict(list)
    causes: dict = defaultdict(Counter)
    for r in recs:
        if r.get("error"):
            errors[r["model"]] += 1
            continue
        rid = _rater_id(r)
        by[(r["file"], rid)].append(r["decision"])
        truth = key.get(r["file"], {}).get("truth")
        conf[(rid, truth)].append(r["confidence"])
        causes[(rid, truth)][r["cause"]] += 1
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
        rc = row.get("concern", {}).get("rate")
        rd = row.get("decoy", {}).get("rate")
        rk = row.get("contract", {}).get("rate")
        # PRIMARY: concern vs contract tests the span rule (both heavy, both threaded);
        # concern vs decoy tests whether threading itself is seen (secondary)
        row["concern_minus_contract"] = (round(rc - rk, 3)
                                         if rc is not None and rk is not None else None)
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
        log(f"  {m:22} {'  '.join(parts)}  concern-contract={row['concern_minus_contract']}  "
            f"concern-decoy={row['concern_minus_decoy']}  "
            f"self-consistency={row['self_consistency']}  errors={row['errors']}")
    for k, v in card["agreement"].items():
        log(f"  {k}: kappa={v['kappa']} (n={v['n']})  [agreement between raters; two models of "
            "one vendor are not independent raters]")


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
    """Build the planted history (sinew/planted.py), mine it, write its blind dossiers (the
    planted chains, contracts and matched decoys), have the raters rate them, and score.
    Passes when every model rates at least GATE[concern_min] of the chains and at most
    GATE[decoy_max] of the decoys "consolidate", with no errors. The probability that a
    coin-flip rater passes the same gate is computed and reported. Returns the scorecard
    with a `passed` flag and the raters used."""
    from . import analyze as A, mine as mine_mod, planted, sinew_study
    models = models or DEFAULT_MODELS
    runs = runs or DEFAULT_RUNS
    effort = effort or DEFAULT_EFFORT
    import shutil
    os.makedirs(work_dir, exist_ok=True)
    shutil.rmtree(os.path.join(work_dir, "repo"), ignore_errors=True)   # a fresh plant every run
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
    n_c, n_d = card["truths"].get("concern", 0), card["truths"].get("decoy", 0)

    def ok(row):
        rc = (row.get("concern") or {}).get("rate")
        rd = (row.get("decoy") or {}).get("rate")
        return (rc is not None and rd is not None and rc >= GATE["concern_min"]
                and rd <= GATE["decoy_max"] and row["errors"] == 0)
    passed = (bool(card["per_model"]) and set(card["per_model"]) == set(models)
              and all(ok(row) for row in card["per_model"].values()))
    chance = chance_pass(n_c, n_d)
    card.update({"passed": passed, "gate": GATE, "chance_pass": round(chance, 4),
                 "models": models, "runs": runs, "effort": effort,
                 "work_dir": os.path.abspath(work_dir)})
    gate_txt = (f"gate: >= {GATE['concern_min']:.0%} of {n_c} planted chains and <= "
                f"{GATE['decoy_max']:.0%} of {n_d} matched decoys rated 'consolidate'; a "
                f"coin-flip rater passes with probability {chance:.3f}")
    if passed:
        log(f"\nRATER CHECK PASSED ({gate_txt}). Read the justifications in "
            f"{os.path.join(out, 'ratings.jsonl')}.")
    else:
        log(f"\nRATER CHECK FAILED ({gate_txt}). Try another model, more runs, or higher "
            f"effort; the ratings and justifications are in {os.path.join(out, 'ratings.jsonl')}.")
    return card
