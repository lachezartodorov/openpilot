# Tailscale boot hook (Comma 3)

## Goal

Start the on-device Tailscale daemon automatically when OpenPilot boots, without blocking OpenPilot or making boot depend on Tailscale success.

## Context

- Device: Comma 3 (AGNOS)
- Tailscale binary on device: `/data/tailscale/bin/tailscaled`
- OpenPilot boot path: `/data/continue.sh` → `launch_openpilot.sh` → `launch_chffrplus.sh` → `system/manager/manager.py`
- Current repo stub at `scripts/start_tailscale.sh` contains a self-calling boot-hook snippet (incorrect). A mistaken nested copy existed at `scripts/scripts/start_tailscale.sh`.

## Approach

Hook from `launch_chffrplus.sh` (not process manager, not a separate systemd unit).

## Components

### 1. Boot hook in `launch_chffrplus.sh`

Near the start of `launch`, before manager starts:

- If `scripts/start_tailscale.sh` exists and is executable, run it
- Ignore failures (`|| true`) so OpenPilot boot never depends on Tailscale

### 2. `scripts/start_tailscale.sh`

Real start script checked into the openpilot repo:

- Exit quietly if `/data/tailscale/bin/tailscaled` is missing
- No-op if `tailscaled` is already running
- Otherwise start `/data/tailscale/bin/tailscaled` in the background
- Prefer state under `/data/tailscale/` when the daemon needs a state directory
- Do **not** run `tailscale up` in this iteration
- Never fail hard; safe to call from boot

### 3. Cleanup

- Replace the incorrect self-calling content in `scripts/start_tailscale.sh`
- Remove the mistaken nested path `scripts/scripts/start_tailscale.sh` if still present

## Error handling

- Missing Tailscale install → silent skip
- Start failure → ignored by launch hook; OpenPilot continues
- Already running → skip duplicate start

## Testing (on device)

1. Reboot Comma 3
2. Confirm OpenPilot boots normally
3. Confirm `tailscaled` is running (e.g. `pgrep -a tailscaled`)
4. Temporarily rename/hide Tailscale binary and reboot; confirm OpenPilot still boots

## Out of scope

- `tailscale up` / auth keys / login flow
- systemd unit outside openpilot
- OpenPilot process-manager supervision/restart of Tailscale
- Shipping Tailscale binaries inside the openpilot repo
