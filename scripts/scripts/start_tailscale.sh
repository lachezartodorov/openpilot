# optional remote access — never block openpilot boot
if [ -x /data/openpilot/scripts/start_tailscale.sh ]; then
  /data/openpilot/scripts/start_tailscale.sh || true
fi
