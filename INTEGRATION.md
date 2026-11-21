# BITT: alles zusammenführen und am Auto starten

Nichts von uns läuft auf dem Auto. Alles läuft auf unseren Laptops und redet per WLAN mit BITT.
Sim und Auto unterscheiden sich für uns nur im Startbefehl: `tools/start_sim.sh` oder `tools/start_car.sh`.

## Wer hat was

| Wer | Ordner im Repo | Läuft auf | Was es tut |
| --- | --- | --- | --- |
| Du | `student_ws/src/bitt_voice` | Dus Windows-Laptop (Dev-Container) | HTTP-Bridge Port 8770: Bild, Objekte orten, parken, stoppen, Zustände |
| D | `student_ws/src/bitt_body` | Dus Laptop (gleicher Container) | Lichter + Hupe aus `/bitt/state` (KITT-Lauflicht, Blinker, Warnblinker) |
| E | ihr Ordner für die Sprach-App, z. B. `copilot/` + `tools/bitt_client.py` | Es Mac | Sprache rein/raus (OpenAI), ruft die Bridge über `bitt_client.py` |
| E oder D | `tools/threat_watch.py` | Es Mac | Pistole erkannt: Stopp, Warnblinker, Hupe, Ansage |
| alle | `tools/start_sim.sh`, `start_car.sh`, `check.sh`, `stop_all.sh` | Dus Laptop | alles mit einem Befehl starten, prüfen, beenden |

Jeder arbeitet nur in seinem Ordner. Deshalb gibt es beim Zusammenführen keine Konflikte.

## 1. Code zusammenführen (einmal, ca. 15 min)

**Du** (Container, Repo-Ordner, `bitt_team_pack_v2.zip` liegt im Hauptordner):

```bash
python3 -m zipfile -e bitt_team_pack_v2.zip /tmp/pack
rm -rf student_ws/src/bitt_voice student_ws/src/bitt_body
cp -r /tmp/pack/bitt_voice /tmp/pack/bitt_body student_ws/src/
mkdir -p tools && cp /tmp/pack/tools/* tools/ && chmod +x tools/*.sh && cp /tmp/pack/INTEGRATION.md .
rm -rf bitt_team_pack_v2.zip /tmp/pack
cd student_ws && colcon build && cd ..
python3 -m pytest student_ws/src/bitt_voice/test student_ws/src/bitt_body/test -q      # 18 passed
git add student_ws/src tools INTEGRATION.md
git commit -m "Bridge, Lichter, Waffenerkennung, Start-Skripte, Client fuer Sprach-App"
git push
```

**E** (Mac):

```bash
git pull
git checkout -b e/voice
# Sprach-App in ihren Ordner legen, bitt_client.py daneben kopieren oder importieren (siehe unten)
git add copilot && git commit -m "Sprach-App mit BITT-Tools" && git push -u origin e/voice
```

Auf GitHub einen Pull Request `e/voice` nach `main` öffnen, Du merged. D macht dasselbe mit `d/body`, falls nötig.

### Was E in ihrer App ändert (ca. 30 min)

`tools/bitt_client.py` enthält alles Fertige: Tool-Definitionen für OpenAI, den Ausführer, die Stopp-Erkennung,
die Bilderkennung und einen System-Prompt mit BITTs Persönlichkeit.

```python
import sys; sys.path.insert(0, '../tools')          # Pfad zu tools/ anpassen
from bitt_client import (TOOLS_REALTIME, TOOLS_CHAT, SYSTEM_PROMPT,
                         handle_tool_call, is_stop_command, stop, set_mood)

# Realtime-API: session.update -> "instructions": SYSTEM_PROMPT, "tools": TOOLS_REALTIME
# chat.completions: messages=[{"role": "system", "content": SYSTEM_PROMPT}, ...], tools=TOOLS_CHAT

# wenn das Modell ein Tool aufruft:
ergebnis = handle_tool_call(name, argumente)        # JSON-String als Tool-Antwort zurückgeben

# Sprache erkannt, BEVOR sie zum Modell geht:
if is_stop_command(text):
    stop()                                          # sofort, ohne KI

# Lichter passend zur App:
set_mood('listening')   # beim Zuhören
set_mood('thinking')    # während das Modell rechnet
set_mood('speaking')    # während BITT spricht
set_mood('idle')        # danach
```

Umgebung auf dem Mac: `BRIDGE_URL=http://<IP von Dus Laptop>:8770`, `OPENAI_API_KEY`, und
`OPENAI_VISION_MODEL` auf das Bildmodell, das E schon nutzt. Selbsttest ohne App: `python3 tools/bitt_client.py look`.

