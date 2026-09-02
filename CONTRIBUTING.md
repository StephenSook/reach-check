# Contributing

## Run the tests

```bash
python3 tests/test_reach.py
```

No dependencies and no packages need to be pulled. The fixtures under `tests/fixtures/` are
complete Play packages, so the suite is hermetic and runs anywhere python does.

Run it under `/usr/bin/python3` as well. **Python 3.9 is the floor**, because that is what a stock
macOS with only the Xcode command line tools provides, and a stranger running this will often have
exactly that. CI enforces 3.9 and 3.13 on Linux and macOS.

## The rule that matters

This tool reads other people's packages and makes public claims about them. **A false positive is
worse than a miss.** If a token cannot be shown to be a command, do not report it. If a body cannot
be read, name it in `unanalysed_bodies` rather than let the package look clean.

Every one of the five conservatism fixes in the history came from running the tool against real
published Plays and finding it wrong. If you change the extractor, run it against a real corpus, not
only the fixtures:

```bash
python3 play/resources/reach_analysis.py all
```

## Adding a case

A behaviour change needs a fixture, not just an assertion. Add a directory under `tests/fixtures/`
containing a real `manifest.json` or `main.ts`, then assert against it in `tests/test_reach.py`.
Fixtures are cheap and they document the input shape better than prose.

## Things this tool must never do

- Execute, import, or pull the package it is reading.
- Call `find_spec` on a dotted name, which imports the parent package.
- Read a file outside the package's own `resources/` directory.

There are tests for all three. If you find a way around one, that is a security bug; see
[SECURITY.md](./SECURITY.md).
