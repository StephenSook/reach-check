# reach-check

**See what a Play actually reaches on your machine, before you publish it or run it.**

`rote play inspect` shows what a Play *declares*, and resolves those declarations against your host.
Nothing reported the set a Play never declared. That undeclared set is what this reads.

Published as [`sookra/reach-check`](https://play.modiqo.ai/sookra/reach-check@0.1.0) on the Rote
registry. Read-only, python3 standard library only, no credentials. It never runs, imports, pulls or
writes anything.

## Run it

```bash
rote play run https://play.modiqo.ai/sookra/reach-check@0.1.0
```

Point it at a Play you are still writing, which is the habit it was built for:

```bash
rote play run https://play.modiqo.ai/sookra/reach-check@0.1.0 play=/path/to/your-play
```

Other parameters: `flows_root` to read a different rote store, `offline=true` to make no network
call at all, `strict=true` to exit non-zero when something reached is missing here. By default a
finding is information, not a failure, so the run succeeds and the table is the result.

## What it reports

One row per package: declared, reached, undeclared but reached, missing here, and rote's own
eligibility verdict beside ours. Then per package: writes split by whether the target leaves the
working directory, adapters called, browser steps, environment variables read by name, third party
imports that are not installed, and any call target the Play builds at run time that cannot be
resolved by reading.

## How it reads a package

- `manifest.json` when the package was pulled or published.
- `main.ts` frontmatter plus `deps.toml` when the package is still being written and has no manifest
  yet. That is the case the tool leads with, and `rote play info --json` does not expose steps, so
  the frontmatter is read directly.

## How it finds reach

- `argv[0]`, then shell bodies tokenised with `shlex` across pipes, semicolons and and-lists,
  recursing into nested `python3 -c` and `sh -c`.
- `@resource{...}` files, including rote's own `_rote_python_c.py` exec shim, whose imports are
  deliberately **not** attributed to the Play.
- Python bodies parsed with `ast` and **never executed**, with constant propagation over
  module-level bindings and `for` targets, so a binary invoked through a loop variable is still
  found. A regex or a shell tokenizer finds none of those.
- Module presence via `importlib.util.find_spec` on top-level names only, because `find_spec` on a
  dotted name imports the parent package, which would break the promise that nothing is imported.

## What it does not do

- It does not execute, import or pull the Play it reads.
- It cannot resolve a call target the Play assembles at run time. Those are reported by name rather
  than dropped.
- It does not model platform branching, and it cannot see state a previous run left behind.
- Reachability is evidence and uncertainty, never a guarantee of safety.

## Verification

The extractor is tested against fixture packages that exercise each body shape, so the suite needs
nothing installed and no packages pulled:

```bash
python3 tests/test_reach.py
```

16 checks, run on python 3.9 and 3.13, on Linux and macOS, in CI. The fixtures cover a binary
reached only through a loop variable, a shell body that writes and nests an interpreter, a body
behind the exec shim, an adapter step with no `type` key, browser steps, and a package that has no
`manifest.json` yet.

Separately, during development every pulled package was read a second way, from `main.ts`
frontmatter instead of `manifest.json`, and the two independent readings were required to produce
byte-identical `argv`. That cross-check found four real parser defects before it reached zero
mismatches, including YAML block scalars needing both minimum-indent dedent and chomping-indicator
handling.

## Layout

| Path | What |
|---|---|
| `play/` | the published Play package: `main.ts`, `deps.toml`, `resources/` |
| `play/resources/reach_analysis.py` | the extractor |
| `tests/` | hermetic fixtures and the test suite |
| `examples-pulled-play-inventory/` | a second, smaller published Play, kept as a worked example |

## Related work

[`modiqo/play-dag`](https://play.modiqo.ai/modiqo/play-dag) renders a Play's step graph, and
[`jaylabs/audit-play`](https://play.modiqo.ai/jaylabs/audit-play) audits whether a Play's contract is
internally consistent. Both are good, and both answer a different question than this one: neither
reads the step bodies for what they invoke, and neither resolves that against your host.

## License

MIT.
