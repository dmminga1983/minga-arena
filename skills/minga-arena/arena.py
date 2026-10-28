#!/usr/bin/env python3
"""arena.py - the tournament state machine behind minga-arena. Standard library only.

    python3 arena.py plan [--agents N | --quick | --full] [--budget B]   # size and cost, writes nothing
    python3 arena.py init --task-file T [--agents N] [--seed S] [--domain D]
                          [--budget B] [--wave W] [--baseline-file F]
    python3 arena.py next                  # what to do now, with the exact command
    python3 arena.py prompts <phase>       # write the briefs, list the jobs in waves
    python3 arena.py check <phase>         # jobs whose output is still missing
    python3 arena.py collect               # read the judges' verdicts for this round
    python3 arena.py advance               # eliminate losers, pair the survivors
    python3 arena.py status                # alive and out, per round
    python3 arena.py winner                # the champion, what it survived, the rounds
    python3 arena.py record <match> <agent> --reason "..."   # fix bookkeeping by hand
    python3 arena.py domains               # list the domain packs

State lives in .arena/<run>/arena.json under the current directory; .arena/LATEST points at
the run. Phases: spawn, then per round attack -> defend -> judge -> collect -> advance, then
final (only with a baseline), then done.
"""
import argparse
import itertools
import json
import os
import random
import re
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = ".arena"

DEFAULT_AGENTS = 16
QUICK_AGENTS = 8
FULL_AGENTS = 100
CONFIRM_ABOVE = 32
DEFAULT_WAVE = 10

# Which model plays which role, by budget. The judge decides who goes out, so it is the
# role that gets the strongest model; attacks are cheap to make and cheap to check.
BUDGETS = {
    "economy":  {"competitor": "haiku",  "attacker": "haiku",  "defender": "haiku",
                 "judge": "sonnet", "final": "sonnet"},
    "balanced": {"competitor": "sonnet", "attacker": "haiku",  "defender": "sonnet",
                 "judge": "sonnet", "final": "sonnet"},
    "max":      {"competitor": "sonnet", "attacker": "sonnet", "defender": "sonnet",
                 "judge": "opus",   "final": "opus"},
}
DEFAULT_BUDGET = "balanced"


class ArenaError(Exception):
    pass


# ---------------------------------------------------------------- data

def load_json(path):
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def load_cards():
    return load_json(os.path.join(HERE, "cards.json"))


def domain_names():
    d = os.path.join(HERE, "domains")
    return sorted(f[:-5] for f in os.listdir(d) if f.endswith(".json"))


def load_domain(name):
    path = os.path.join(HERE, "domains", name + ".json")
    if not os.path.exists(path):
        raise ArenaError("unknown domain %r. Known: %s" % (name, ", ".join(domain_names())))
    d = load_json(path)
    total = sum(c["weight"] for c in d["criteria"])
    if total != 100:
        raise ArenaError("domain %s: weights add up to %d, not 100" % (name, total))
    return d


def load_template(name):
    with open(os.path.join(HERE, "templates", name + ".txt"), encoding="utf-8") as f:
        return f.read()


def fill(template, values):
    # Only known keys are replaced, so placeholders meant for the agents survive.
    def rep(m):
        k = m.group(1)
        return str(values[k]) if k in values else m.group(0)
    return re.sub(r"\{\{(\w+)\}\}", rep, template)


# ---------------------------------------------------------------- dealing

