#!/bin/bash
# A simple wrapper script to execute the main RAG query application.

# Exit immediately if a command fails.
set -e

# Ensure we are in the correct directory (project root)
cd "$(dirname "$0")"

# Load environment variables from .env file if it exists
if [ -f .env ]; then
  set -a # automatically export all variables
  source .env
  set +a # stop automatically exporting
fi

# Activate virtual environment if it exists
if [ -d "venv" ]; then
  source venv/bin/activate
fi

# --- Argument Handling ---
# Simple pass-through of arguments
ARGS=("$@")
# --- End Argument Handling ---

echo "Executing RAG query application with arguments: ${ARGS[@]}"
echo "-----------------------------------------------------"

# Pass the processed arguments to the Python CLI entrypoint
python3 -m rag_system.cli query "${ARGS[@]}"
