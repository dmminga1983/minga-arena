---
name: minga-arena
description: "Runs a tournament of sub-agents on one task when an answer is not good enough: N agents (default 16, --quick 8, up to 100) get the identical task and different strategy cards, attack each other's solutions, defend and revise, and a judge scores every match on a written rubric until one solution survives. Domain packs for code, writing, legal claims and construction expertise. Use when the user says arena, make them compete, bad answer, try again properly, do better; RU: арена, турнир, устрой битву, плохой ответ, переделай как следует. Not for a simple retry or a one-line fix."
---

# minga-arena

A tournament instead of asking again. N sub-agents get exactly the same task, each one attacks it
with a different card (a way of thinking, a way of working, a priority), then they fight in a
single-elimination bracket: attack, defend and revise, judge. One solution is left.

You are the orchestrator. You never compete, attack or judge, and you never pick a winner.

Reply to the user in their language. All bookkeeping goes through the tool in this folder:

```bash
python3 "${CLAUDE_SKILL_DIR}/arena.py" <command>
```

Below, `ARENA` means that command. If the variable is not expanded, use the base directory of this
skill that Claude Code printed when it loaded it. Run every `ARENA` command from the same directory:
the run lives in `.arena/` there.

## Step 1: size it and get a yes

Flags in the user's request; everything else is the task.

| flag | meaning |
| --- | --- |
| (none) | 16 agents, 91 sub-agent calls |
| `--quick` | 8 agents, 43 calls |
| `--agents N` | any number up to 2,160 |
| `--full` | 100 agents, 595 calls |
| `--domain D` | `general` (default), `code`, `writing`, `legal`, `expertise`. `ARENA domains` lists them |
| `--budget B` | `balanced` (default), `economy`, `max`: which model plays which role |
| `--seed S` | same seed, same cards and same bracket |
| `--wave W` | sub-agents per wave, default 10 |

No task text at all: the task is the user's latest request in this conversation, and your last
answer to it is the baseline to beat.

Pick the domain yourself when it is obvious (a bug is `code`, a pre-trial claim is `legal`, a
review of an expert report is `expertise`, a post or a script is `writing`) and say which.

Run `ARENA plan` with the same flags. It prints rounds, calls and calls per model.

- The user asked for the arena by name: tell them the size in one line ("16 agents, 4 rounds, 91
  sub-agent calls: 16 sonnet, 30 haiku, 45 sonnet") and start.
- The skill fired because the user is unhappy and never said "arena": ask once, with three
  options: `--quick` (43 calls), the default (91 calls), or an ordinary retry. Wait.
- More than 32 agents, or `--budget max`: always ask for a clear yes first, whatever the trigger.
  These runs use a lot of the user's plan limit.

Every sub-agent writes a file into `.arena/`. In the default permission mode that is one approval
per file. Suggest accept-edits mode for the run. Do not change the user's settings yourself.

## Step 2: write the task file

This decides the result. Sub-agents cannot see this conversation. Write `.arena/task.md` so a
stranger could do the work from it alone:

- the request, in the user's words where you can
- every requirement and limit the user gave anywhere in the conversation: audience, length,
  format, tone, stack, deadline, what must not change
- the context: absolute paths of the files that matter, pasted data, what the product is
- what done looks like, if the user said
- if there is an answer to beat: what the user disliked about it, in their words

Do not add requirements the user never gave. Do not write your own idea of the right answer into
it: that pushes every agent the same way, which defeats the point. If there is a rejected answer,
write it word for word to `.arena/baseline.md`.

## Step 3: init

```bash
ARENA init --task-file .arena/task.md [--quick | --agents N | --full] [--domain D] \
           [--budget B] [--seed S] [--baseline-file .arena/baseline.md]
```

## Step 4: the loop

Always drive it with `ARENA next`: it reads the state on disk and prints the next step and the
exact command.

Every phase that runs sub-agents works the same way:

1. `ARENA prompts <phase>` writes one brief per job and lists the jobs in waves, each with its
   model.
2. Launch one wave at a time: a single message with one Agent call per job in that wave.
   `subagent_type` general-purpose, `description` "arena <job id>", `model` as listed, `prompt`
   "Read <prompt path> and follow it exactly. It is your whole brief." Wait for the whole wave.
3. After the last wave, `ARENA next`. If an output is missing it sends you back to the same phase
   and `prompts` lists only the missing jobs. Re-run those once. If a competitor, attacker or
   defender fails twice, write the single line `NO OUTPUT` into each of its missing files
   (`ARENA check <phase>` lists them) and move on. A judge that fails gets a fresh run; never
   decide a match yourself.

Order: spawn once, then every round attack, defend, judge, `ARENA collect`, `ARENA advance`, then
the final check (only with a baseline) and `ARENA collect`, then DONE.

If minga-router is installed, its rules still apply on top of the listed model: escalate a judge
that returns an unreadable verdict to the next stronger model rather than retrying the same one.

After each `advance`, give the user one line: "Round 2 done: 4 of 16 left." Nothing more. Never
paste briefs, attacks, verdicts or solutions into the chat during the run.

## Step 5: the result

Run `ARENA winner`, then read the champion's solution file at the path it prints. That is the
only solution file you read in the whole run. Give the user:

1. the winning solution, in full
2. why it won: the attacks it survived, as a short list, and its card on one line
3. the rounds: "4 rounds, 16 agents in, 1 left"
4. against the rejected answer, if there was one: the final scores, honestly. If the old answer
   scored higher, say so plainly and show both
5. where the full record is: the run folder

If the solution changes files in the user's project, do not apply it. Ask: apply it, or change it?

## Rules for the orchestrator

- The task reaches every agent byte for byte the same, through the briefs. Never paraphrase it for
  one agent and never add a hint to one agent's call.
- Do not read solutions, attacks or verdicts during the run. `next`, `status` and `check` are all
  you need.
- After a context compaction nothing is lost: `ARENA status`, then `ARENA next`.
- Sub-agents write only inside `.arena/`. If one wrote anywhere else, tell the user.
- If the user says stop, stop. `ARENA next` resumes later.
- `ARENA record` is only for fixing bookkeeping when the user asks.

## What the results mean

The judges are Claude too, scoring against the rubric in `domains/<domain>.json`, which the user
can read and change. The winner is the strongest answer this tournament found, not proof that it
is right. For legal and expertise work the winning draft still goes to a qualified person before
anyone relies on it. A vague task gives N flavours of vague: the task file is the lever.
