#!/usr/bin/env python3
"""Hermetic tests for the reach extractor.

Every case runs against a fixture package in tests/fixtures, so this suite needs
nothing installed and no packages pulled. Run it with:

    python3 tests/test_reach.py

It is deliberately dependency free and runs on python 3.9 and newer, which is the
floor a stranger on a stock macOS actually has.
"""
import importlib.util
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
FIX = os.path.join(HERE, "fixtures")
EXTRACTOR = os.path.join(ROOT, "play", "resources", "reach_analysis.py")

spec = importlib.util.spec_from_file_location("reach_analysis", EXTRACTOR)
mod = importlib.util.module_from_spec(spec)
sys.modules["reach_analysis"] = mod
spec.loader.exec_module(mod)

FAILURES = []
CHECKS = 0


def check(label, condition, detail=""):
    global CHECKS
    CHECKS += 1
    if condition:
        print("  pass  " + label)
    else:
        print("  FAIL  " + label + ("  -> " + str(detail) if detail else ""))
        FAILURES.append(label)


def analyze(name):
    return mod.analyze(os.path.join(FIX, name), name)


print("reach extractor, hermetic fixtures")
print("python", sys.version.split()[0], "| stdlib via",
      "api" if hasattr(sys, "stdlib_module_names") else "sysconfig-fallback")
print()

print("loop-variable: a binary reached only through a python loop variable")
r = analyze("loop-variable")
check("finds both tools bound in a module-level list", {"alpha-tool", "beta-tool"} <= set(r["reached"]), r["reached"])
check("reports them as undeclared", {"alpha-tool", "beta-tool"} <= set(r["undeclared_but_reached"]), r["undeclared_but_reached"])
check("still records the declared interpreter", "python3" in r["reached"], r["reached"])

print("shell-nested: a shell body that writes and nests an interpreter")
r = analyze("shell-nested")
check("finds shell command heads across separators", {"sleep", "mkdir", "tee"} <= set(r["reached"]), r["reached"])
check("recurses into the nested python3 -c", "python3" in r["reached"], r["reached"])
check("records a write inside the working directory", len(r["writes_cwd"]) >= 1, r["writes_cwd"])
check("invents no write outside the working directory", r["writes_outside_cwd"] == [], r["writes_outside_cwd"])

print("resource-body: a body behind rote's exec shim")
r = analyze("resource-body")
check("follows the @resource reference to the real body", "gamma-tool" in r["reached"], r["reached"])
check("reads environment variables by name", {"FIXTURE_TOKEN", "FIXTURE_HOME"} <= set(r["env_vars_read"]), r["env_vars_read"])
check("does not attribute the shim's own imports to the Play",
      "_frozen_importlib" not in r["third_party_imports"] + r.get("sibling_modules", []),
      r["third_party_imports"])

print("adapter-browser: non-process steps are reach too")
r = analyze("adapter-browser")
check("adapter step with no type key is found", r["adapters_reached"] == ["adapter/example"], r["adapters_reached"])
check("browser steps are flagged", r["needs_browser"] is True, r["step_kinds"])

print("authored-frontmatter: a package with no manifest.json yet")
r = analyze("authored-frontmatter")
check("reads the package from main.ts frontmatter", "frontmatter" in r["read_from"], r["read_from"])
check("parses a YAML block scalar body", "delta-tool" in r["reached"], r["reached"])
check("does not mistake depends_on entries for argv", "probe" not in r["reached"], r["reached"])
check("reads declared tools from deps.toml", r["declared"] == ["python3"], r["declared"])

print()
print("%d checks, %d failed" % (CHECKS, len(FAILURES)))
if FAILURES:
    for f in FAILURES:
        print("  failed:", f)
    sys.exit(1)
print("ok")
