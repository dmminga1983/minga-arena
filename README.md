# minga-arena

**When Claude keeps giving you a weak answer, make a tournament out of it: 16 sub-agents get the identical task and different strategy cards, attack each other's work, defend and revise, and a judge scores every match until one answer is left.**

Part of the [Minga](https://github.com/dmminga1983/minga) ecosystem. Sized for a normal plan: 16 agents by default, 8 with `--quick`, 100 only when you ask for it. Each role gets its own model, so the judges are strong and the attacks are cheap. Free, MIT, no API key, nothing to connect.

[Русская версия →](README.ru.md)

## How it works

1. **Spawn.** N sub-agents get the same task, byte for byte (a test checks it), plus one strategy card each: a way of thinking (15), a way of working (12) and a priority (12, plus the domain's own). That is over 2,000 distinct cards. The dealer never repeats one, spreads every part evenly, and on small runs never lets two agents share two parts of a card.
2. **Attack.** Agents are paired, avoiding two with the same way of thinking. Each attacks the other's solution through its own card: up to 7 attacks, labelled FATAL, MAJOR or MINOR, each pointing at the exact place and the exact counterexample.
3. **Defend.** Each side concedes or rebuts every attack it took and rewrites its solution with the fixes.
4. **Judge.** A separate judge reads both revised solutions, checks every attack itself and scores both against the domain's rubric. It never sees the cards. `arena.py` does the arithmetic: the higher weighted total goes through, and a solution with a verified fatal flaw cannot beat one without.
5. **Repeat** until one is left. An odd number means a bye, never twice for the same agent while someone else is waiting.
6. **Final check.** If you started from an answer you rejected, a last judge compares the champion with it blind. You get the score even when the old answer wins.

The main session never reads the hundreds of files: every sub-agent writes to disk and replies with one line, and the whole tournament lives in one JSON file. If the conversation is compacted halfway, `arena.py next` picks it up.

## Install

**Claude Code**, as a plugin:

```
/plugin marketplace add dmminga1983/minga-arena
/plugin install minga-arena@minga-arena
```

Or copy the folder to get a plain `/minga-arena`:

```bash
git clone https://github.com/dmminga1983/minga-arena.git
cp -r minga-arena/skills/minga-arena ~/.claude/skills/
```

It needs Claude Code (it launches sub-agents with the Agent tool) and Python 3.8 or newer. Nothing to pip install.

## Use it

```
/minga-arena
/minga-arena --quick write the headline for our pricing page
/minga-arena --domain code fix the flaky test in tests/test_api.py
/minga-arena --domain legal draft the pre-trial claim from these documents
/minga-arena --agents 32 --budget max plan the launch week
```

On its own, `/minga-arena` takes your last request as the task and the answer you did not like as the one to beat. Claude can also reach for it when you say "bad answer, make them compete"; then it asks before spending anything.

| flag | what it does |
| --- | --- |
| (none) | 16 agents |
| `--quick` | 8 agents |
| `--agents N` | any number |
| `--full` | 100 agents |
| `--domain D` | the rubric and extra cards: `general`, `code`, `writing`, `legal`, `expertise` |
| `--budget B` | which model plays which role: `economy`, `balanced`, `max` |
| `--seed S` | same seed, same cards and bracket |
| `--wave W` | sub-agents per wave, default 10 |

## What it costs

The skill is free. The calls come out of your plan, so it prints the size before it starts and asks for a yes above 32 agents or on `--budget max`.

| agents | rounds | sub-agent calls |
| --- | --- | --- |
| 8, `--quick` | 3 | 43 |
| 16, default | 4 | 91 |
| 32 | 5 | 187 |
| 100, `--full` | 7 | 595 |

| budget | competitors | attackers | defenders | judges |
| --- | --- | --- | --- | --- |
| `economy` | haiku | haiku | haiku | sonnet |
| `balanced` (default) | sonnet | haiku | sonnet | sonnet |
| `max` | sonnet | sonnet | sonnet | opus |

`python3 skills/minga-arena/arena.py plan --agents N --budget B` prints the calls per model any time.

## Domains

Each domain is one JSON file in `skills/minga-arena/domains/`: the criteria, their weights (they must add up to 100), the score anchors, and extra priority cards. Edit them or add your own.

| domain | judged on |
| --- | --- |
| `general` | correctness 30, completeness 25, robustness 20, specificity 15, clarity 10 |
| `code` | correctness 35, verification 20, completeness 20, safety 15, clarity 10 |
| `writing` | fit 25, specificity 20, voice 20, correctness 20, structure 15 |
| `legal` | legal basis 30, evidence 25, specific demands 20, completeness 15, clarity 10 |
| `expertise` | FACT, EVIDENCE, NORM, CONCLUSION chain 30, norms 25, causality 20, objectivity 15, clarity 10 |

## The tool

`arena.py` is standard-library Python and the reason the orchestrator never loses track.

```bash
python3 arena.py plan --agents 16              # rounds, calls, calls per model
python3 arena.py init --task-file task.md --quick --domain writing [--baseline-file old.md]
python3 arena.py next                          # what to do now, with the exact command
python3 arena.py prompts attack                # write the briefs, list jobs in waves with models
python3 arena.py check judge                   # outputs still missing
python3 arena.py collect                       # record the judges' verdicts
python3 arena.py advance                       # eliminate, pair the survivors
python3 arena.py status | winner | domains
```

Tests run full tournaments for 1 to 100 agents with stand-in sub-agents, every domain, the final check, re-runs of missing outputs, the dealer's guarantees and the decision rules:

```bash
python3 -m unittest discover -s tests -v
```

## The fine print

- **"16 versions of Claude" means 16 sub-agents**, not 16 different models. The card is what makes them different.
- **The best answer means the one that survived every match.** The judges are Claude too, scoring against a rubric you can read and change. It is the strongest answer the tournament found, not a proof. That is why it shows the attacks it survived and tells you plainly when your old answer scored higher.
- **Legal and expertise drafts still go to a qualified person** before anyone relies on them.
- **It never edits your project.** Code comes back as a diff or full files, and applying it is your call.
- **Sub-agents cannot see your chat.** The task file is all they know. A vague task gives 16 flavours of vague.
- **Expect permission prompts** unless you run it in accept-edits mode: every sub-agent writes a file into `.arena/`.

## Credit

Made by [dmminga1983](https://github.com/dmminga1983). The tournament idea was inspired by [arena-skill](https://github.com/Jakeschincariol/arena-skill) by Jake Schincariol; the code, cards, rubrics and domains here are written from scratch.

## License

MIT.
