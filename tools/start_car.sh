#!/usr/bin/env bash
# Startet unsere Knoten fuer das ECHTE Auto: Verbindung pruefen, Bridge, Lichter. Logs: /tmp/bitt_logs/*.log
#   tools/start_car.sh
# Vorher: Laptop im WLAN "BITT", Fernbedienung in der Hand. Ende: tools/stop_all.sh

ROOT=$(cd "$(dirname "$0")/.." && pwd)
LOG=/tmp/bitt_logs
mkdir -p "$LOG"

pkill -9 -f "bitt_voice bridge" 2>/dev/null; pkill -9 -f lib/bitt_voice/bridge 2>/dev/null
pkill -9 -f "bitt_body body" 2>/dev/null; pkill -9 -f lib/bitt_body/body 2>/dev/null
if pgrep -f gz-sim >/dev/null; then
    echo "Die Sim laeuft noch. Erst tools/stop_all.sh, dann nochmal."
    exit 1
fi

# shellcheck disable=SC1091
source /opt/ros/lyrical/setup.bash
# shellcheck disable=SC1091
source /opt/bitt_ws/install/setup.bash
# shellcheck disable=SC1091
source "$ROOT/student_ws/install/setup.bash"
# shellcheck disable=SC1091
source "$ROOT/scripts/bitt_env.sh" live

if ! ping -c 1 -W 2 "${BITT_IP:-10.42.0.1}" >/dev/null 2>&1; then
    echo "Das Auto (${BITT_IP:-10.42.0.1}) antwortet nicht auf ping. WLAN 'BITT'? Siehe TROUBLESHOOTING.md."
    exit 1
fi

echo "Verbindungscheck (scripts/doctor.py) ..."
python3 "$ROOT/scripts/doctor.py" --seconds 4 | tee "$LOG/doctor.log" | grep -E "FAIL|warn|clock|ready" || true
if grep -q "FAIL" "$LOG/doctor.log"; then
    echo "doctor meldet FAIL, siehe oben und TROUBLESHOOTING.md. Ich starte trotzdem weiter."
fi
if grep -qiE "(FAIL|warn).*clock" "$LOG/doctor.log"; then
    echo "Die Autouhr weicht ab: scripts/set_car_time.py ausfuehren und 2 Minuten warten."
fi

echo "Bridge und Lichter starten ..."
setsid nohup ros2 run bitt_voice bridge > "$LOG/bridge.log" 2>&1 < /dev/null &
setsid nohup ros2 run bitt_body body > "$LOG/body.log" 2>&1 < /dev/null &

for _ in $(seq 1 20); do
    code=$(curl -s -o /dev/null -w '%{http_code}' localhost:8770/status)
    [ "$code" = 200 ] && break
    sleep 1
done
[ "$code" != 200 ] && { echo "Bridge antwortet nicht:"; tail -15 "$LOG/bridge.log"; exit 1; }

ip=$(hostname -I 2>/dev/null | awk '{print $1}')
cat <<MSG

==== BITT am Auto bereit ====
Foxglove:   ws://10.42.0.1:8765
Bridge:     http://localhost:8770   von Es Mac: http://${ip:-<IP dieses Laptops>}:8770
Testen:     tools/check.sh          (Kartons 4-5 m vor das Auto)
Not-Aus:    Fernbedienung, oder curl -s -X POST localhost:8770/stop
Beenden:    tools/stop_all.sh
MSG
