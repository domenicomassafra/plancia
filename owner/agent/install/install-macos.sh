#!/usr/bin/env bash
set -euo pipefail

if [[ $# -lt 1 || $# -gt 2 ]]; then
  echo "usage: $0 <host-id> [token-file]" >&2
  exit 64
fi

host_id=$1
token_source=${2:-}
case "$host_id" in
  mac-studio|macbook-air) ;;
  *) echo "unsupported macOS fleet host id: $host_id" >&2; exit 64 ;;
esac

script_dir=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)
source_agent=$(cd -- "$script_dir/.." && pwd)/fleetprobe.py
agent_source_dir=$(dirname "$source_agent")
agent_modules=(fleetprobe.py fleetprobe_support.py fleetprobe_linux.py fleetprobe_darwin.py fleetprobe_windows.py cos_orca_observer.py cos_orca_routes.py)
template="$script_dir/com.controlcenter.fleetprobe.plist"
data_root="${XDG_DATA_HOME:-$HOME/.local/share}/controlcenter"
state_root="${XDG_STATE_HOME:-$HOME/.local/state}/controlcenter"
config_root="${XDG_CONFIG_HOME:-$HOME/.config}/controlcenter"
agent_dir="$data_root/fleetprobe"
log_dir="$state_root/fleetprobe"
secret_dir="$config_root/secrets"
token_path="$secret_dir/fleetprobe-token"
launch_dir="$HOME/Library/LaunchAgents"
plist_path="$launch_dir/com.controlcenter.fleetprobe.plist"

install -d -m 700 "$agent_dir" "$log_dir" "$secret_dir" "$launch_dir"
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

sed -e "s|__DATA_DIR__|$agent_dir|g" -e "s|__STATE_DIR__|$log_dir|g" -e "s|__TOKEN_FILE__|$token_path|g" -e "s|__HOST_ID__|$host_id|g" "$template" >"$plist_path"
chmod 600 "$plist_path"
touch "$log_dir/fleetprobe.log"
chmod 600 "$log_dir/fleetprobe.log"

launchctl bootout "gui/$UID" "$plist_path" >/dev/null 2>&1 || true
launchctl bootstrap "gui/$UID" "$plist_path"
launchctl kickstart -k "gui/$UID/com.controlcenter.fleetprobe"
sleep 2
launchctl print "gui/$UID/com.controlcenter.fleetprobe" >/dev/null

echo "fleetprobe host=$host_id state=active"
