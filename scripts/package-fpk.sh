#!/usr/bin/env bash
# Build the fnOS gateway launcher; the music service stays in its existing container.
set -euo pipefail

project_dir="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)"
fnpack="${FNPACK:-${project_dir}/fnos-fpk/tools/fnpack}"
output_dir="${1:-${project_dir}/artifacts}"

if [[ "$fnpack" != */* ]]; then
  fnpack="$(command -v "$fnpack" || true)"
fi
if [[ ! -x "$fnpack" || -d "$fnpack" ]]; then
  printf '找不到 fnpack：%s\n请准备 fnos-fpk/tools/fnpack，或通过 FNPACK 指定工具路径。\n' "$fnpack" >&2
  exit 1
fi
fnpack="$(cd -- "$(dirname -- "$fnpack")" && pwd)/$(basename -- "$fnpack")"
mkdir -p -- "$output_dir"
output_dir="$(cd -- "$output_dir" && pwd)"
staging="$(mktemp -d "$output_dir/.fpk-build.XXXXXX")"
trap 'rm -rf -- "$staging"' EXIT

mkdir -p "$staging/source"
cp -a "$project_dir/fnos-fpk/fpk/." "$staging/source/"
# fnpack requires the directory even when the application has no wizard.
mkdir -p "$staging/source/wizard"
chmod +x "$staging/source/cmd/"*
# Local Python checks must not leak interpreter caches into the installable app.
find "$staging/source" -type d -name __pycache__ -prune -exec rm -rf -- {} +
(cd -- "$staging" && "$fnpack" build -d "$staging/source")
test -s "$staging/MUSICASSISTANT.fpk"
(cd -- "$staging" && sha256sum MUSICASSISTANT.fpk > MUSICASSISTANT.fpk.sha256)
mv -f -- "$staging/MUSICASSISTANT.fpk" "$output_dir/MUSICASSISTANT.fpk"
mv -f -- "$staging/MUSICASSISTANT.fpk.sha256" "$output_dir/MUSICASSISTANT.fpk.sha256"
printf '安装包：%s/MUSICASSISTANT.fpk\n校验：%s/MUSICASSISTANT.fpk.sha256\n' "$output_dir" "$output_dir"
