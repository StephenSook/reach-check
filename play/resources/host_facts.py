import json
import os
import platform
import shutil
import sys

path_entries = [p for p in os.environ.get("PATH", "").split(os.pathsep) if p]
print(
    json.dumps(
        {
            "ok": True,
            "python": sys.version.split()[0],
            "python_path": sys.executable,
            "stdlib_source": "api" if hasattr(sys, "stdlib_module_names") else "sysconfig-fallback",
            "stdlib_count": len(getattr(sys, "stdlib_module_names", []) or []),
            "platform": platform.system(),
            "release": platform.release(),
            "machine": platform.machine(),
            "path_entries": len(path_entries),
            "rote_on_path": shutil.which("rote") is not None,
        }
    )
)
