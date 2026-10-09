#!/usr/bin/env bash
# 构建前后端合一镜像，并导出可搬到 NAS 上 docker load 的归档。
set -euo pipefail

project_dir="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)"
image_ref="${1:-media-music-service:$(date +%Y%m%d-%H%M%S)}"
output_dir="${2:-${project_dir}/artifacts}"
mkdir -p -- "$output_dir"

build_args=()
if [[ -n "${WEB_BUILD_IMAGE:-}" ]]; then
  build_args+=(--build-arg "WEB_BUILD_IMAGE=${WEB_BUILD_IMAGE}")
fi
docker build "${build_args[@]}" -t "$image_ref" "$project_dir"

image_arch="$(docker image inspect --format '{{.Architecture}}' "$image_ref")"
archive_name="${image_ref//\//-}"
archive_name="${archive_name//:/-}-linux-${image_arch}.tar.gz"
archive_path="${output_dir}/${archive_name}"
temporary_path="${archive_path}.tmp"
trap 'rm -f -- "$temporary_path"' EXIT
docker image save "$image_ref" | gzip -1 > "$temporary_path"
mv -f -- "$temporary_path" "$archive_path"
(cd -- "$output_dir" && sha256sum "$archive_name" > "${archive_name}.sha256")
printf '镜像：%s\n归档：%s\n校验：%s.sha256\n' "$image_ref" "$archive_path" "$archive_path"
