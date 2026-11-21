#!/usr/bin/env python3
"""BITT erkennt, wenn eine Person eine (Spielzeug-)Pistole haelt, und reagiert: stoppt, Warnblinker, hupt, spricht.

Laeuft auf einem Laptop mit Internet (z. B. Es Mac), neben der Sprach-App. Braucht nur die Bridge (Port 8770).

    pip install openai requests
    export OPENAI_API_KEY=...                       # derselbe Key wie in Es Sprach-App
    export BRIDGE_URL=http://<IP von Dus Laptop>:8770
    export BITT_BEIFAHRER=Bruno                     # Name des Plueschtiers fuer die Ansage (optional)
    python3 threat_watch.py                         # Dauerbetrieb, prueft alle 2 s
    python3 threat_watch.py --once                  # ein Bild pruefen und Ergebnis zeigen (zum Testen)
    python3 threat_watch.py --dry                   # erkennen und ausgeben, aber nicht reagieren

Ablauf pro Runde:  GET /snapshot  ->  Vision-Modell: "Haelt jemand eine Pistole?"  ->  bei Ja:
POST /stop (Nav2 abbrechen), POST /state warn (Warnblinker + Hupe ueber bitt_body), Sprachausgabe.
Danach 15 s Pause, damit BITT nicht dauernd hupt.

Hinweis: Die Frontkamera ist schwarz-weiss. Die Pistole deutlich sichtbar halten, 2-4 m vor dem Auto,
nicht vor dem Koerper verstecken. Vorher mit --once testen.
"""
import argparse
import base64
import json
import os
import shutil
import subprocess
import time

import requests
from openai import OpenAI

BRIDGE = os.environ.get('BRIDGE_URL', 'http://localhost:8770').rstrip('/')
MODEL = os.environ.get('OPENAI_VISION_MODEL', 'gpt-4o-mini')   # auf das Modell stellen, das E fuer Vision nutzt
PROMPT = (
    'Das ist ein Schwarz-Weiss-Bild der Frontkamera eines kleinen Roboterautos. '
    'Haelt eine Person eine Pistole oder Spielzeugpistole in der Hand (auch auf das Auto gerichtet)? '
    'Antworte nur als JSON: {"gun": true/false, "confidence": 0.0-1.0, "where": "links|mitte|rechts|-", '
    '"what": "kurze Beschreibung auf Deutsch"}'
)
BEIFAHRER = os.environ.get('BITT_BEIFAHRER', 'mein Beifahrer')   # Name des Pluschtiers, z. B. export BITT_BEIFAHRER=Bruno
SAY_TEXT = os.environ.get('BITT_THREAT_SAY',
                          f'Achtung, Waffe erkannt! Ich bleibe stehen. Pistole runter, {BEIFAHRER} hat Angst.')


def speak(text):
    if shutil.which('say'):                       # macOS
        subprocess.Popen(['say', '-v', 'Anna', text])
    else:
        print(f'BITT: {text}')


def check(client, jpeg):
    image = 'data:image/jpeg;base64,' + base64.b64encode(jpeg).decode()
    resp = client.chat.completions.create(
        model=MODEL,
        response_format={'type': 'json_object'},
        messages=[{'role': 'user', 'content': [
            {'type': 'text', 'text': PROMPT},
            {'type': 'image_url', 'image_url': {'url': image}},
        ]}],
    )
    return json.loads(resp.choices[0].message.content)


def react():
    for path, body in (('/stop', {}), ('/state', {'state': 'warn'})):
        try:
            requests.post(BRIDGE + path, json=body, timeout=2)
        except requests.RequestException as e:
            print(f'Bridge {path} nicht erreichbar: {e}')
    speak(SAY_TEXT)


def main():
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument('--once', action='store_true', help='nur ein Bild pruefen')
    p.add_argument('--dry', action='store_true', help='nicht reagieren, nur ausgeben')
    p.add_argument('--period', type=float, default=2.0, help='Sekunden zwischen zwei Pruefungen')
    p.add_argument('--threshold', type=float, default=0.6, help='ab dieser Sicherheit reagieren')
    p.add_argument('--cooldown', type=float, default=15.0, help='Pause nach einer Reaktion (s)')
    args = p.parse_args()

    client = OpenAI()
    print(f'Bridge {BRIDGE}, Modell {MODEL}')
    while True:
        try:
            jpeg = requests.get(BRIDGE + '/snapshot', timeout=3).content
            result = check(client, jpeg)
        except Exception as e:   # Netz weg, Bridge weg, Modell meckert: weitermachen
            print(f'Fehler: {e}')
            if args.once:
                return
            time.sleep(args.period)
            continue
        hit = bool(result.get('gun')) and float(result.get('confidence', 0)) >= args.threshold
        print(time.strftime('%H:%M:%S'), 'WAFFE' if hit else 'ok   ', result)
        if args.once:
            return
        if hit and not args.dry:
            react()
            time.sleep(args.cooldown)
        time.sleep(args.period)


if __name__ == '__main__':
    main()
