# Security

## Threat model

This tool reads **untrusted input**: Play packages written by other people, pulled from a public
registry. It is designed so that reading a hostile package cannot execute it.

The guarantees, each with a test:

| Guarantee | Enforced by |
|---|---|
| The analysed code is never executed | Python bodies go to `ast.parse`, which builds a tree and runs nothing. The extractor contains no `exec`, `eval`, `__import__` or `importlib.import_module`. |
| The analysed code is never imported | `importlib.util.find_spec` is called on **top-level names only**. On a dotted name it would import the parent package. |
| No file outside the package is read | `@resource{...}` is resolved with `realpath` and refused unless it stays inside that package's `resources/` directory. |
| Nothing is written | The tool declares no writes and performs none. |
| No network call, on request | `offline=true` skips the only step that makes one. |

## Known limits

These are limits, not vulnerabilities, and the tool reports them rather than hiding them:

- JavaScript bodies are not read. They are named in `unanalysed_bodies`.
- A call target assembled at run time cannot be resolved by reading, and is reported by name.
- Reachability is evidence and uncertainty. A clean row is not a safety guarantee, and this tool
  should not be the only thing between you and running someone else's code.

## Reporting

Open an issue at <https://github.com/StephenSook/reach-check/issues>. If the finding is a way to
make the tool execute, import, or read outside a package, please say so in the title so it is
triaged first.