**Rohe Endpoints**, falls E lieber selbst aufruft:

| Wenn BITT … | Aufruf | Body |
| --- | --- | --- |
| etwas sehen soll | `GET /snapshot` → Bild an die Vision-KI → `POST /locate` | `{"objects":[{"label":"BMW","u":410}]}` (`u` = Pixelspalte der Objektmitte) |
| parken soll | `POST /park`, dann alle 0,5 s `GET /status` bis `reached` oder `failed` | `{"label":"BMW","relation":"behind"}` (`behind`, `left`, `right`, `front`) |
| stoppen soll | `POST /stop`, **ohne** vorher die KI zu fragen | `{}` |
| Stimmung zeigen | `POST /state` | `{"state":"listening"}` (`thinking`, `speaking`, `idle`, `refuse`, `arrived`, `warn`) |

`/park` antwortet mit `{"ok": false, "reason": "..."}`, wenn es nicht geht (zu nah, Wenderadius, kein Platz).
Den Grund soll BITT in eigenen Worten sagen.

## 2. Netz am Auto (einmal)

1. Dus Laptop und Es Mac ins WLAN `BITT`.
2. Es Mac braucht Internet für OpenAI: iPhone per **USB** anschließen, persönlichen Hotspot an. Das WLAN bleibt bei BITT.
3. Dus Laptop: In `C:\Users\<du>\.wslconfig` muss stehen:
   ```ini
   [wsl2]
   networkingMode=mirrored
   memory=8GB
   ```
   Danach in PowerShell `wsl --shutdown` und den Container neu öffnen.
4. Dus Laptop, PowerShell **als Administrator** (damit der Mac an Port 8770 kommt):
   ```powershell
   Set-NetFirewallHyperVVMSetting -Name '{40E0AC32-46A5-438A-A0B2-2B479E8F2E90}' -DefaultInboundAction Allow
   New-NetFirewallRule -DisplayName "BITT Bridge" -Direction Inbound -Protocol TCP -LocalPort 8770 -Action Allow
   ```
5. IP von Dus Laptop: `start_car.sh` bzw. `start_sim.sh` zeigt sie am Ende an (sonst PowerShell `ipconfig`, WLAN-Adapter `10.42.0.x`).
6. Auf Es Mac: `export BRIDGE_URL=http://10.42.0.x:8770` und testen: `curl $BRIDGE_URL/status`.

## 3. Am Auto starten (jedes Mal)

Im Dev-Container auf Dus Laptop:

```bash
tools/stop_all.sh                 # falls noch eine Sim läuft
tools/start_car.sh                # Verbindungscheck, Bridge, Lichter, zeigt IP und Foxglove-Adresse
scripts/set_car_time.py           # nur wenn start_car.sh eine Uhrabweichung meldet, danach 2 min warten
tools/check.sh                    # Status, Kamera, Bild (snap.jpg), Lichter
tools/check.sh park BMW 320 left  # Objekt bei Bildspalte 320 orten und links daneben parken
```

Dann auf Es Mac die Sprach-App und `python3 tools/threat_watch.py` starten.
Foxglove: `ws://10.42.0.1:8765`, Layout `foxglove/bmw_bitt.layout.json`, im Navigation-Panel `/bitt/markers` einschalten.
Kartons **4–5 m** vor das Auto stellen. Näher geht `behind` nicht (BITT sagt dann „zu nah“).

## 4. In der Sim üben

```bash
tools/start_sim.sh                # Szenario i8_parking_lot; andere: tools/start_sim.sh i8_street
tools/check.sh
tools/check.sh park Auto 520 left
tools/stop_all.sh                 # IMMER zum Beenden, sonst bleibt Gazebo hängen
```

Foxglove: `ws://localhost:8765`. Logs: `/tmp/bitt_logs/sim.log`, `bridge.log`, `body.log`.
Es Mac kann auch gegen die Sim testen: `BRIDGE_URL=http://<IP von Dus Laptop>:8770` im selben WLAN.

Wenn `start_sim.sh` nach 3 Minuten keine Uhr meldet: `tools/stop_all.sh`, Ladekabel dran, andere Programme zu, nochmal.

## Not-Aus

1. Die Fernbedienung gewinnt immer. Immer in der Hand halten.
2. `curl -s -X POST localhost:8770/stop` oder `scripts/cancel_navigation.py`.
3. „Stopp!“ in Es App geht direkt an `/stop`.