def deal(n, seed, cards, extra_priority=()):
    """n distinct cards, parts spread as evenly as possible, pairs of parts not repeated
    while that is still possible."""
    th, wk = cards["thinking"], cards["working"]
    pr = list(cards["priority"]) + list(extra_priority)
    combos = list(itertools.product(range(len(th)), range(len(wk)), range(len(pr))))
    if n > len(combos):
        raise ArenaError("only %d distinct cards exist, asked for %d" % (len(combos), n))
    rng = random.Random(seed)
    rng.shuffle(combos)
    use = [[0] * len(th), [0] * len(wk), [0] * len(pr)]
    pairs = set()
    out = []
    taken = set()
    for _ in range(n):
        best, best_key = None, None
        for c in combos:
            if c in taken:
                continue
            p = {("tw", c[0], c[1]), ("tp", c[0], c[2]), ("wp", c[1], c[2])}
            key = (use[0][c[0]] + use[1][c[1]] + use[2][c[2]], len(p & pairs))
            if best_key is None or key < best_key:
                best, best_key = c, key
                if key == (min(use[0]) + min(use[1]) + min(use[2]), 0):
                    break
        taken.add(best)
        for i in range(3):
            use[i][best[i]] += 1
        pairs |= {("tw", best[0], best[1]), ("tp", best[0], best[2]), ("wp", best[1], best[2])}
        out.append({"thinking": th[best[0]], "working": wk[best[1]], "priority": pr[best[2]]})
    return out


def agent_ids(n):
    width = max(3, len(str(n)))
    return ["a%0*d" % (width, i + 1) for i in range(n)]


# ---------------------------------------------------------------- sizing

