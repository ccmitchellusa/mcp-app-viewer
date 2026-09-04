#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"
EXT_DIR="$REPO_DIR/vscode-extension"
VERSION="$(tr -d ' \n' < "$REPO_DIR/VERSION")"
OUT_DIR="${1:-$REPO_DIR/dist/vscode-extension}"
PACKAGE_JSON="$EXT_DIR/package.json"
VSIX_PATH="$OUT_DIR/ccmitchellusa.mcp-app-viewer-${VERSION}.vsix"
SHA_PATH="$OUT_DIR/ccmitchellusa.mcp-app-viewer-${VERSION}.vsix.sha256"
META_PATH="$OUT_DIR/mcp-app-viewer-vscode-extension-version.json"

mkdir -p "$OUT_DIR"

PKG_VERSION=$(python3 - <<'PY' "$PACKAGE_JSON"
import json, pathlib, sys
path = pathlib.Path(sys.argv[1])
pkg = json.loads(path.read_text(encoding='utf-8'))
print(pkg.get('version', '').strip())
PY
)

if [ "$PKG_VERSION" != "$VERSION" ]; then
  echo "release/build-vscode-extension.sh: VERSION ($VERSION) != vscode-extension/package.json version ($PKG_VERSION)" >&2
  exit 1
fi

(
  cd "$EXT_DIR"
  npx @vscode/vsce package --allow-missing-repository --out "$VSIX_PATH"
)

sha256=$(shasum -a 256 "$VSIX_PATH" | awk '{print $1}')
printf '%s  %s\n' "$sha256" "$(basename "$VSIX_PATH")" > "$SHA_PATH"

python3 - <<'PY' "$META_PATH" "$VERSION" "$sha256" "$(basename "$VSIX_PATH")" "$(basename "$SHA_PATH")"
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

echo "built $VSIX_PATH"
echo "wrote $SHA_PATH"
echo "wrote $META_PATH"
