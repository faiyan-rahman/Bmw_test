#!/usr/bin/env bash
# Prueft die laufende Bridge (Sim oder Auto) in 20 Sekunden und sagt, was geht und was nicht.
#   tools/check.sh                              # Grundcheck: Status, Kamera, Bild, Lichter
#   tools/check.sh park Auto 520 left           # zusaetzlich: Objekt bei Bildspalte 520 orten und links daneben parken
# Das Bild landet in snap.jpg im Repo-Ordner (in VS Code anklicken, um die Spalte u zu schaetzen; Breite meist 640).

ROOT=$(cd "$(dirname "$0")/.." && pwd)
B=${BRIDGE_URL:-http://localhost:8770}
pass=0; fail=0
ok()  { echo "  [ ok ] $*"; pass=$((pass + 1)); }
bad() { echo "  [FAIL] $*"; fail=$((fail + 1)); }

echo "Bridge: $B"
s=$(curl -s -m 3 "$B/status") && ok "Bridge antwortet: $s" || { bad "Bridge antwortet nicht (laeuft tools/start_sim.sh bzw. start_car.sh?)"; exit 1; }

ci=$(curl -s -m 3 -w ' %{http_code}' "$B/camera_info")
[ "${ci##* }" = 200 ] && ok "Kamera-Kalibrierung: ${ci% *}" || bad "keine camera_info (Kamera/Pipeline aus?)"

code=$(curl -s -m 5 -o "$ROOT/snap.jpg" -w '%{http_code}' "$B/snapshot")
if [ "$code" = 200 ] && [ -s "$ROOT/snap.jpg" ]; then ok "Kamerabild gespeichert: snap.jpg ($(stat -c %s "$ROOT/snap.jpg") Bytes)"
else bad "kein Kamerabild (Sim: camera_raw:=true gesetzt? Auto: /preview/left/compressed da?)"; fi

for st in speaking arrived idle; do
    r=$(curl -s -m 3 -X POST "$B/state" -d "{\"state\":\"$st\"}")
    echo "$r" | grep -q '"ok": true' && ok "Zustand $st gesendet (Lichter: /tmp/bitt_logs/body.log bzw. am Auto schauen)" || bad "Zustand $st: $r"
    sleep 1.5
done

if [ "${1:-}" = park ]; then
    label=${2:-Auto}; u=${3:-320}; rel=${4:-left}
    echo "Parken: $label bei u=$u, Relation $rel"
    r=$(curl -s -m 5 -X POST "$B/locate" -d "{\"objects\":[{\"label\":\"$label\",\"u\":$u}]}")
    echo "  locate: $r"
    echo "$r" | grep -q '"found": true' || { bad "Objekt nicht in der Costmap (andere Spalte probieren / naeher ran)"; exit 1; }
    r=$(curl -s -m 5 -X POST "$B/park" -d "{\"label\":\"$label\",\"relation\":\"$rel\"}")
    echo "  park:   $r"
    echo "$r" | grep -q '"ok": true' || { bad "park abgelehnt (Grund steht oben)"; exit 1; }
    for _ in $(seq 1 120); do
        st=$(curl -s -m 3 "$B/status")
        case "$st" in
            *reached*) ok "angekommen: $st"; break ;;
            *failed*|*cancelled*) bad "nicht angekommen: $st"; break ;;
        esac
        sleep 1
    done
fi
echo "Ergebnis: $pass ok, $fail FAIL"
[ "$fail" = 0 ]
