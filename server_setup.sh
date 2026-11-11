#!/usr/bin/env bash
# =============================================================================
# server_setup.sh - one-time setup of the AI Trading Tournament on an Ubuntu
# server (Google Cloud or any other).
#
# Run it from inside the folder that holds engine.py, main_dashboard.py,
# requirements.txt and the bot files:
#
#     bash server_setup.sh
#
# What it sets up:
#   * the engine, running 24/7 in the background and restarting by itself;
#   * the dashboard, on port 8501, with a password on its controls;
#   * automatic updates: if this folder came from GitHub ("git clone"), the
#     server checks GitHub every 2 minutes and picks up any changed file.
#
# It is safe to run again (for example to change the dashboard password).
# =============================================================================
set -e
DIR="$(cd "$(dirname "$0")" && pwd)"
USER="${USER:-$(id -un)}"
echo "Setting up the tournament in $DIR (user: $USER)"

for f in engine.py main_dashboard.py requirements.txt; do
  if [ ! -f "$DIR/$f" ]; then
    echo "ERROR: $f is missing from $DIR - upload it first."; exit 1
  fi
done

PW="${TOURNAMENT_PASSWORD:-}"
if [ -z "$PW" ]; then
  read -rsp "Choose a password for the dashboard controls (letters and numbers, 6+ characters): " PW; echo
fi
if [[ ! "$PW" =~ ^[A-Za-z0-9]{6,}$ ]]; then
  echo "ERROR: use at least 6 letters and/or numbers only (no spaces or symbols)."; exit 1
fi

echo "[1/6] Installing system packages..."
sudo apt-get update -y
sudo apt-get install -y python3-venv python3-pip curl git

echo "[2/6] Adding 2 GB of swap (extra memory for a small server)..."
if ! swapon --show | grep -q /swapfile; then
  sudo fallocate -l 2G /swapfile
  sudo chmod 600 /swapfile
  sudo mkswap /swapfile
  sudo swapon /swapfile
  grep -q '^/swapfile' /etc/fstab || echo '/swapfile none swap sw 0 0' | sudo tee -a /etc/fstab >/dev/null
else
  echo "      swap already set up"
fi

echo "[3/6] Installing Python libraries (this can take a few minutes)..."
python3 -m venv "$DIR/.venv"
"$DIR/.venv/bin/pip" install --upgrade pip -q
"$DIR/.venv/bin/pip" install -r "$DIR/requirements.txt" -q

echo "[4/6] Creating the background services..."
sudo tee /etc/systemd/system/tournament-engine.service >/dev/null <<EOF
[Unit]
Description=AI Trading Tournament engine
After=network-online.target
Wants=network-online.target

[Service]
User=$USER
WorkingDirectory=$DIR
ExecStart=$DIR/.venv/bin/python $DIR/engine.py
Restart=always
RestartSec=30

[Install]
WantedBy=multi-user.target
EOF

sudo tee /etc/systemd/system/tournament-dashboard.service >/dev/null <<EOF
[Unit]
Description=AI Trading Tournament dashboard
After=network-online.target tournament-engine.service
Wants=network-online.target

[Service]
User=$USER
WorkingDirectory=$DIR
Environment=TOURNAMENT_MODE=local
Environment=TOURNAMENT_AUTOSTART=0
Environment=TOURNAMENT_PASSWORD=$PW
ExecStart=$DIR/.venv/bin/streamlit run $DIR/main_dashboard.py --server.address 0.0.0.0 --server.port 8501 --server.headless true --browser.gatherUsageStats false
Restart=always
RestartSec=10

[Install]
WantedBy=multi-user.target
EOF
sudo chmod 600 /etc/systemd/system/tournament-dashboard.service   # keeps the password private

echo "[5/6] Setting up automatic updates from GitHub..."
if [ -d "$DIR/.git" ]; then
  sudo tee /usr/local/bin/tournament-update >/dev/null <<EOF
#!/usr/bin/env bash
# Fetches changed files from GitHub. Bot files are picked up by the running
# engine by itself; engine or dashboard changes restart the right service.
cd "$DIR" || exit 0
old=\$(runuser -u $USER -- git rev-parse HEAD 2>/dev/null) || exit 0
runuser -u $USER -- git pull -q --ff-only 2>/dev/null || exit 0
new=\$(runuser -u $USER -- git rev-parse HEAD)
[ "\$old" = "\$new" ] && exit 0
changed=\$(runuser -u $USER -- git diff --name-only "\$old" "\$new")
echo "Updated from GitHub: \$changed"
if echo "\$changed" | grep -qx "requirements.txt"; then
  runuser -u $USER -- "$DIR/.venv/bin/pip" install -q -r "$DIR/requirements.txt"
fi
if echo "\$changed" | grep -qxE "engine\.py|requirements\.txt"; then systemctl restart tournament-engine; fi
if echo "\$changed" | grep -qxE "engine\.py|main_dashboard\.py|requirements\.txt"; then systemctl restart tournament-dashboard; fi
EOF
  sudo chmod 755 /usr/local/bin/tournament-update
  sudo tee /etc/systemd/system/tournament-update.service >/dev/null <<EOF
[Unit]
Description=AI Trading Tournament: fetch updates from GitHub

[Service]
Type=oneshot
ExecStart=/usr/local/bin/tournament-update
EOF
  sudo tee /etc/systemd/system/tournament-update.timer >/dev/null <<EOF
[Unit]
Description=AI Trading Tournament: check GitHub every 2 minutes

[Timer]
OnBootSec=2min
OnUnitActiveSec=2min

[Install]
WantedBy=timers.target
EOF
  UPDATES=1
else
  echo "      this folder did not come from GitHub - skipping automatic updates"
  UPDATES=0
fi

echo "[6/6] Starting everything..."
sudo systemctl daemon-reload
sudo systemctl enable tournament-engine tournament-dashboard >/dev/null
sudo systemctl restart tournament-engine tournament-dashboard
if [ "$UPDATES" = "1" ]; then
  sudo systemctl enable --now tournament-update.timer >/dev/null
fi
sleep 8

for s in tournament-engine tournament-dashboard; do
  if systemctl is-active --quiet "$s"; then echo "   OK  $s is running"; else echo "   !!  $s is NOT running - see: journalctl -u $s -n 50"; fi
done
if [ "$UPDATES" = "1" ]; then echo "   OK  checking GitHub for updated files every 2 minutes"; fi

IP=$(curl -s -m 5 https://ifconfig.me || true)
echo
echo "All done. Open this in your browser:  http://${IP:-YOUR-SERVER-IP}:8501"
echo "The first engine cycle takes a minute or two (it downloads the stock list)."
