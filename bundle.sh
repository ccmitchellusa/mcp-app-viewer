#!/usr/bin/env bash
set -euo pipefail

SELF_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
VERSION_FILE="$SELF_DIR/VERSION"
VERSION="$(tr -d ' \n' < "$VERSION_FILE")"
INSTALL_ROOT="${MCP_APP_INSTALL_ROOT:-$HOME/.local/share/mcp-app-viewer}"
VERSIONS_DIR="$INSTALL_ROOT/versions"
CURRENT_LINK="$INSTALL_ROOT/current"
BIN_DIR="${MCP_APP_BIN_DIR:-$HOME/.local/bin}"
STATE_DIR="${XDG_STATE_HOME:-$HOME/.local/state}/mcp-app-viewer"
MANIFEST="$STATE_DIR/install-manifest.json"
VERSION_DIR="$VERSIONS_DIR/$VERSION"
RUNTIME_LINK="$BIN_DIR/mcp-app.sh"
BUNDLE_LINK="$BIN_DIR/mcp-app-viewer-bundle"

usage() {
  cat <<EOF
bundle.sh — install/update/uninstall the versioned mcp-app-viewer runtime bundle

Usage:
  bundle.sh install
  bundle.sh update
  bundle.sh uninstall
  bundle.sh status
  bundle.sh version
EOF
}

require_cmd() {
  command -v "$1" >/dev/null 2>&1 || { echo "bundle.sh: required command not found: $1" >&2; exit 2; }
}

require_cmd python3

copy_runtime() {
  local src="$1" dest="$2"
  python3 - "$src" "$dest" <<'PY'
import os, pathlib, shutil, sys
src = pathlib.Path(sys.argv[1]).resolve()
dest = pathlib.Path(sys.argv[2]).resolve()
if dest.exists():
    shutil.rmtree(dest)
ignore = shutil.ignore_patterns(
    '.git', '.gitignore', '.DS_Store', '__pycache__', '*.pyc', '.pytest_cache', 'dist', '.venv'
)
shutil.copytree(src, dest, symlinks=True, ignore=ignore)
PY
}

write_manifest() {
  mkdir -p "$STATE_DIR"
  python3 - "$MANIFEST" "$VERSION" "$INSTALL_ROOT" "$VERSION_DIR" "$CURRENT_LINK" "$RUNTIME_LINK" "$BUNDLE_LINK" <<'PY'
import json, pathlib, sys, time
manifest = pathlib.Path(sys.argv[1])
version, install_root, version_dir, current_link, runtime_link, bundle_link = sys.argv[2:]
payload = {
    'version': version,
    'install_root': install_root,
    'version_dir': version_dir,
    'current_link': current_link,
    'symlinks': [
        {'path': runtime_link, 'target': f'{install_root}/current/bin/mcp-app.sh'},
        {'path': bundle_link, 'target': f'{install_root}/current/bundle.sh'},
    ],
    'installed_at_epoch': int(time.time()),
}
manifest.write_text(json.dumps(payload, indent=2, sort_keys=True) + '\n', encoding='utf-8')
PY
}

install_bundle() {
  mkdir -p "$VERSIONS_DIR" "$BIN_DIR" "$STATE_DIR"
  local tmp
  tmp="$(mktemp -d "$VERSIONS_DIR/.${VERSION}.tmp.XXXXXX")"
  trap 'rm -rf "$tmp"' EXIT
  copy_runtime "$SELF_DIR" "$tmp/runtime"
  rm -rf "$VERSION_DIR"
  mv "$tmp/runtime" "$VERSION_DIR"
  ln -sfn "$VERSION_DIR" "$CURRENT_LINK"
  ln -sfn "$CURRENT_LINK/bin/mcp-app.sh" "$RUNTIME_LINK"
  ln -sfn "$CURRENT_LINK/bundle.sh" "$BUNDLE_LINK"
  write_manifest
  trap - EXIT
  rm -rf "$tmp"
  echo "installed mcp-app-viewer $VERSION"
  echo "  runtime : $VERSION_DIR"
  echo "  current : $CURRENT_LINK"
  echo "  mcp-app : $RUNTIME_LINK"
}

uninstall_bundle() {
  if [ ! -f "$MANIFEST" ]; then
    echo "mcp-app-viewer not installed via bundle manifest"
    exit 0
  fi
  python3 - "$MANIFEST" <<'PY' > "$STATE_DIR/.bundle-uninstall.env"
import json, pathlib, sys
payload = json.loads(pathlib.Path(sys.argv[1]).read_text('utf-8'))
print(f"VERSION_DIR={payload['version_dir']}")
print(f"CURRENT_LINK={payload['current_link']}")
for i, item in enumerate(payload.get('symlinks', []), start=1):
    print(f"LINK_{i}={item['path']}")
PY
  # shellcheck disable=SC1090
  . "$STATE_DIR/.bundle-uninstall.env"
  rm -f "$STATE_DIR/.bundle-uninstall.env"
  for name in ${!LINK_@}; do
    link="${!name}"
    if [ -L "$link" ]; then rm -f "$link"; fi
  done
  if [ -L "$CURRENT_LINK" ]; then rm -f "$CURRENT_LINK"; fi
  if [ -d "$VERSION_DIR" ]; then rm -rf "$VERSION_DIR"; fi
  rm -f "$MANIFEST"
  rmdir "$STATE_DIR" 2>/dev/null || true
  echo "uninstalled mcp-app-viewer $VERSION"
}

status_bundle() {
  echo "bundle version : $VERSION"
  if [ -f "$MANIFEST" ]; then
    python3 - "$MANIFEST" <<'PY'
import json, pathlib, sys
payload = json.loads(pathlib.Path(sys.argv[1]).read_text('utf-8'))
print(f"installed      : yes ({payload.get('version','unknown')})")
print(f"version_dir    : {payload.get('version_dir','')}")
for item in payload.get('symlinks', []):
    print(f"symlink        : {item['path']} -> {item['target']}")
PY
  else
    echo "installed      : no"
  fi
}

case "${1:-status}" in
  install|update)
    install_bundle
    ;;
  uninstall)
    uninstall_bundle
    ;;
  status)
    status_bundle
    ;;
  version)
    echo "$VERSION"
    ;;
  -h|--help|help)
    usage
    ;;
  *)
    usage >&2
    exit 2
    ;;
esac
