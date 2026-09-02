import os
import pathlib
import shutil

open("out/report.json", "w").write("{}")
os.makedirs("out/nested")
shutil.copy("a", "/etc/somewhere-else")
pathlib.Path("~/.ssh/authorized_keys").write_text("x")
open("read-only.txt").read()
