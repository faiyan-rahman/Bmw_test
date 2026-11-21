#!/usr/bin/env bash
# Startet die komplette Sim mit einem Befehl: aufraeumen, Gazebo + Nav2, unsere Bridge, unsere Lichter.
#   tools/start_sim.sh                  # Szenario i8_parking_lot
#   tools/start_sim.sh i8_street        # anderes Szenario (i8_plaza, i8_street, ...)
# Alles laeuft im Hintergrund weiter, auch wenn du das Terminal schliesst. Logs: /tmp/bitt_logs/*.log
# Danach: Foxglove ws://localhost:8765, Test mit tools/check.sh, Ende mit tools/stop_all.sh
# Laeuft im isolierten Replay-Netz (Domain 77): die Sim kann das echte Auto nie erreichen.

ROOT=$(cd "$(dirname "$0")/.." && pwd)
SCENARIO=${1:-i8_parking_lot}
LOG=/tmp/bitt_logs
mkdir -p "$LOG"

if [ ! -f "$ROOT/student_ws/install/setup.bash" ]; then
    echo "student_ws ist nicht gebaut. Erst: cd student_ws && colcon build && cd .."
    exit 1
fi

echo "1/4 Aufraeumen ..."
"$ROOT/tools/stop_all.sh" >/dev/null 2>&1

# Software-Rendering: mit der WSL-GPU haengt Gazebo beim Kamera-Rendern (Erfahrung von Freitagnacht).
export BITT_RENDER=${BITT_RENDER:-software}
# shellcheck disable=SC1091
source /etc/bitt/gpu-env.sh
# shellcheck disable=SC1091
source /opt/ros/lyrical/setup.bash
# shellcheck disable=SC1091
source /opt/bitt_ws/install/setup.bash
# shellcheck disable=SC1091
source /opt/bitt_sim_ws/install/setup.bash
# shellcheck disable=SC1091
source "$ROOT/student_ws/install/setup.bash"
# shellcheck disable=SC1091
source "$ROOT/scripts/bitt_env.sh" replay >/dev/null

echo "2/4 Sim starten: $SCENARIO (Rendering: $BITT_RENDER_MODE) ..."
setsid nohup ros2 launch bitt_sim sim.launch.py scenario:="$SCENARIO" > "$LOG/sim.log" 2>&1 < /dev/null &

printf "    warte auf Gazebo "
ok=0
for _ in $(seq 1 30); do
    if grep -q "not found in" "$LOG/sim.log" 2>/dev/null; then
        echo; grep "not found in" "$LOG/sim.log" | tail -1
        echo "Szenario-Namen: siehe 'Available' oben, ohne .sdf (z. B. i8_parking_lot)."
        exit 1
    fi
    if timeout 4 ros2 topic echo --once /clock >/dev/null 2>&1; then ok=1; break; fi
    printf "."
    sleep 2
done
echo
if [ "$ok" != 1 ]; then
    echo "Gazebo liefert nach 3 Minuten keine Uhr (/clock). Letzte Log-Zeilen:"
    tail -15 "$LOG/sim.log"
    echo "Tipp: tools/stop_all.sh, Ladekabel dran, andere Programme zu, dann nochmal."
    exit 1
fi

printf "3/4 warte auf Nav2 "
for _ in $(seq 1 45); do
    if ros2 action list 2>/dev/null | grep -q navigate_to_pose; then break; fi
    printf "."
    sleep 2
done
echo

echo "4/4 Bridge und Lichter starten ..."
setsid nohup ros2 run bitt_voice bridge --ros-args -p use_sim_time:=true \
    -p camera_topic:=/visual_slam/image_0 -p camera_raw:=true > "$LOG/bridge.log" 2>&1 < /dev/null &
setsid nohup ros2 run bitt_body body > "$LOG/body.log" 2>&1 < /dev/null &

for _ in $(seq 1 20); do
    code=$(curl -s -o /dev/null -w '%{http_code}' localhost:8770/status)
    [ "$code" = 200 ] && break
    sleep 1
done
if [ "$code" != 200 ]; then
    echo "Bridge antwortet nicht. Log:"; tail -15 "$LOG/bridge.log"; exit 1
fi

ip=$(hostname -I 2>/dev/null | awk '{print $1}')
cat <<MSG

==== BITT-Sim laeuft ====
Foxglove:   ws://localhost:8765   (Layout bmw_bitt.layout.json, /bitt/markers einschalten)
Bridge:     http://localhost:8770   von Es Mac: http://${ip:-<IP dieses Laptops>}:8770
Testen:     tools/check.sh
Logs:       tail -f $LOG/sim.log   (bridge.log, body.log)
Beenden:    tools/stop_all.sh
Eigene ros2-Befehle in einem Terminal erst nach: source scripts/bitt_env.sh replay
MSG
