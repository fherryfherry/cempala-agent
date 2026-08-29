#!/usr/bin/env bash
# One-shot installer: clone CEMPALA and check prerequisites.
# Usage: curl -fsSL https://raw.githubusercontent.com/fherryfherry/cempala-agent/main/install.sh | bash
set -euo pipefail

REPO_URL="https://github.com/fherryfherry/cempala-agent.git"
DEST="${1:-cempala}"

case "$(uname -s)" in
  Linux*|Darwin*) ;;
  *)
    echo "Unsupported OS. On Windows, run this script inside WSL." >&2
    exit 1
    ;;
esac

missing=()
command -v git >/dev/null 2>&1 || missing+=("git")
command -v node >/dev/null 2>&1 || missing+=("node (v20+)")
command -v make >/dev/null 2>&1 || missing+=("make")
if command -v python3 >/dev/null 2>&1; then
  py_minor=$(python3 -c 'import sys; print(sys.version_info[1])')
  [ "$py_minor" -ge 11 ] || missing+=("python3.11+ (found $(python3 --version))")
else
  missing+=("python3.11+")
fi

if [ "${#missing[@]}" -gt 0 ]; then
  echo "Missing prerequisites:" >&2
  printf '  - %s\n' "${missing[@]}" >&2
  echo "See https://github.com/fherryfherry/cempala-agent#prerequisites for install links." >&2
  exit 1
fi

if [ -d "$DEST" ]; then
  echo "==> $DEST already exists, pulling latest"
  git -C "$DEST" pull
else
  echo "==> Cloning into $DEST"
  git clone "$REPO_URL" "$DEST"
fi

cat <<EOF

==> Prerequisites OK, repo ready at ./$DEST

Next steps:
  1. Install & authenticate at least one agent CLI (opencode is simplest):
       curl -fsSL https://opencode.ai/install | bash && opencode auth login
  2. cd $DEST && ./run.sh
  3. Open http://localhost:3000
EOF