def bracket_sizes(n):
    sizes = [n]
    while sizes[-1] > 1:
        sizes.append((sizes[-1] + 1) // 2)
    return sizes


def plan_rows(n, wave=DEFAULT_WAVE):
    def waves(jobs):
        return (jobs + wave - 1) // wave
    rows = [{"round": "spawn", "alive": n, "matches": "-", "bye": "-", "calls": n,
             "waves": waves(n)}]
    for i, alive in enumerate(bracket_sizes(n)[:-1]):
        m = alive // 2
        rows.append({"round": i + 1, "alive": alive, "matches": m,
                     "bye": "yes" if alive % 2 else "-", "calls": 5 * m,
                     "waves": waves(2 * m) * 2 + waves(m)})
    return rows


def plan_totals(n, wave=DEFAULT_WAVE, budget=DEFAULT_BUDGET, baseline=False):
    rows = plan_rows(n, wave)
    by_model = {}
    roles = BUDGETS[budget]
    by_model[roles["competitor"]] = by_model.get(roles["competitor"], 0) + n
    for r in rows[1:]:
        m = r["matches"]
        for role, k in (("attacker", 2 * m), ("defender", 2 * m), ("judge", m)):
            by_model[roles[role]] = by_model.get(roles[role], 0) + k
    if baseline:
        by_model[roles["final"]] = by_model.get(roles["final"], 0) + 1
    calls = sum(r["calls"] for r in rows) + (1 if baseline else 0)
    return {"agents": n, "rounds": len(rows) - 1, "calls": calls,
            "waves": sum(r["waves"] for r in rows) + (1 if baseline else 0),
            "by_model": by_model}


# ---------------------------------------------------------------- paths

def p_solution0(d, aid):
    return os.path.join(d, "solutions", aid + ".md")


def p_attack(d, rnd, mid, attacker):
    return os.path.join(d, "r%d" % rnd, mid, "attack-by-%s.md" % attacker)


def p_defense(d, rnd, mid, aid):
    return os.path.join(d, "r%d" % rnd, mid, "defense-%s.md" % aid)


def p_revised(d, rnd, mid, aid):
    return os.path.join(d, "r%d" % rnd, mid, "revised-%s.md" % aid)


def p_verdict(d, rnd, mid):
    return os.path.join(d, "r%d" % rnd, mid, "verdict.json")


def p_final(d):
    return os.path.join(d, "final", "verdict.json")


def p_prompt(d, job):
    return os.path.join(d, "prompts", job + ".txt")


def has_output(path):
    try:
        with open(path, encoding="utf-8") as f:
            return bool(f.read().strip())
    except OSError:
        return False


# ---------------------------------------------------------------- state

def new_state(n, seed, run_dir, domain="general", budget=DEFAULT_BUDGET, wave=DEFAULT_WAVE,
              baseline=False):
    if n < 1:
        raise ArenaError("need at least one agent")
    if budget not in BUDGETS:
        raise ArenaError("unknown budget %r. Known: %s" % (budget, ", ".join(BUDGETS)))
    dom = load_domain(domain)
    cards = deal(n, seed, load_cards(), dom.get("priority", []))
    ids = agent_ids(n)
    st = {
        "version": 1, "n": n, "seed": seed, "domain": domain, "budget": budget, "wave": wave,
        "run_dir": run_dir, "created": time.strftime("%Y-%m-%d %H:%M:%S"),
        "has_baseline": baseline,
        "agents": {aid: {"card": cards[i], "solution": p_solution0(run_dir, aid), "alive": True,
                         "out_round": None, "byes": 0}
                   for i, aid in enumerate(ids)},
        "rounds": [], "phase": "spawn", "final": None,
    }
    if n == 1:
        st["phase"] = "final" if baseline else "done"
        st["needs_spawn"] = True
    else:
        open_round(st, ids, random.Random(seed + 1))
    return st


def alive_ids(st):
    return sorted(a for a, v in st["agents"].items() if v["alive"])


def open_round(st, alive, rng):
    alive = list(alive)
    bye = None
    if len(alive) % 2:
        fewest = min(st["agents"][a]["byes"] for a in alive)
        pool = [a for a in alive if st["agents"][a]["byes"] == fewest]
        bye = rng.choice(sorted(pool))
        st["agents"][bye]["byes"] += 1
        alive.remove(bye)
    rng.shuffle(alive)
    matches = []
    rnd = len(st["rounds"]) + 1
    while alive:
        a = alive.pop(0)
        ta = st["agents"][a]["card"]["thinking"]["id"]
        j = next((i for i, b in enumerate(alive)
                  if st["agents"][b]["card"]["thinking"]["id"] != ta), 0)
        b = alive.pop(j)
        matches.append({"id": "r%d-m%02d" % (rnd, len(matches) + 1), "a": a, "b": b,
                        "winner": None, "reason": None, "survived": [], "scores": None,
                        "source": None})
    st["rounds"].append({"n": rnd, "matches": matches, "bye": bye, "collected": False})


def current_round(st):
    return st["rounds"][-1] if st["rounds"] else None


def find_match(st, mid):
    for r in st["rounds"]:
        for m in r["matches"]:
            if m["id"] == mid:
                return r, m
    raise ArenaError("no match %r" % mid)


# ---------------------------------------------------------------- jobs

def phase_jobs(st, phase):
    d = st["run_dir"]
    roles = BUDGETS[st["budget"]]
    jobs = []
    if phase == "spawn":
        for aid in sorted(st["agents"]):
            jobs.append({"id": "spawn-" + aid, "role": "competitor", "agent": aid,
                         "outs": [p_solution0(d, aid)], "model": roles["competitor"]})
        return jobs
    if phase == "final":
        return [{"id": "final", "role": "final", "outs": [p_final(d)], "model": roles["final"]}]
    r = current_round(st)
    for m in r["matches"]:
        mid, a, b = m["id"], m["a"], m["b"]
        if phase == "attack":
            for x, y in ((a, b), (b, a)):
                jobs.append({"id": "%s-attack-%s" % (mid, x), "role": "attacker", "agent": x,
                             "target": y, "match": mid, "outs": [p_attack(d, r["n"], mid, x)],
                             "model": roles["attacker"]})
        elif phase == "defend":
            for x, y in ((a, b), (b, a)):
                jobs.append({"id": "%s-defend-%s" % (mid, x), "role": "defender", "agent": x,
                             "attacker": y, "match": mid,
                             "outs": [p_defense(d, r["n"], mid, x), p_revised(d, r["n"], mid, x)],
                             "model": roles["defender"]})
        elif phase == "judge":
            jobs.append({"id": "%s-judge" % mid, "role": "judge", "match": mid,
                         "outs": [p_verdict(d, r["n"], mid)], "model": roles["judge"]})
        else:
            raise ArenaError("unknown phase %r" % phase)
    return jobs


def missing(jobs):
    return [j for j in jobs if not all(has_output(o) for o in j["outs"])]


def read_task(st):
    with open(os.path.join(st["run_dir"], "task.md"), encoding="utf-8") as f:
        return f.read().strip()


def rubric_text(dom):
    lines = ["Domain: %s. %s" % (dom["name"], dom["about"]), ""]
    for c in dom["criteria"]:
        a = c["anchors"]
        lines.append("%s (weight %d): %s" % (c["id"], c["weight"], c["question"]))
        lines.append("  10: %s | 7: %s | 4: %s | 0-2: %s" % (a["10"], a["7"], a["4"], a["0"]))
    lines += ["",
              "Weighted total = sum(score x weight) / 10, from 0 to 100.",
              "A solution with a verified fatal flaw cannot beat one without.",
              "Not rewarded: length, confidence, talk about quality, agreement with your taste."]
    return "\n".join(lines)


def score_template(dom):
    keys = ", ".join('"%s": 0' % c["id"] for c in dom["criteria"])
    return "{%s, \"fatal\": false}" % keys


def card_values(card):
    return {"thinking_name": card["thinking"]["name"], "thinking_how": card["thinking"]["how"],
            "working_name": card["working"]["name"], "working_how": card["working"]["how"],
            "priority_name": card["priority"]["name"], "priority_how": card["priority"]["how"]}


def render(st, job, task, dom):
    d = st["run_dir"]
    base = {"task": task, "n": st["n"], "arena_dir": d, "rubric": rubric_text(dom),
            "score_template": score_template(dom)}
    if job["role"] == "competitor":
        aid = job["agent"]
        note = ""
        if st["has_baseline"]:
            note = ("The person who asked already rejected one answer to this task. It is at "
                    "%s. Read it, see why it fell short, and do better. Do not copy it.\n"
                    % os.path.join(d, "baseline.md"))
        v = dict(base, agent=aid, baseline_note=note, out=job["outs"][0],
                 **card_values(st["agents"][aid]["card"]))
        return fill(load_template("competitor"), v)
    if job["role"] == "final":
        rng = random.Random(st["seed"] + 999)
        champ = st["agents"][champion(st)]["solution"]
        base_path = os.path.join(d, "baseline.md")
        x, y = (champ, base_path) if rng.random() < 0.5 else (base_path, champ)
        st["final"] = {"x_is_champion": x == champ}
        v = dict(base, x_solution=x, y_solution=y, rounds=len(st["rounds"]), out=job["outs"][0])
        return fill(load_template("final"), v)
    r = current_round(st)
    _, m = find_match(st, job["match"])
    if job["role"] == "attacker":
        aid, tgt = job["agent"], job["target"]
        v = dict(base, agent=aid, target=tgt, round=r["n"], match=m["id"],
                 target_solution=st["agents"][tgt]["solution"],
                 own_solution=st["agents"][aid]["solution"], out=job["outs"][0],
                 **card_values(st["agents"][aid]["card"]))
        return fill(load_template("attacker"), v)
    if job["role"] == "defender":
        aid, atk = job["agent"], job["attacker"]
        v = dict(base, agent=aid, attacker=atk, round=r["n"], match=m["id"],
                 own_solution=st["agents"][aid]["solution"],
                 attacks=p_attack(d, r["n"], m["id"], atk),
                 defense_out=job["outs"][0], solution_out=job["outs"][1],
                 **card_values(st["agents"][aid]["card"]))
        return fill(load_template("defender"), v)
    if job["role"] == "judge":
        a, b = m["a"], m["b"]
        v = dict(base, match=m["id"], round=r["n"], first=a, second=b,
                 first_solution=p_revised(d, r["n"], m["id"], a),
                 first_attacks=p_attack(d, r["n"], m["id"], b),
                 first_defense=p_defense(d, r["n"], m["id"], a),
                 second_solution=p_revised(d, r["n"], m["id"], b),
                 second_attacks=p_attack(d, r["n"], m["id"], a),
                 second_defense=p_defense(d, r["n"], m["id"], b), out=job["outs"][0])
        return fill(load_template("judge"), v)
    raise ArenaError("unknown role %r" % job["role"])


def write_prompts(st, phase):
    if phase != expected_phase(st):
        raise ArenaError("the run is in phase %r, not %r. Run `next`." % (expected_phase(st), phase))
    task = read_task(st)
    dom = load_domain(st["domain"])
    jobs = missing(phase_jobs(st, phase))
    os.makedirs(os.path.join(st["run_dir"], "prompts"), exist_ok=True)
    for j in jobs:
        for o in j["outs"]:
            os.makedirs(os.path.dirname(o), exist_ok=True)
        with open(p_prompt(st["run_dir"], j["id"]), "w", encoding="utf-8") as f:
            f.write(render(st, j, task, dom))
        j["prompt"] = p_prompt(st["run_dir"], j["id"])
    return jobs


# ---------------------------------------------------------------- deciding

def weighted_total(scores, dom):
    t = 0.0
    for c in dom["criteria"]:
        try:
            v = float(scores.get(c["id"], 0))
        except (TypeError, ValueError):
            v = 0.0
        t += max(0.0, min(10.0, v)) * c["weight"]
    return round(t / 10, 1)


def truthy(v):
    return v is True or (isinstance(v, str) and v.strip().lower() in ("true", "yes", "1"))


def extract_json(text):
    text = text.strip()
    try:
        return json.loads(text)
    except ValueError:
        pass
    m = re.search(r"\{.*\}", text, re.S)
    if m:
        try:
            return json.loads(m.group(0))
        except ValueError:
            return None
    return None


def decide(verdict, a, b, dom):
    """Winner by arithmetic, not by the judge's say-so: fatal rule, then totals, then fewer
    standing attacks, then correctness-like first criterion, then the judge's pick."""
    s = verdict.get("scores", {}) if isinstance(verdict, dict) else {}
    sa, sb = s.get(a, {}) or {}, s.get(b, {}) or {}
    ta, tb = weighted_total(sa, dom), weighted_total(sb, dom)
    fa, fb = truthy(sa.get("fatal")), truthy(sb.get("fatal"))
    if fa != fb:
        return (b if fa else a), ta, tb, "fatal flaw"
    if ta != tb:
        return (a if ta > tb else b), ta, tb, "total"
    standing = verdict.get("standing", {}) or {}
    na, nb = len(standing.get(a, []) or []), len(standing.get(b, []) or [])
    if na != nb:
        return (a if na < nb else b), ta, tb, "fewer standing attacks"
    first = dom["criteria"][0]["id"]
    ca, cb = float(sa.get(first, 0) or 0), float(sb.get(first, 0) or 0)
    if ca != cb:
        return (a if ca > cb else b), ta, tb, "higher " + first
    pick = str(verdict.get("winner", "")).strip()
    return (pick if pick in (a, b) else a), ta, tb, "judge's call on a dead tie"


def record(st, mid, winner, reason=None, survived=None, scores=None, source="manual"):
    _, m = find_match(st, mid)
    if winner not in (m["a"], m["b"]):
        raise ArenaError("%s is not in match %s" % (winner, mid))
    m.update(winner=winner, reason=reason, survived=survived or [], scores=scores, source=source)


def collect(st):
    dom = load_domain(st["domain"])
    if st["phase"] == "final":
        path = p_final(st["run_dir"])
        if not has_output(path):
            raise ArenaError("the final verdict is missing")
        v = extract_json(open(path, encoding="utf-8").read())
        if v is None:
            raise ArenaError("the final verdict is not valid JSON. Run the final check again.")
        champ_is = "X" if st["final"]["x_is_champion"] else "Y"
        other = "Y" if champ_is == "X" else "X"
        w, tx, ty, why = decide(v, "X", "Y", dom)
        st["final"].update(winner_is_champion=(w == champ_is),
                           champion_total=tx if champ_is == "X" else ty,
                           baseline_total=ty if champ_is == "X" else tx,
                           reason=v.get("reason"), fixed=v.get("fixed", []), by=why,
                           champion_letter=champ_is, baseline_letter=other)
        st["phase"] = "done"
        return {"final": st["final"]}
    r = current_round(st)
    done, bad = 0, []
    for m in r["matches"]:
        path = p_verdict(st["run_dir"], r["n"], m["id"])
        if not has_output(path):
            bad.append(m["id"])
            continue
        v = extract_json(open(path, encoding="utf-8").read())
        if v is None or not isinstance(v.get("scores"), dict):
            bad.append(m["id"])
            continue
        w, ta, tb, why = decide(v, m["a"], m["b"], dom)
        record(st, m["id"], w, reason="%s (%s %.1f - %s %.1f, by %s)" % (
            v.get("reason", ""), m["a"], ta, m["b"], tb, why),
            survived=v.get("survived", []), scores={m["a"]: ta, m["b"]: tb}, source="judge")
        done += 1
    if bad:
        raise ArenaError("verdict missing or unreadable for: %s. Run `prompts judge` again "
                         "for those matches; never decide them yourself." % ", ".join(bad))
    r["collected"] = True
    return {"recorded": done}


def advance(st):
    r = current_round(st)
    if not r or not r["collected"]:
        raise ArenaError("collect the verdicts first")
    for m in r["matches"]:
        w = m["winner"]
        loser = m["b"] if w == m["a"] else m["a"]
        st["agents"][loser]["alive"] = False
        st["agents"][loser]["out_round"] = r["n"]
        st["agents"][w]["solution"] = p_revised(st["run_dir"], r["n"], m["id"], w)
    alive = alive_ids(st)
    if len(alive) == 1:
        st["phase"] = "final" if st["has_baseline"] else "done"
    else:
        open_round(st, alive, random.Random(st["seed"] + 1 + len(st["rounds"])))
    return alive


def champion(st):
    alive = alive_ids(st)
    return alive[0] if len(alive) == 1 else None


def expected_phase(st):
    if st["phase"] in ("final", "done"):
        if st.get("needs_spawn") and missing(phase_jobs(st, "spawn")):
            return "spawn"
        return st["phase"]
    if missing(phase_jobs(st, "spawn")):
        return "spawn"
    r = current_round(st)
    if r["collected"]:
        return "advance"
    for ph in ("attack", "defend", "judge"):
        if missing(phase_jobs(st, ph)):
            return ph
    return "collect"


# ---------------------------------------------------------------- files on disk

def run_dir_from_latest():
    latest = os.path.join(ROOT, "LATEST")
    if not os.path.exists(latest):
        raise ArenaError("no arena here. Run `init` first, from the same directory.")
    with open(latest, encoding="utf-8") as f:
        return f.read().strip()


def load_state(run_dir=None):
    d = run_dir or run_dir_from_latest()
    return load_json(os.path.join(d, "arena.json"))


def save_state(st):
    path = os.path.join(st["run_dir"], "arena.json")
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(st, f, ensure_ascii=False, indent=1)
    os.replace(tmp, path)


def me():
    return 'python3 "%s"' % os.path.abspath(__file__)


def card_line(card):
    return "%s + %s + %s" % (card["thinking"]["name"], card["working"]["name"],
                             card["priority"]["name"])


# ---------------------------------------------------------------- commands

def agents_from(args):
    if getattr(args, "quick", False):
        return QUICK_AGENTS
    if getattr(args, "full", False):
        return FULL_AGENTS
    return args.agents or DEFAULT_AGENTS


def cmd_plan(args):
    n = agents_from(args)
    t = plan_totals(n, args.wave, args.budget, args.baseline)
    print("%d agents, %d rounds, %d sub-agent calls in %d waves of up to %d (budget: %s)\n"
          % (n, t["rounds"], t["calls"], t["waves"], args.wave, args.budget))
    print("  %-6s %6s %8s %4s %6s %6s" % ("round", "alive", "matches", "bye", "calls", "waves"))
    for r in plan_rows(n, args.wave):
        print("  %-6s %6s %8s %4s %6s %6s" % (r["round"], r["alive"], r["matches"], r["bye"],
                                             r["calls"], r["waves"]))
    print("\n  calls by model: " + ", ".join("%s %d" % kv for kv in sorted(t["by_model"].items())))
    print("  alive per round: " + " -> ".join(str(x) for x in bracket_sizes(n)))
    if n > CONFIRM_ABOVE:
        print("\n  Above %d agents: ask the user for a clear yes before init." % CONFIRM_ABOVE)


def cmd_init(args):
    n = agents_from(args)
    seed = args.seed if args.seed is not None else random.randrange(1, 10 ** 6)
    if not os.path.exists(args.task_file):
        raise ArenaError("task file %s not found" % args.task_file)
    task = open(args.task_file, encoding="utf-8").read().strip()
    if not task:
        raise ArenaError("the task file is empty")
    run = os.path.join(ROOT, time.strftime("%Y%m%d-%H%M%S") + "-s%d" % seed)
    k = 1
    while os.path.exists(run):
        k += 1
        run = os.path.join(ROOT, time.strftime("%Y%m%d-%H%M%S") + "-s%d-%d" % (seed, k))
    os.makedirs(run)
    with open(os.path.join(run, "task.md"), "w", encoding="utf-8") as f:
        f.write(task + "\n")
    baseline = False
    if args.baseline_file:
        b = open(args.baseline_file, encoding="utf-8").read().strip()
        if b:
            with open(os.path.join(run, "baseline.md"), "w", encoding="utf-8") as f:
                f.write(b + "\n")
            baseline = True
    st = new_state(n, seed, run, args.domain, args.budget, args.wave, baseline)
    save_state(st)
    with open(os.path.join(ROOT, "LATEST"), "w", encoding="utf-8") as f:
        f.write(run)
    t = plan_totals(n, args.wave, args.budget, baseline)
    print("arena ready: %s" % run)
    print("  %d agents, seed %d, domain %s, budget %s, %d calls"
          % (n, seed, args.domain, args.budget, t["calls"]))
    print("  next: %s next" % me())


def cmd_next(args):
    st = load_state()
    ph = expected_phase(st)
    if ph == "done":
        print("DONE. Run: %s winner" % me())
    elif ph == "collect":
        print("All verdicts are in. Run: %s collect" % me())
    elif ph == "advance":
        print("Verdicts recorded. Run: %s advance" % me())
    else:
        left = len(missing(phase_jobs(st, ph)))
        where = "round %d" % current_round(st)["n"] if ph in ("attack", "defend", "judge") else ph
        print("Phase %s (%s): %d job(s) to run. Run: %s prompts %s" % (ph, where, left, me(), ph))
        if ph == "final":
            print("After the final verdict is written, run: %s collect" % me())


def cmd_prompts(args):
    st = load_state()
    jobs = write_prompts(st, args.phase)
    save_state(st)
    if not jobs:
        print("nothing to run for %s. Run: %s next" % (args.phase, me()))
        return
    w = st["wave"]
    print("%d job(s), %d wave(s). Launch one wave at a time, one Agent call per job:"
          % (len(jobs), (len(jobs) + w - 1) // w))
    print('  subagent_type general-purpose, description "arena <job>", model as listed,')
    print('  prompt "Read <prompt> and follow it exactly. It is your whole brief."\n')
    for i in range(0, len(jobs), w):
        print("wave %d" % (i // w + 1))
        for j in jobs[i:i + w]:
            print("  %-26s model=%-6s prompt=%s" % (j["id"], j["model"], j["prompt"]))
    print("\nAfter the last wave: %s next" % me())


def cmd_check(args):
    st = load_state()
    left = missing(phase_jobs(st, args.phase))
    if not left:
        print("all outputs present for %s" % args.phase)
    for j in left:
        print("%s: %s" % (j["id"], ", ".join(o for o in j["outs"] if not has_output(o))))


def cmd_collect(args):
    st = load_state()
    res = collect(st)
    save_state(st)
    if "final" in res:
        f = res["final"]
        verdict = "the champion" if f["winner_is_champion"] else "THE ANSWER YOU REJECTED"
        print("final check: %s wins, %.1f vs %.1f. %s" % (
            verdict, f["champion_total"] if f["winner_is_champion"] else f["baseline_total"],
            f["baseline_total"] if f["winner_is_champion"] else f["champion_total"],
            f.get("reason") or ""))
    else:
        print("recorded %d verdict(s). Run: %s advance" % (res["recorded"], me()))


def cmd_advance(args):
    st = load_state()
    r = current_round(st)
    alive = advance(st)
    save_state(st)
    print("Round %d done: %d of %d left." % (r["n"], len(alive), st["n"]))
    print("Run: %s next" % me())


def cmd_status(args):
    st = load_state()
    print("run %s  domain %s  budget %s  seed %d  phase %s"
          % (st["run_dir"], st["domain"], st["budget"], st["seed"], expected_phase(st)))
    alive = len(alive_ids(st))
    print("alive %d of %d" % (alive, st["n"]))
    for r in st["rounds"]:
        done = sum(1 for m in r["matches"] if m["winner"])
        print("  round %d: %d match(es), %d decided%s" % (
            r["n"], len(r["matches"]), done, ", bye " + r["bye"] if r["bye"] else ""))


def cmd_winner(args):
    st = load_state()
    c = champion(st)
    if not c or expected_phase(st) != "done":
        raise ArenaError("no champion yet. Run: %s next" % me())
    a = st["agents"][c]
    survived = []
    for r in st["rounds"]:
        for m in r["matches"]:
            if m["winner"] == c:
                survived.append("round %d vs %s: %s" % (
                    r["n"], m["b"] if m["a"] == c else m["a"], m["reason"] or ""))
                survived += ["    beat: " + s for s in (m.get("survived") or [])]
    print("champion: %s" % c)
    print("card: %s" % card_line(a["card"]))
    print("solution: %s" % a["solution"])
    print("rounds: %d, %d agents in, 1 left" % (len(st["rounds"]), st["n"]))
    if survived:
        print("what it survived:")
        for s in survived:
            print("  " + s)
    if st.get("final"):
        f = st["final"]
        if f.get("winner_is_champion") is not None:
            print("against the rejected answer: %s, %.1f vs %.1f" % (
                "champion wins" if f["winner_is_champion"] else "the rejected answer scored higher",
                f["champion_total"], f["baseline_total"]))
    print("full record: %s" % st["run_dir"])


def cmd_record(args):
    st = load_state()
    r = current_round(st)
    record(st, args.match, args.agent, reason=args.reason or "recorded by hand")
    if all(m["winner"] for m in r["matches"]):
        r["collected"] = True
    save_state(st)
    print("recorded %s for %s" % (args.agent, args.match))


def cmd_domains(args):
    for n in domain_names():
        d = load_domain(n)
        print("%-10s %s" % (n, d["about"]))


def build_parser():
    p = argparse.ArgumentParser(prog="arena.py", description=__doc__,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = p.add_subparsers(dest="cmd", required=True)

    def size(sp):
        g = sp.add_mutually_exclusive_group()
        g.add_argument("--agents", type=int)
        g.add_argument("--quick", action="store_true")
        g.add_argument("--full", action="store_true")
        sp.add_argument("--budget", default=DEFAULT_BUDGET, choices=sorted(BUDGETS))
        sp.add_argument("--wave", type=int, default=DEFAULT_WAVE)

    sp = sub.add_parser("plan")
    size(sp)
    sp.add_argument("--baseline", action="store_true")
    sp.set_defaults(fn=cmd_plan)
    sp = sub.add_parser("init")
    size(sp)
    sp.add_argument("--seed", type=int)
    sp.add_argument("--domain", default="general")
    sp.add_argument("--task-file", required=True)
    sp.add_argument("--baseline-file")
    sp.set_defaults(fn=cmd_init)
    for name, fn in (("next", cmd_next), ("collect", cmd_collect), ("advance", cmd_advance),
                     ("status", cmd_status), ("winner", cmd_winner), ("domains", cmd_domains)):
        sub.add_parser(name).set_defaults(fn=fn)
    for name, fn in (("prompts", cmd_prompts), ("check", cmd_check)):
        sp = sub.add_parser(name)
        sp.add_argument("phase", choices=["spawn", "attack", "defend", "judge", "final"])
        sp.set_defaults(fn=fn)
    sp = sub.add_parser("record")
    sp.add_argument("match")
    sp.add_argument("agent")
    sp.add_argument("--reason")
    sp.set_defaults(fn=cmd_record)
    return p


def main(argv=None):
    args = build_parser().parse_args(argv)
    try:
        args.fn(args)
    except ArenaError as e:
        print("arena: " + str(e), file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    sys.exit(main())
