# reach-check

**See what a Play actually reaches on your machine, before you publish it or run it.**

[![tests](https://github.com/StephenSook/reach-check/actions/workflows/tests.yml/badge.svg)](https://github.com/StephenSook/reach-check/actions/workflows/tests.yml)
[![License: MIT](https://img.shields.io/badge/License-MIT-blue.svg)](./LICENSE)
[![Python 3.9+](https://img.shields.io/badge/python-3.9%2B-3776AB.svg?logo=python&logoColor=white)](./play/deps.toml)
[![stdlib only](https://img.shields.io/badge/dependencies-none-3fb950.svg)](./play/deps.toml)
[![read only](https://img.shields.io/badge/effects-read_only-3fb950.svg)](#what-it-does-not-do)
[![Rote Play](https://img.shields.io/badge/Rote-sookra%2Freach--check-6933FF.svg)](https://play.modiqo.ai/sookra/reach-check@0.1.0)

Built for the [Rote Playoffs](https://modiqo.ai/blog/the-playoffs), Modiqo's Rote and Play hackathon, September 2026.

> **Across the 22 Play packages installed on my machine, 29 executables are reached that no manifest declares, and 5 of those are not installed here.** `rote play inspect` reports every one of those Plays as eligible to run, because it is answering a different question: it resolves what a Play *declares*. This reads what its steps actually *invoke*.

Both statements are true. That gap is the whole tool.

## Judge quick access

Every row is checkable by a stranger, with nothing installed but python.

| To verify... | Do this |
|---|---|
| **It runs on a clean clone, no dependencies** | `git clone https://github.com/StephenSook/reach-check && cd reach-check && python3 tests/test_reach.py` |
| **It runs on the python a stock Mac actually has** | Same command under `/usr/bin/python3` (3.9). CI runs 3.9 and 3.13 on Linux and macOS. |
| **It never executes what it reads** | `grep -n "ast.parse" play/resources/reach_analysis.py`, then `grep -nE "\b(exec|eval|__import__|importlib.import_module)\(" play/resources/reach_analysis.py` returns nothing. |
| **It finds a binary a regex cannot** | [`tests/fixtures/loop-variable`](tests/fixtures/loop-variable) invokes a tool only through a Python loop variable. |
| **It refuses to read outside a package** | [`tests/fixtures/escaping-resource`](tests/fixtures/escaping-resource) points a resource reference at `/etc/passwd` and is refused. |
| **It admits what it cannot read** | [`tests/fixtures/javascript-body`](tests/fixtures/javascript-body) is a node body; it is named in `unanalysed_bodies`, not silently skipped. |
| **The published Play runs** | `rote play run https://play.modiqo.ai/sookra/reach-check@0.1.0` |

## Run it

```bash
rote play run https://play.modiqo.ai/sookra/reach-check@0.1.0
```

Point it at a Play you are still writing, which is the habit it was built for:

```bash
rote play run https://play.modiqo.ai/sookra/reach-check@0.1.0 play=/path/to/your-play
```

| Parameter | Default | What it does |
|---|---|---|
| `play` | `all` | `all`, an `owner/name` reference, or a path to a package directory |
| `flows_root` | `~/.rote/flows` | read a different rote store |
| `strict` | `false` | exit non-zero when something reached is missing here |
| `offline` | `false` | skip the one step that calls `rote play inspect`, so no network call is made |

By default a finding is information, not a failure. The run succeeds and the table is the result.

## What it reports

One row per package: declared, reached, undeclared but reached, missing here, and rote's own
eligibility verdict beside this one. Then per package: writes split by whether the target leaves the
working directory, adapters called, browser steps, environment variables read by name, imports that
are not installed, bodies it could not read, and any call target the Play builds at run time.

## How it reads a package

- `manifest.json` when the package was pulled or published.
- `main.ts` frontmatter plus `deps.toml` when the package is still being written and has no manifest
  yet. That is the case the tool leads with, and `rote play info --json` does not expose steps, so
  the frontmatter is parsed directly.

## How it finds reach

- `argv[0]`, then shell bodies tokenised with `shlex`, twice: once whole so a multi-line quoted body
  stays intact, and once per line because `shlex` treats a newline as ordinary whitespace and a real
  script would otherwise collapse into a single segment.
- `@resource{...}` files, routed by language. Python bodies are parsed; shell bodies are tokenised;
  a JavaScript body is reported as unread rather than silently contributing nothing. Rote's own
  `_rote_python_c.py` exec shim is excluded, so its imports are never attributed to the Play.
- Python parsed with `ast` and **never executed**, with constant propagation over string and list
  bindings anywhere in the module and over `for` targets, so a binary invoked through a loop variable is still found. An `argv`
  list bound to a name yields its head, not its flags.
- Module presence via `importlib.util.find_spec` on top-level names only, because `find_spec` on a
  dotted name imports the parent package, which would break the promise that nothing is imported.

The command filter is deliberately conservative. Shell grammar words, builtins, assignments, globs,
flags and loop variables are not commands, and when a token cannot be shown to be one it is not
reported. **A false claim about someone else's package is the worst failure this tool can have.**

## What it does not do

- It does not execute, import or pull the Play it reads, and it writes nothing.
- It does shell out to `rote play inspect` for the "rote says" column. That is the only subprocess it runs and the only network call it makes. `offline=true` removes both.
- It does not read JavaScript. Those bodies are named in `unanalysed_bodies` and printed in the report.
- It cannot resolve a call target assembled at run time. Those are reported by name rather than dropped.
- It does not model platform branching, and it cannot see state a previous run left behind.
- It reads code, not execution. A call inside `if False:` is still reported as reached, and a name
  reassigned to a computed value keeps its earlier constant. Both are deliberate: this reports what
  a package contains, and narrowing that to what a particular run would execute is a different and
  much harder question.
- Reachability is evidence and uncertainty, never a guarantee of safety.

## Verification

```bash
python3 tests/test_reach.py
```

57 checks against fixture packages, so the suite needs nothing installed and no packages pulled. CI
runs it on python 3.9 and 3.13, on Linux and macOS.

Most of those checks exist because the tool was proved wrong and then fixed. Running it against 20
real published Plays found five: nested resource modules read as missing dependencies, shell bodies
parsed as Python, a resource reference able to escape the package, shell grammar words reported as
executables, and an `argv` list reported as several commands. Two adversarial reviews of the shipped code found eleven more: writes performed from a Python body were invisible so the writes column read
zero for almost every Play, `subprocess(..., shell=True)` was dropped entirely, an aliased import
defeated detection, a shell redirect was always called local even when it targeted `/etc`, a step
carrying both a resource and an inline body lost the inline one, and every diagnostic note was
computed and then discarded before the user could see it. A second review found that a
multi-line shell body was read as one segment, so the first command appeared to write to every
path in the script and every later command was lost; that python source lines and heredoc text
inside a shell body were reported as executables; that `subprocess.run(["sh", "-c", ...])` was
never followed; that a `case` label read as a command; and that a malformed manifest crashed the
whole run instead of degrading. Each is fixed and each has a test.

Separately, during development every pulled package was read a second way, from `main.ts`
frontmatter instead of `manifest.json`, and the two independent readings were required to produce
byte-identical `argv`. That cross-check found four parser defects before reaching zero mismatches.

## Layout

| Path | What |
|---|---|
| `play/` | the published Play package: `main.ts`, `deps.toml`, `resources/` |
| `play/resources/reach_analysis.py` | the extractor |
| `tests/` | hermetic fixtures and the suite |
| `examples-pulled-play-inventory/` | a second, smaller published Play, kept as a worked example |

## Related work

[`modiqo/play-dag`](https://play.modiqo.ai/modiqo/play-dag) renders a Play's step graph, and
[`jaylabs/audit-play`](https://play.modiqo.ai/jaylabs/audit-play) audits whether a Play's contract is
internally consistent. Both are good, and both answer a different question than this one: neither
reads the step bodies for what they invoke, and neither resolves that against your host.

## License

MIT. See [LICENSE](./LICENSE).
