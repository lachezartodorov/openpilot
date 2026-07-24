# Tailscale Boot Hook Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Start on-device `/data/tailscale/bin/tailscaled` from OpenPilot boot without blocking launch.

**Architecture:** Replace the incorrect `scripts/start_tailscale.sh` with a real daemon starter. Call it optionally from `launch_chffrplus.sh` before manager starts, ignoring failures.

**Tech Stack:** Bash launch scripts on AGNOS / Comma 3; Tailscale binary already installed at `/data/tailscale/bin/tailscaled`.

## Global Constraints

- Never block or fail OpenPilot boot because of Tailscale
- Do not run `tailscale up` in this iteration
- Do not add Tailscale to `process_config.py` / manager
- Do not ship Tailscale binaries in the openpilot repo
- Prefer Tailscale state under `/data/tailscale/`

---

## File Structure

- `scripts/start_tailscale.sh` — start `tailscaled` if present and not already running
- `launch_chffrplus.sh` — optional non-blocking boot hook
- Remove mistaken nested `scripts/scripts/start_tailscale.sh` if present

---

### Task 1: Real `scripts/start_tailscale.sh`

**Files:**
- Create/Replace: `scripts/start_tailscale.sh`
- Delete if present: `scripts/scripts/start_tailscale.sh`

**Interfaces:**
- Consumes: `/data/tailscale/bin/tailscaled` on device (optional)
- Produces: background `tailscaled` process when binary exists; exit 0 always for missing/already-running cases

- [x] **Step 1: Write `scripts/start_tailscale.sh`**

```bash
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
```

- [x] **Step 2: Make executable and remove nested stub**

```bash
chmod +x scripts/start_tailscale.sh
rm -f scripts/scripts/start_tailscale.sh
rmdir scripts/scripts 2>/dev/null || true
```

- [x] **Step 3: Smoke-check script syntax**

Run: `bash -n scripts/start_tailscale.sh`
Expected: no output, exit 0

- [ ] **Step 4: Commit** (deferred — wait for explicit user request)

```bash
git add scripts/start_tailscale.sh
git add -u scripts/scripts/start_tailscale.sh 2>/dev/null || true
git commit -m "Add Tailscale daemon start script for Comma 3."
```

---

### Task 2: Boot hook in `launch_chffrplus.sh`

**Files:**
- Modify: `launch_chffrplus.sh` (after `agnos_init`, before manager start)

**Interfaces:**
- Consumes: `$DIR/scripts/start_tailscale.sh` from Task 1
- Produces: optional Tailscale start on every OpenPilot launch

- [x] **Step 1: Insert non-blocking hook after hardware init**

After the `agnos_init` block (around the `# write tmux scrollback` section), insert:

```bash
  # optional remote access — never block openpilot boot
  if [ -x "$DIR/scripts/start_tailscale.sh" ]; then
    "$DIR/scripts/start_tailscale.sh" || true
  fi
```

- [x] **Step 2: Verify hook is present and syntax-ok**

Run: `bash -n launch_chffrplus.sh && grep -n start_tailscale launch_chffrplus.sh`
Expected: syntax OK; one hook reference

- [ ] **Step 3: Commit** (deferred — wait for explicit user request)

```bash
git add launch_chffrplus.sh
git commit -m "Start Tailscale from openpilot launch on boot."
```

---

### Task 3: Device verification notes

**Files:** none (manual on Comma 3 after deploy)

- [ ] **Step 1: Deploy and reboot device**
- [ ] **Step 2: Confirm OpenPilot boots**
- [ ] **Step 3: Confirm daemon:** `pgrep -a tailscaled`
- [ ] **Step 4: Failure path:** hide/rename binary, reboot, confirm OpenPilot still boots
