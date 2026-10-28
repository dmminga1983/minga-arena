import io
import json
import os
import random
import re
import shutil
import sys
import tempfile
import unittest
import warnings
from contextlib import redirect_stdout, redirect_stderr

warnings.simplefilter("ignore", ResourceWarning)

HERE = os.path.dirname(os.path.abspath(__file__))
SKILL = os.path.join(HERE, "..", "skills", "minga-arena")
sys.path.insert(0, SKILL)
import arena  # noqa: E402


def run(*argv):
    out, err = io.StringIO(), io.StringIO()
    with redirect_stdout(out), redirect_stderr(err):
        code = arena.main(list(argv))
    return code, out.getvalue(), err.getvalue()


class FakeAgents:
    """Stands in for the sub-agents: reads each brief, writes what the brief asks for."""

    def __init__(self, seed=0, fail_first=()):
        self.rng = random.Random(seed)
        self.fail_first = set(fail_first)
        self.briefs = []

    def do(self, job):
        brief = open(job["prompt"], encoding="utf-8").read()
        self.briefs.append((job, brief))
        if job["id"] in self.fail_first:
            self.fail_first.discard(job["id"])
            return
        for o in job["outs"]:
            os.makedirs(os.path.dirname(o), exist_ok=True)
        if job["role"] == "judge":
            st = arena.load_state()
            _, m = arena.find_match(st, job["match"])
            dom = arena.load_domain(st["domain"])
            sc = {}
            for aid in (m["a"], m["b"]):
                sc[aid] = {c["id"]: self.rng.randint(3, 10) for c in dom["criteria"]}
                sc[aid]["fatal"] = False
            v = {"match": m["id"], "scores": sc, "winner": m["a"], "reason": "fake",
                 "survived": ["x"], "standing": {m["a"]: [], m["b"]: []}}
            open(job["outs"][0], "w", encoding="utf-8").write(json.dumps(v))
        elif job["role"] == "final":
            st = arena.load_state()
            dom = arena.load_domain(st["domain"])
            sc = {k: {c["id"]: 8 for c in dom["criteria"]} for k in ("X", "Y")}
            for k in sc:
                sc[k]["fatal"] = False
            sc["X"][dom["criteria"][0]["id"]] = 10
            v = {"scores": sc, "winner": "X", "reason": "fake", "fixed": []}
            open(job["outs"][0], "w", encoding="utf-8").write(json.dumps(v))
        else:
            for o in job["outs"]:
                open(o, "w", encoding="utf-8").write("text for " + job["id"])


def drive(agents, max_steps=5000):
    for _ in range(max_steps):
        st = arena.load_state()
        ph = arena.expected_phase(st)
        if ph == "done":
            return st
        if ph == "collect":
            assert run("collect")[0] == 0
            continue
        if ph == "advance":
            assert run("advance")[0] == 0
            continue
        jobs = arena.write_prompts(st, ph)
        arena.save_state(st)
        for j in jobs:
            agents.do(j)
        if ph == "final":
            st = arena.load_state()
            if not arena.missing(arena.phase_jobs(st, "final")):
                assert run("collect")[0] == 0
    raise AssertionError("tournament did not finish")


class TempDir(unittest.TestCase):
    def setUp(self):
        self.old = os.getcwd()
        self.dir = tempfile.mkdtemp()
        os.chdir(self.dir)
        open("task.md", "w", encoding="utf-8").write(
            "Напишите заголовок для страницы цен.\nТочно эту строку {{не трогать}}.")

    def tearDown(self):
        os.chdir(self.old)
        shutil.rmtree(self.dir, ignore_errors=True)


