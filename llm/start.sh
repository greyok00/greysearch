#!/usr/bin/env bash
# Greysearch local-model helper — NO daemon, nothing resident.
#   llm/start.sh --pull [<org/file.gguf>]   download a GGUF into ./models/
#   llm/start.sh --status                   binary? model? GPU free?
# The audit runner (llm/oneshot compiled module) spawns llama-server PER
# AUDIT and kills it after; this script only fetches model files and
# reports status.
set -euo pipefail
HERE="$(cd "$(dirname "$0")" && pwd)"
MODELS="$HERE/../models"
DEFAULT_MODEL="https://huggingface.co/bartowski/Qwen2.5-7B-Instruct-GGUF/resolve/main/Qwen2.5-7B-Instruct-Q4_K_M.gguf"
mkdir -p "$MODELS"

status() {
  echo "llama-server binary: $(python3 -c "import sys;sys.path.insert(0,'$HERE');from llm import oneshot;print(oneshot.llama_bin() or 'NOT FOUND')")"
  echo "gguf model:          $(python3 -c "import sys;sys.path.insert(0,'$HERE');from llm import oneshot;print(oneshot.find_model() or 'NOT INSTALLED (run --pull)')")"
  nvidia-smi --query-gpu=memory.used,memory.total --format=csv,noheader 2>/dev/null \
    | sed 's/^/GPU used,total MiB: /' || echo "GPU: none detected"
}

pull() {
  local url="${1:-$DEFAULT_MODEL}"
  local fname; fname="$(basename "$url")"
  echo "pulling $url -> $MODELS/$fname"
  curl -L --fail --retry 2 -o "$MODELS/$fname.part" "$url"
  mv "$MODELS/$fname.part" "$MODELS/$fname"
  echo "installed: $MODELS/$fname"
}

case "${1:-}" in
  --pull)   pull "${2:-}" ;;
  --status) status ;;
  *) echo "usage: $0 [--pull [gguf_url] | --status]" >&2; exit 2 ;;
esac
