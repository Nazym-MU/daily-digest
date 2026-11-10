#!/bin/sh
# Installs listener.py as a launchd agent: starts at login, restarts if it dies.
# Re-run after moving the repo or changing python. Uninstall:
#   launchctl bootout gui/$(id -u)/space.nazym.digest-listener
set -e
REPO="$(cd "$(dirname "$0")" && pwd)"
PY="$(command -v python3)"
CLAUDE="$(command -v claude)"
LABEL=space.nazym.digest-listener
PLIST="$HOME/Library/LaunchAgents/$LABEL.plist"
LOG="$HOME/Library/Logs/digest-listener.log"

[ -f "$REPO/.env" ] || { echo "Create $REPO/.env first (copy .env.example)."; exit 1; }

cat > "$PLIST" <<PL
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0"><dict>
  <key>Label</key><string>$LABEL</string>
  <key>ProgramArguments</key><array><string>$PY</string><string>-u</string><string>$REPO/listener.py</string></array>
  <key>WorkingDirectory</key><string>$REPO</string>
  <key>EnvironmentVariables</key><dict>
    <key>PATH</key><string>$(dirname "$CLAUDE"):/usr/local/bin:/usr/bin:/bin</string>
    <key>CLAUDE_BIN</key><string>$CLAUDE</string>
  </dict>
  <key>RunAtLoad</key><true/>
  <key>KeepAlive</key><true/>
  <key>ThrottleInterval</key><integer>30</integer>
  <key>StandardOutPath</key><string>$LOG</string>
  <key>StandardErrorPath</key><string>$LOG</string>
</dict></plist>
PL

launchctl bootout "gui/$(id -u)/$LABEL" 2>/dev/null || true
launchctl bootstrap "gui/$(id -u)" "$PLIST"
echo "Installed. Logs: tail -f $LOG"
