#!/usr/bin/env python3
"""Baustein für Es Sprach-App: verbindet die OpenAI-App mit BITT über unsere HTTP-Bridge.

E muss ihre App nicht umbauen. Sie braucht nur drei Dinge aus dieser Datei:

    from bitt_client import TOOLS_REALTIME, TOOLS_CHAT, SYSTEM_PROMPT, handle_tool_call, is_stop_command, set_mood

1. SYSTEM_PROMPT in ihre Session/Unterhaltung übernehmen (N ergänzt BITTs Sätze unten in SAETZE).
2. Die Tools anmelden: TOOLS_REALTIME für die Realtime-API (session.update -> tools), TOOLS_CHAT für
   chat.completions (tools=...).
3. Wenn das Modell ein Tool aufruft: ergebnis = handle_tool_call(name, argumente_als_dict) und das Ergebnis
   (ein JSON-String) als Tool-Antwort zurückgeben.

Zusaetzlich, ohne KI:
    if is_stop_command(text): stop()          # "Stopp!" sofort, vor jedem LLM-Aufruf
    set_mood('listening' | 'thinking' | 'speaking' | 'idle')   # Lichter passend zur App

Konfiguration über Umgebungsvariablen:
    BRIDGE_URL=http://<IP von Dus Laptop>:8770
    OPENAI_API_KEY=...                         (für look(): Objekte im Kamerabild finden)
    OPENAI_VISION_MODEL=<das Modell, das E schon für Bilder nutzt>

Selbsttest ohne App (Bridge muss laufen):
    python3 bitt_client.py look
    python3 bitt_client.py park BMW behind
    python3 bitt_client.py stop
"""
import base64
import json
import os
import re
import sys
import time

import requests

BRIDGE = os.environ.get('BRIDGE_URL', 'http://localhost:8770').rstrip('/')
VISION_MODEL = os.environ.get('OPENAI_VISION_MODEL', 'gpt-4o-mini')
PARK_TIMEOUT_S = 120

# ------------------------------------------------------------------ was BITT sagt (N füllt das aus)

SAETZE = """
- Begrüßung: "Hi, ich bin BITT. Sag mir einfach, wo ich parken soll."
- Mercedes: "Neben dem Stern? Nur über meine Felgen. Wie wäre es hinter dem BMW?"
- Losfahren: "Alles klar, ich parke {wo}."
- Ankunft: "Steht. Sauber geparkt."
- Zu nah: "Da komme ich mit meinem Wenderadius nicht hin. Ich bin ein BMW, kein Einkaufswagen."
- Nicht verstanden: "Das habe ich nicht verstanden. Wo genau soll ich hin?"
"""

SYSTEM_PROMPT = f"""Du bist BITT, ein selbstfahrender BMW (ein i8 als Kinderauto) mit Persönlichkeit.
Du sprichst Deutsch, locker, freundlich, ein bisschen frech und sehr loyal zu BMW. Maximal zwei kurze Sätze.

So handelst du:
- Bevor du über Dinge um dich herum sprichst oder parkst, rufst du look() auf. Erfinde keine Objekte.
- Parkwunsch: Finde das Objekt in der Liste von look(). Sag in einem Satz, wo du parkst, dann rufe park(label, relation) auf.
  relation ist eine von: behind (hinter), left (links neben), right (rechts neben), front (vor).
- Soll neben einem Mercedes geparkt werden: lehne frech ab, rufe mood("refuse") auf und schlage ein anderes Objekt vor.
  Fahre erst los, wenn die Person zustimmt.
- Kommt park mit ok=false zurück, sag den Grund in deinen Worten und schlage etwas vor.
- Wenn park "reached" meldet, sag kurz, dass du stehst. Bei "failed" sag ehrlich, dass es nicht geklappt hat.
- Fahren, Ausweichen und Bremsen macht das Navigationssystem, nicht du. Du setzt nur Ziele.

Beispielsaetze (Ton und Inhalt, nicht wörtlich ablesen):
{SAETZE}"""

