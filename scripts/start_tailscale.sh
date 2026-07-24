#!/usr/bin/bash
# optional remote access — never block openpilot boot

TS_ROOT="/data/tailscale"
TS_BIN="$TS_ROOT/bin/tailscaled"
TS_STATE="$TS_ROOT/tailscaled.state"
TS_SOCKET="$TS_ROOT/tailscaled.sock"

# missing install is fine
if [ ! -x "$TS_BIN" ]; then
  exit 0
fi

# already running
if pgrep -x tailscaled >/dev/null 2>&1; then
  exit 0
fi

mkdir -p "$TS_ROOT" || true
"$TS_BIN" --state="$TS_STATE" --socket="$TS_SOCKET" >/dev/null 2>&1 &
exit 0