class TestTournament(TempDir):
    def full(self, n, baseline=False, domain="general", seed=7):
        args = ["init", "--agents", str(n), "--seed", str(seed), "--task-file", "task.md",
                "--domain", domain]
        if baseline:
            open("old.md", "w", encoding="utf-8").write("старый ответ")
            args += ["--baseline-file", "old.md"]
        code, out, err = run(*args)
        self.assertEqual(code, 0, err)
        agents = FakeAgents(seed)
        st = drive(agents)
        self.assertEqual(len(arena.alive_ids(st)), 1)
        return st, agents

    def test_sizes_end_with_one_champion(self):
        for n in (1, 2, 3, 5, 7, 8, 16, 33):
            with self.subTest(n=n):
                st, _ = self.full(n)
                self.assertEqual(len(st["rounds"]), len(arena.bracket_sizes(n)) - 1)
                code, out, _ = run("winner")
                self.assertEqual(code, 0)
                self.assertIn("champion:", out)

    def test_hundred(self):
        st, agents = self.full(100, seed=3)
        self.assertEqual(len(st["rounds"]), 7)
        calls = len([b for b in agents.briefs])
        self.assertEqual(calls, arena.plan_totals(100)["calls"])

    def test_task_is_byte_identical_in_every_brief(self):
        _, agents = self.full(8, baseline=True)
        task = open(os.path.join(arena.load_state()["run_dir"], "task.md"),
                    encoding="utf-8").read().strip()
        for job, brief in agents.briefs:
            self.assertIn(task, brief, job["id"])
        # a placeholder inside the task is not touched by the template engine
        self.assertTrue(all("{{не трогать}}" in b for _, b in agents.briefs))

    def test_judges_never_see_cards(self):
        st, agents = self.full(8)
        names = {a["card"]["priority"]["name"] for a in st["agents"].values()}
        for job, brief in agents.briefs:
            if job["role"] in ("judge", "final"):
                for nm in names:
                    self.assertNotIn("Priority: " + nm, brief)

    def test_baseline_final_check(self):
        st, _ = self.full(4, baseline=True)
        self.assertIsNotNone(st["final"]["winner_is_champion"])
        code, out, _ = run("winner")
        self.assertIn("against the rejected answer", out)

    def test_domains_run(self):
        for d in arena.domain_names():
            with self.subTest(domain=d):
                self.full(4, domain=d)

    def test_missing_output_is_rerun_not_skipped(self):
        run("init", "--agents", "4", "--seed", "1", "--task-file", "task.md")
        st = arena.load_state()
        jobs = arena.write_prompts(st, "spawn")
        arena.save_state(st)
        fake = FakeAgents(fail_first={jobs[0]["id"]})
        for j in jobs:
            fake.do(j)
        st = arena.load_state()
        self.assertEqual(arena.expected_phase(st), "spawn")
        left = arena.write_prompts(st, "spawn")
        self.assertEqual([j["id"] for j in left], [jobs[0]["id"]])

    def test_wrong_phase_refused(self):
        run("init", "--agents", "4", "--seed", "1", "--task-file", "task.md")
        code, _, err = run("prompts", "judge")
        self.assertEqual(code, 2)
        self.assertIn("phase", err)


class TestRules(unittest.TestCase):
    def setUp(self):
        self.dom = arena.load_domain("general")

    def v(self, a, b, fa=False, fb=False, sta=0, stb=0):
        return {"scores": {"a": dict(a, fatal=fa), "b": dict(b, fatal=fb)},
                "standing": {"a": ["x"] * sta, "b": ["x"] * stb}, "winner": "b"}

    def s(self, x):
        return {c["id"]: x for c in self.dom["criteria"]}

    def test_higher_total_wins_over_judge_pick(self):
        w, ta, tb, _ = arena.decide(self.v(self.s(9), self.s(5)), "a", "b", self.dom)
        self.assertEqual((w, ta, tb), ("a", 90.0, 50.0))

    def test_fatal_cannot_beat_non_fatal(self):
        w, *_ = arena.decide(self.v(self.s(10), self.s(2), fa=True), "a", "b", self.dom)
        self.assertEqual(w, "b")

    def test_tie_breaks_on_standing_attacks(self):
        w, *_ = arena.decide(self.v(self.s(7), self.s(7), sta=2, stb=1), "a", "b", self.dom)
        self.assertEqual(w, "b")

    def test_scores_are_clamped(self):
        self.assertEqual(arena.weighted_total(self.s(50), self.dom), 100.0)
        self.assertEqual(arena.weighted_total({"correctness": "x"}, self.dom), 0.0)

    def test_extract_json_from_fenced_text(self):
        self.assertEqual(arena.extract_json('```json\n{"a": 1}\n```'), {"a": 1})