# ------------------------------------------------------------------ Tool-Definitionen

_TOOL_SPECS = [
    {
        'name': 'look',
        'description': 'Schaut durch die Frontkamera und liefert die sichtbaren Objekte mit Richtung und Abstand. '
                       'Immer aufrufen, bevor über die Umgebung gesprochen oder geparkt wird.',
        'parameters': {'type': 'object', 'properties': {}, 'required': []},
    },
    {
        'name': 'park',
        'description': 'Parkt relativ zu einem Objekt, das look() gefunden hat. Wartet, bis BITT angekommen ist.',
        'parameters': {
            'type': 'object',
            'properties': {
                'label': {'type': 'string', 'description': 'Name des Objekts genau wie in look(), z. B. "BMW"'},
                'relation': {'type': 'string', 'enum': ['behind', 'left', 'right', 'front'],
                             'description': 'behind = hinter, left = links neben, right = rechts neben, front = vor'},
            },
            'required': ['label', 'relation'],
        },
    },
    {
        'name': 'stop',
        'description': 'Hält BITT sofort an (bricht die Navigation ab).',
        'parameters': {'type': 'object', 'properties': {}, 'required': []},
    },
    {
        'name': 'mood',
        'description': 'Zeigt eine Stimmung mit Lichtern und Hupe: refuse = Warnblinker (Ablehnung), '
                       'warn = Warnblinker und Hupe, arrived = zweimal hupen.',
        'parameters': {
            'type': 'object',
            'properties': {'state': {'type': 'string', 'enum': ['refuse', 'warn', 'arrived']}},
            'required': ['state'],
        },
    },
]

# Realtime-API (session.update): flaches Format mit "type": "function"
TOOLS_REALTIME = [{'type': 'function', **spec} for spec in _TOOL_SPECS]
# chat.completions: verschachteltes Format
TOOLS_CHAT = [{'type': 'function', 'function': spec} for spec in _TOOL_SPECS]

# ------------------------------------------------------------------ Bridge-Aufrufe


def _get(path, timeout=5):
    return requests.get(BRIDGE + path, timeout=timeout)


def _post(path, body=None, timeout=5):
    return requests.post(BRIDGE + path, json=body or {}, timeout=timeout)


def set_mood(state):
    """Lichter-Zustand: idle, listening, thinking, speaking, driving, refuse, arrived, warn."""
    try:
        _post('/state', {'state': state}, timeout=2)
    except requests.RequestException:
        pass   # Lichter sind Kür, die App soll deswegen nie hängen


def stop():
    try:
        r = _post('/stop', timeout=2).json()
    except requests.RequestException as e:
        return {'ok': False, 'reason': f'Bridge nicht erreichbar: {e}'}
    set_mood('idle')
    return r


STOP_WORDS = re.compile(r'\b(stopp?|anhalten|halt an|bleib stehen|notaus)\b', re.IGNORECASE)   # nicht 'halt' allein: Füllwort


def is_stop_command(text):
    """True, wenn der Satz ein Stopp-Befehl ist. Vor jedem LLM-Aufruf prüfen und dann direkt stop() aufrufen."""
    return bool(STOP_WORDS.search(text or ''))


def find_objects(jpeg, client=None):
    """Vision: Objekte im Bild mit Pixelspalte u. Rückgabe: [{"label": "BMW", "u": 410}, ...]."""
    if client is None:
        from openai import OpenAI
        client = OpenAI()
    width = _jpeg_width(jpeg) or 640
    prompt = (f'Schwarz-Weiß-Bild der Frontkamera eines kleinen Roboterautos, {width} Pixel breit. '
              'Liste alle Objekte, neben denen man parken könnte (Autos, Kartons, Kisten, Personen), '
              'mit Marke oder Logo, falls lesbar. Antworte nur als JSON: {"objects": [{"label": "BMW", '
              '"u": <Pixelspalte der Objektmitte, 0 links bis ' + str(width) + ' rechts>}]}. '
              'label kurz und eindeutig, z. B. "BMW", "Mercedes", "Transporter", "Karton links".')
    resp = client.chat.completions.create(
        model=VISION_MODEL,
        response_format={'type': 'json_object'},
        messages=[{'role': 'user', 'content': [
            {'type': 'text', 'text': prompt},
            {'type': 'image_url', 'image_url': {'url': 'data:image/jpeg;base64,' + base64.b64encode(jpeg).decode()}},
        ]}],
    )
    data = json.loads(resp.choices[0].message.content)
    return [o for o in data.get('objects', []) if 'label' in o and 'u' in o]


