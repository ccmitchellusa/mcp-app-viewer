#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"
VERSION="$(tr -d ' \n' < "$REPO_DIR/VERSION")"
OUT_DIR="${1:-$REPO_DIR/dist}"
ARTIFACT_BASENAME="mcp-app-viewer-${VERSION}"
ARTIFACT_PATH="$OUT_DIR/${ARTIFACT_BASENAME}.tgz"
SHA_PATH="$OUT_DIR/${ARTIFACT_BASENAME}.sha256"
VERSION_JSON="$OUT_DIR/mcp-app-viewer-version.json"

mkdir -p "$OUT_DIR"

python3 - "$REPO_DIR" "$ARTIFACT_PATH" <<'PY'
import pathlib, sys, tarfile
repo = pathlib.Path(sys.argv[1]).resolve()
out = pathlib.Path(sys.argv[2]).resolve()
with tarfile.open(out, 'w:gz') as tf:
    for path in sorted(repo.rglob('*')):
        rel = path.relative_to(repo)
        parts = rel.parts
        if not parts:
            continue
        if parts[0] in {'.git', 'dist', '.venv', '__pycache__', '.pytest_cache'}:
            continue
        if path.name.endswith('.pyc') or path.name == '.DS_Store':
            continue
        tf.add(path, arcname=str(pathlib.Path('mcp-app-viewer') / rel), recursive=False)
PY

sha256=$(shasum -a 256 "$ARTIFACT_PATH" | awk '{print $1}')
printf '%s  %s\n' "$sha256" "$(basename "$ARTIFACT_PATH")" > "$SHA_PATH"

python3 - "$VERSION_JSON" "$VERSION" "$sha256" "$(basename "$ARTIFACT_PATH")" "$(basename "$SHA_PATH")" <<'PY'
import json, pathlib, sys
path = pathlib.Path(sys.argv[1])
version, sha, artifact, sha_file = sys.argv[2:]
payload = {
    'version': version,
    'artifact': artifact,
    'sha256': sha,
    'sha256_file': sha_file,
}
path.write_text(json.dumps(payload, indent=2, sort_keys=True) + '\n', encoding='utf-8')
PY

echo "built $ARTIFACT_PATH"
echo "wrote $SHA_PATH"
echo "wrote $VERSION_JSON"
