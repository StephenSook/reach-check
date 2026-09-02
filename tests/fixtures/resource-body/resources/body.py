import os
import subprocess
subprocess.run(["gamma-tool", "--check"], capture_output=True)
TOKEN = os.environ["FIXTURE_TOKEN"]
HOME_CFG = os.getenv("FIXTURE_HOME")
