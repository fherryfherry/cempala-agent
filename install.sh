#!/usr/bin/env bash
# One-shot installer: install prerequisites, clone CEMPALA, print next steps.
# Usage: curl -fsSL https://raw.githubusercontent.com/fherryfherry/cempala-agent/main/install.sh | bash
set -euo pipefail

REPO_URL="https://github.com/fherryfherry/cempala-agent.git"
DEST="${1:-cempala}"

OS="$(uname -s)"
case "$OS" in
  Linux|Darwin) ;;
  *)
    echo "Unsupported OS. On Windows, run this script inside WSL." >&2
    exit 1
    ;;
esac

PKG_MANAGER=""
if [ "$OS" = "Darwin" ]; then
  command -v brew >/dev/null 2>&1 && PKG_MANAGER="brew"
elif command -v apt-get >/dev/null 2>&1; then
  PKG_MANAGER="apt"
elif command -v dnf >/dev/null 2>&1; then
  PKG_MANAGER="dnf"
fi

install_pkg() {
  # $1: brew formula, $2: apt package(s), $3: dnf package(s)
  case "$PKG_MANAGER" in
    brew) brew install "$1" ;;
    apt) sudo apt-get update -qq && sudo apt-get install -y $2 ;;
    dnf) sudo dnf install -y $3 ;;
    *)
      echo "No supported package manager found (brew/apt/dnf) — install manually: $1" >&2
      return 1
      ;;
  esac
}

has_node20() {
  command -v node >/dev/null 2>&1 && [ "$(node -e 'console.log(process.versions.node.split(".")[0])')" -ge 20 ]
}

# Distro-packaged nodejs is often years out of date (e.g. Ubuntu 22.04 ships v12).
# Use NodeSource's setup script so apt/dnf install a current major version instead.
install_node() {
  case "$PKG_MANAGER" in
    brew) brew install node ;;
    apt) curl -fsSL https://deb.nodesource.com/setup_20.x | sudo -E bash - && sudo apt-get install -y nodejs ;;
    dnf) curl -fsSL https://rpm.nodesource.com/setup_20.x | sudo -E bash - && sudo dnf install -y nodejs ;;
    *) echo "No supported package manager found — install Node.js 20+ manually" >&2; return 1 ;;
  esac
}

command -v git >/dev/null 2>&1 || { echo "==> Installing git"; install_pkg git git make; }
command -v make >/dev/null 2>&1 || { echo "==> Installing make"; install_pkg make make make; }
has_node20 || { echo "==> Installing Node.js 20"; install_node; }
command -v uv >/dev/null 2>&1 || { echo "==> Installing uv"; curl -LsSf https://astral.sh/uv/install.sh | sh; export PATH="$HOME/.local/bin:$PATH"; }
command -v opencode >/dev/null 2>&1 || { echo "==> Installing opencode"; curl -fsSL https://opencode.ai/install | bash; export PATH="$HOME/.opencode/bin:$PATH"; }

# uv manages its own Python interpreters (run.sh uses `uv venv --python 3.12`), so a
# system Python 3.11+ isn't required as long as uv is present.
missing=()
command -v git >/dev/null 2>&1 || missing+=("git")
has_node20 || missing+=("node (v20+)")
command -v make >/dev/null 2>&1 || missing+=("make")
command -v uv >/dev/null 2>&1 || missing+=("uv")

if [ "${#missing[@]}" -gt 0 ]; then
  echo "Still missing (auto-install failed or unsupported package manager):" >&2
  printf '  - %s\n' "${missing[@]}" >&2
  echo "See https://github.com/fherryfherry/cempala-agent#prerequisites for manual install links." >&2
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

==> Prerequisites OK, repo ready at $DEST

Next steps:
  1. Authenticate opencode (already installed by this script): opencode auth login
  2. cd $DEST && ./run.sh
  3. Open http://localhost:3000
EOF
