import _frozen_importlib
import sys

_runner_path = sys.argv[0]
_source_path = sys.argv[1]
_source_args = sys.argv[2:]
with open(_source_path, encoding="utf-8", newline="") as _source_file:
    _source = _source_file.read()
if hasattr(sys, "orig_argv"):
    try:
        _runner_index = sys.orig_argv.index(_runner_path)
    except ValueError:
        pass
    else:
        sys.orig_argv[_runner_index:_runner_index + 2] = ["-c"]
sys.argv[:] = ["-c", *_source_args]
if sys.path:
    sys.path[0] = ""
else:
    sys.path.append("")
_main = {
    "__name__": "__main__",
    "__doc__": None,
    "__package__": None,
    "__loader__": _frozen_importlib.BuiltinImporter,
    "__spec__": None,
    "__annotations__": {},
    "__builtins__": __builtins__,
}
exec(compile(_source, "<string>", "exec"), _main, _main)