class TestDealer(unittest.TestCase):
    def test_unique_and_balanced(self):
        cards = arena.load_cards()
        for n in (8, 16, 45, 100):
            d = arena.deal(n, 5, cards)
            keys = {(c["thinking"]["id"], c["working"]["id"], c["priority"]["id"]) for c in d}
            self.assertEqual(len(keys), n)
            for part, size in (("thinking", 15), ("working", 12), ("priority", 12)):
                counts = {}
                for c in d:
                    counts[c[part]["id"]] = counts.get(c[part]["id"], 0) + 1
                if n >= size:
                    self.assertLessEqual(max(counts.values()) - min(counts.values()), 1)

    def test_same_seed_same_cards(self):
        cards = arena.load_cards()
        self.assertEqual(arena.deal(16, 9, cards), arena.deal(16, 9, cards))

    def test_no_shared_pairs_for_small_runs(self):
        d = arena.deal(16, 2, arena.load_cards())
        seen = set()
        for c in d:
            t, w, p = c["thinking"]["id"], c["working"]["id"], c["priority"]["id"]
            for pair in (("tw", t, w), ("tp", t, p), ("wp", w, p)):
                self.assertNotIn(pair, seen)
                seen.add(pair)


class TestPlan(unittest.TestCase):
    def test_known_sizes(self):
        self.assertEqual(arena.plan_totals(16)["calls"], 91)
        self.assertEqual(arena.plan_totals(8)["calls"], 43)
        self.assertEqual(arena.plan_totals(100)["calls"], 595)

    def test_defaults(self):
        self.assertEqual(arena.DEFAULT_AGENTS, 16)
        self.assertEqual(arena.QUICK_AGENTS, 8)
        code, out, _ = run("plan", "--full")
        self.assertIn("ask the user", out)

    def test_budget_routes_judge_to_strongest(self):
        t = arena.plan_totals(16, budget="max")
        self.assertEqual(t["by_model"]["opus"], 15)


class TestRepo(unittest.TestCase):
    ROOT = os.path.join(HERE, "..")

    def test_templates_have_no_unknown_placeholders(self):
        known = set()
        for t in os.listdir(os.path.join(SKILL, "templates")):
            text = open(os.path.join(SKILL, "templates", t), encoding="utf-8").read()
            known |= set(re.findall(r"\{\{(\w+)\}\}", text))
        allowed = {"agent", "n", "task", "baseline_note", "thinking_name", "thinking_how",
                   "working_name", "working_how", "priority_name", "priority_how", "out",
                   "arena_dir", "round", "match", "target", "target_solution", "own_solution",
                   "attacker", "attacks", "defense_out", "solution_out", "rubric", "first",
                   "second", "first_solution", "first_attacks", "first_defense",
                   "second_solution", "second_attacks", "second_defense", "score_template",
                   "rounds", "x_solution", "y_solution", "your"}
        self.assertEqual(known - allowed, set())

    def test_no_long_dashes_in_text_files(self):
        for base, _, files in os.walk(self.ROOT):
            if ".git" in base:
                continue
            for f in files:
                if f.endswith((".md", ".json", ".txt", ".py")):
                    text = open(os.path.join(base, f), encoding="utf-8").read()
                    self.assertNotIn(chr(0x2014), text, os.path.join(base, f))

    def test_skill_frontmatter(self):
        text = open(os.path.join(SKILL, "SKILL.md"), encoding="utf-8").read()
        m = re.match(r"---\nname: (.+)\ndescription: (.+?)\n---\n", text, re.S)
        self.assertTrue(m)
        self.assertEqual(m.group(1).strip().strip('"'), "minga-arena")
        self.assertLessEqual(len(m.group(2)), 1024)

    def test_plugin_manifests(self):
        p = json.load(open(os.path.join(self.ROOT, ".claude-plugin", "plugin.json")))
        self.assertEqual(p["name"], "minga-arena")
        json.load(open(os.path.join(self.ROOT, ".claude-plugin", "marketplace.json")))


if __name__ == "__main__":
    unittest.main()