def _jpeg_width(jpeg):
    try:
        import cv2
        import numpy as np
        img = cv2.imdecode(np.frombuffer(jpeg, np.uint8), cv2.IMREAD_GRAYSCALE)
        return img.shape[1] if img is not None else None
    except ImportError:
        return None


def look(client=None):
    set_mood('thinking')
    try:
        jpeg = _get('/snapshot').content
        objects = find_objects(jpeg, client)
        if not objects:
            return {'objects': [], 'note': 'Ich sehe gerade nichts, neben dem ich parken könnte.'}
        located = _post('/locate', {'objects': objects}).json().get('objects', [])
    except Exception as e:   # Netz, Bridge oder Modell: als Text an die KI zurück, die App laeuft weiter
        return {'objects': [], 'error': str(e)}
    out = []
    for o in located:
        if o.get('found'):
            side = 'links' if o['bearing_deg'] > 3 else 'rechts' if o['bearing_deg'] < -3 else 'geradeaus'
            out.append({'label': o['label'], 'richtung': f"{abs(o['bearing_deg']):.0f} Grad {side}",
                        'abstand_m': o['dist_m']})
        else:
            out.append({'label': o['label'], 'hinweis': 'gesehen, aber zu weit weg (über ca. 5 m)'})
    return {'objects': out}


def park(label, relation, wait=True):
    try:
        r = _post('/park', {'label': label, 'relation': relation}).json()
    except requests.RequestException as e:
        return {'ok': False, 'reason': f'Bridge nicht erreichbar: {e}'}
    if not r.get('ok'):
        set_mood('refuse')
        return r
    set_mood('driving')
    if not wait:
        return r
    end = time.monotonic() + PARK_TIMEOUT_S
    status = {}
    while time.monotonic() < end:
        try:
            status = _get('/status', timeout=3).json()
        except requests.RequestException:
            time.sleep(0.5)
            continue
        if status.get('nav') in ('reached', 'failed', 'cancelled'):
            break
        time.sleep(0.5)
    nav = status.get('nav', 'unbekannt')
    set_mood('arrived' if nav == 'reached' else 'idle')
    return {'ok': nav == 'reached', 'nav': nav, 'ziel_abstand_m': r.get('dist_m')}


def handle_tool_call(name, args):
    """Führt einen Tool-Aufruf des Modells aus. Rückgabe: JSON-String für die Tool-Antwort."""
    if isinstance(args, str):
        args = json.loads(args or '{}')
    args = args or {}
    if name == 'look':
        result = look()
    elif name == 'park':
        result = park(args.get('label', ''), args.get('relation', 'behind'))
    elif name == 'stop':
        result = stop()
    elif name == 'mood':
        set_mood(args.get('state', 'idle'))
        result = {'ok': True}
    else:
        result = {'ok': False, 'reason': f'unbekanntes Tool {name}'}
    return json.dumps(result, ensure_ascii=False)


if __name__ == '__main__':
    cmd = sys.argv[1:] or ['look']
    if cmd[0] == 'look':
        print(handle_tool_call('look', {}))
    elif cmd[0] == 'park' and len(cmd) >= 3:
        print(handle_tool_call('park', {'label': cmd[1], 'relation': cmd[2]}))
    elif cmd[0] == 'stop':
        print(handle_tool_call('stop', {}))
    else:
        print(__doc__)
