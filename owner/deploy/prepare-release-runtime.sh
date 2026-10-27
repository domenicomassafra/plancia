#!/usr/bin/env bash
set -euo pipefail

# Bind one immutable code release to a dependency environment keyed by the
# pinned requirements file. A symlink rollback therefore restores both code and
# the Python dependency set instead of reusing one mutable global venv.

release_root=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)
runtime_root=${CONTROLCENTER_RUNTIME_ROOT:-"${XDG_DATA_HOME:-$HOME/.local/share}/controlcenter"}
venv_root="$runtime_root/dashboard-venvs"
requirements="$release_root/requirements.txt"
python_bin=${PYTHON_BIN:-python3}
check_only=false

if [[ ${1:-} == "--check" ]]; then
  check_only=true
  shift
fi
if (($#)); then
  echo "usage: $0 [--check]" >&2
  exit 64
fi

web_dist="$release_root/../web/dist"
web_index="$web_dist/index.html"

if [[ ! -s "$web_index" ]]; then
  echo "missing built web bundle: $web_index" >&2
  exit 67
fi
if grep -qE '/src/main\.(tsx|ts|jsx|js)' "$web_index"; then
  echo "web bundle is an unbuilt development index: $web_index" >&2
  exit 67
fi
asset_refs=$(grep -oE '/assets/[^"[:space:]]+' "$web_index" | sort -u || true)
if [[ -z "$asset_refs" ]] || ! grep -qE '/assets/[^"[:space:]]+\.js($|[?#])' <<<"$asset_refs"; then
  echo "built web index references no javascript asset: $web_index" >&2
  exit 67
fi
while IFS= read -r asset_ref; do
  asset_path=${asset_ref%%\?*}
  asset_path=${asset_path%%\#*}
  if [[ ! -s "$web_dist$asset_path" ]]; then
    echo "built web index references missing asset: $asset_ref" >&2
    exit 67
  fi
done <<<"$asset_refs"

if [[ ! -s "$requirements" ]]; then
  echo "missing pinned requirements: $requirements" >&2
  exit 66
fi

if [[ -f "$release_root/frontend.json" ]]; then
  frontend_sha=$("$python_bin" - "$release_root" <<'PY'
import hashlib, json, pathlib, re, sys
root = pathlib.Path(sys.argv[1])
source = json.loads((root / "frontend.json").read_text())
revision = (root / "FRONTEND_SOURCE_SHA").read_text().strip()
assert re.fullmatch(r"[0-9a-f]{40}", revision) and revision == source["revision"], "frontend source mismatch"
assert hashlib.sha256((root / "frontend-check.mjs").read_bytes()).hexdigest() == source["checker_sha256"], "frontend checker mismatch"
print(revision)
PY
  )
  frontend_check=$(mktemp)
  if ! node "$release_root/frontend-check.mjs" --dist "$web_dist" --source-sha "$frontend_sha" > "$frontend_check"; then
    rm -f "$frontend_check"
    exit 67
  fi
  if ! cmp -s "$frontend_check" "$release_root/FRONTEND_ASSETS.txt"; then
    rm -f "$frontend_check"
    echo 'frontend artifact differs from its Mac Studio attestation' >&2
    exit 67
  fi
  rm -f "$frontend_check"
fi

if $check_only; then
  echo "release_preflight=ok"
  echo "web_bundle=$web_index"
  exit 0
fi

requirements_sha=$(sha256sum "$requirements" | awk '{print $1}')
venv="$venv_root/$requirements_sha"
install -d -m 700 "$venv_root"

if [[ ! -x "$venv/bin/python" ]]; then
  tmp="$venv_root/.${requirements_sha}.tmp.$$"
  trap 'rm -rf -- "$tmp"' EXIT
  rm -rf -- "$tmp"
  "$python_bin" -m venv "$tmp"
  "$tmp/bin/python" -m pip install --disable-pip-version-check -r "$requirements"
  "$tmp/bin/python" -m pip check
  if [[ -e "$venv" ]]; then
    rm -rf -- "$tmp"
  else
    mv -- "$tmp" "$venv"
  fi
  trap - EXIT
fi

"$venv/bin/python" - <<'PY'
import fastapi
import httpx
import pydantic
import uvicorn

print(
    "runtime",
    f"fastapi={fastapi.__version__}",
    f"httpx={httpx.__version__}",
    f"pydantic={pydantic.__version__}",
    f"uvicorn={uvicorn.__version__}",
)
PY

ln -sfn "$venv" "$release_root/.venv.next"
mv -Tf "$release_root/.venv.next" "$release_root/.venv"
printf '%s\n' "$requirements_sha" >"$release_root/REQUIREMENTS.sha256"
chmod 600 "$release_root/REQUIREMENTS.sha256"

echo "release_runtime=$venv"
