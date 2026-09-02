VERSION="$(subst-tool --version)"
echo `backtick-tool -v`
command fwd-tool --go
env FOO=1 env-tool --go
