head-tool
python3 -c 'import subprocess
subprocess.run(["embedded-tool"])'
cat <<'MARK' > out/note.txt
some heredoc body words
MARK
tail-tool
