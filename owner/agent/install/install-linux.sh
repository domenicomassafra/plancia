#!/usr/bin/env bash
set -euo pipefail

if [[ $# -lt 1 || $# -gt 2 ]]; then
  echo "usage: $0 <host-id> [token-file]" >&2
  exit 64
fi

host_id=$1
token_source=${2:-}
case "$host_id" in
  minipc|matebooklinux|macbook-air) ;;
  *) echo "unsupported Linux fleet host id: $host_id" >&2; exit 64 ;;
esac

script_dir=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)
source_agent=$(cd -- "$script_dir/.." && pwd)/fleetprobe.py
agent_source_dir=$(dirname "$source_agent")
agent_modules=(fleetprobe.py fleetprobe_support.py fleetprobe_linux.py fleetprobe_darwin.py fleetprobe_windows.py)
data_root="${XDG_DATA_HOME:-$HOME/.local/share}/controlcenter"
state_root="${XDG_STATE_HOME:-$HOME/.local/state}/controlcenter"
config_root="${XDG_CONFIG_HOME:-$HOME/.config}/controlcenter"
agent_dir="$data_root/fleetprobe"
log_dir="$state_root/fleetprobe"
secret_dir="$config_root/secrets"
token_path="$secret_dir/fleetprobe-token"
unit_dir="$HOME/.config/systemd/user"
unit_path="$unit_dir/fleetprobe.service"

install -d -m 700 "$agent_dir" "$log_dir" "$secret_dir" "$unit_dir"
for module in "${agent_modules[@]}"; do
  test -f "$agent_source_dir/$module" || { echo "missing FleetProbe module: $module" >&2; exit 66; }
done
for module in "${agent_modules[@]}"; do
  install -m 700 "$agent_source_dir/$module" "$agent_dir/$module"
done
if [[ -n "$token_source" ]]; then
  install -m 600 "$token_source" "$token_path"
elif [[ ! -s "$token_path" ]]; then
  echo "missing token file: $token_path" >&2
  exit 66
fi

cat >"$unit_path" <<EOF
[Unit]
Description=Signal Deck fleet telemetry agent
After=network-online.target tailscaled.service

[Service]
Type=simple
ExecStart=/usr/bin/python3 $agent_dir/fleetprobe.py --serve --host-id $host_id --bind auto --token-file $token_path
Restart=always
RestartSec=10
Nice=10
MemoryMax=128M
CPUQuota=15%
NoNewPrivileges=true
PrivateTmp=true

[Install]
WantedBy=default.target
EOF
chmod 600 "$unit_path"

systemctl --user daemon-reload
# ``enable --now`` does not restart an already-active unit after replacing the
# executable/unit file. Always restart so the running process actually matches
# the just-installed host identity and agent revision.
systemctl --user enable fleetprobe.service >/dev/null
systemctl --user restart fleetprobe.service
systemctl --user is-active --quiet fleetprobe.service
echo "fleetprobe host=$host_id state=active"
