#!/usr/bin/env bash
# Initial setup for Mobility Pulse.
# Creates a virtualenv, installs dependencies, and prepares .env.
set -euo pipefail
cd "$(dirname "${BASH_SOURCE[0]}")"

# On Windows/Git Bash, `python`/`python3` on PATH is often the Microsoft Store
# alias stub, not a real interpreter, and silently produces a broken venv
# (dangling symlinks to a python that doesn't exist). Prefer the `py` launcher,
# which resolves to a real install, and verify each candidate actually runs.
find_python() {
  for candidate in "${PYTHON:-}" py python3 python; do
    [ -n "$candidate" ] || continue
    if command -v "$candidate" >/dev/null 2>&1 && "$candidate" --version >/dev/null 2>&1; then
      echo "$candidate"
      return 0
    fi
  done
  return 1
}
PYTHON="$(find_python)" || { echo "Error: no working python/python3/py found on PATH." >&2; exit 1; }

if [ -f ".venv/Scripts/activate" ]; then
  VENV_ACTIVATE=".venv/Scripts/activate"   # Windows venv layout
elif [ -f ".venv/bin/activate" ]; then
  VENV_ACTIVATE=".venv/bin/activate"       # Linux/macOS venv layout
else
  VENV_ACTIVATE=""
fi

if [ -d ".venv" ] && [ -z "$VENV_ACTIVATE" ]; then
  echo "Existing .venv is missing its activate script (broken/incomplete venv) - recreating it..."
  rm -rf .venv
fi

if [ ! -d ".venv" ]; then
  echo "Creating virtual environment (.venv) with $PYTHON..."
  "$PYTHON" -m venv .venv
  if [ -f ".venv/Scripts/activate" ]; then
    VENV_ACTIVATE=".venv/Scripts/activate"
  else
    VENV_ACTIVATE=".venv/bin/activate"
  fi
else
  echo "Virtual environment (.venv) already exists, skipping creation."
fi
# shellcheck disable=SC1090
source "$VENV_ACTIVATE"

# Sourcing activate is expected to put the venv's python first on PATH, but
# that hasn't proven reliable across all Git Bash / shell setups on Windows,
# so call the venv's own interpreter by its known path instead of bare
# `python` - that works regardless of what activate did to PATH.
if [ -x ".venv/Scripts/python.exe" ]; then
  VENV_PYTHON=".venv/Scripts/python.exe"
else
  VENV_PYTHON=".venv/bin/python"
fi

echo "Installing dependencies from requirements.txt..."
# `pip install --upgrade pip` (rather than `python -m pip ...`) can fail on
# Windows because pip can't overwrite its own running executable; it's not
# essential, so failure here is non-fatal.
"$VENV_PYTHON" -m pip install --upgrade pip >/dev/null 2>&1 || true
"$VENV_PYTHON" -m pip install -r requirements.txt

if [ ! -f ".env" ]; then
  echo "Creating .env from .env.example..."
  cp .env.example .env
else
  echo ".env already exists, leaving it untouched."
fi

mkdir -p data

echo
echo "Setup complete."
echo "  1. Activate the environment:  source $VENV_ACTIVATE"
echo "  2. Place source CSVs in data/ (see README.md > Data files)."
echo "  3. Edit .env for your chosen LLM_PROVIDER (default: groq):"
echo "       - groq (default):   set GROQ_API_KEY"
echo "       - ollama (local):   set LLM_PROVIDER=ollama, LLM_MODEL=qwen:14b,"
echo "                           then run 'ollama pull qwen:14b' and 'ollama serve'"
echo "       - openai:           set LLM_PROVIDER=openai, OPENAI_API_KEY, LLM_MODEL"
echo "  4. Verify the bootstrap:      PYTHONPATH=. $VENV_PYTHON -m core.bootstrap.app_context"
echo "  5. Run the app:               streamlit run app/streamlit_app.py"
