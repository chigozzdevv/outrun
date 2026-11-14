#!/bin/zsh

set -u

# Finder-launched Terminal sessions may not include the user's Homebrew bin path.
export PATH="/opt/homebrew/bin:/usr/local/bin:$PATH"
intent_thesis_project_dir="${0:A:h}"
cd -- "$intent_thesis_project_dir" || exit 1

intent_thesis_fail() {
  printf '\n%s\n' "$1"
  read -r 'intent_thesis_ack?Press Return to close this window... '
  exit 1
}

printf '\nOutrun\nCan NEAR Intents outrun dilution?\n\n'
printf 'Project: %s\n' "$intent_thesis_project_dir"

if ! command -v node >/dev/null 2>&1 || ! command -v npm >/dev/null 2>&1; then
  intent_thesis_fail 'Node.js and npm are required. Install Node.js 22.12 or newer from https://nodejs.org/, then open this launcher again.'
fi

if ! node -e 'const [major, minor] = process.versions.node.split(".").map(Number); if (major < 22 || (major === 22 && minor < 12)) process.exit(1);'; then
  intent_thesis_fail "This project requires Node.js 22.12 or newer. Installed: $(node --version). Update Node.js, then try again."
fi

if [[ ! -d node_modules || ! -x node_modules/.bin/vite ]]; then
  printf '\nInstalling the dependencies recorded in package-lock.json...\n'
  npm ci || intent_thesis_fail 'Dependency installation did not finish. Check your network connection, then open this launcher again. The lockfile and project files are unchanged.'
fi

printf '\nOpen http://127.0.0.1:5174/ in your browser.\n'
printf 'Keep this Terminal window open while using the app.\n'
printf 'Press Control-C here to stop the local server.\n\n'

trap 'printf "\nOutrun stopped.\n"; exit 0' INT
npm run dev
intent_thesis_exit=$?

if (( intent_thesis_exit != 0 && intent_thesis_exit != 130 )); then
  intent_thesis_fail 'The server could not start. If port 5174 is already in use, reuse the existing preview or stop that server before trying again. The details are printed above.'
fi
