"""Lichtmuster fuer BITTs Koerpersprache. Reine Funktionen ohne ROS, damit man sie ohne Auto testen kann.

Ein Muster ist eine Funktion der Zeit seit Beginn des Zustands und liefert ein `Frame`:
4 Frontlampen (je RGB) + 4 Rueckleisten x 8 Pixel (je RGB), Blinkmodus, Hupe an/aus.
Modi wie in car_interfaces/msg/LightsCmd: 0 solid, 1 Blinker links, 2 Blinker rechts, 3 Warnblinker.
"""
import math
from dataclasses import dataclass

MODE_SOLID, MODE_LEFT, MODE_RIGHT, MODE_HAZARD = 0, 1, 2, 3
WHITE, RED, AMBER, BLUE, OFF = (255, 255, 255), (255, 0, 0), (255, 120, 0), (0, 80, 255), (0, 0, 0)
REAR_PIXELS = 32                      # 4 Leisten x 8 Pixel, von links nach rechts

STATES = ('idle', 'listening', 'thinking', 'speaking', 'driving', 'refuse', 'arrived', 'warn')
TRANSIENT = {'refuse': 2.0, 'arrived': 2.0, 'warn': 2.0}   # danach automatisch zurueck zu idle
STEER_THRESHOLD = 0.05                # rad: ab hier blinkt BITT in Fahrtrichtung


@dataclass
class Frame:
    front: list          # 12 Werte (4 Lampen x RGB)
    rear: list           # 96 Werte (32 Pixel x RGB)
    mode: int = MODE_SOLID
    horn: bool = False


def scale(rgb, b):
    return tuple(int(c * max(0.0, min(1.0, b))) for c in rgb)


def solid_front(rgb):
    return [c for _ in range(4) for c in rgb]


def solid_rear(rgb):
    return [c for _ in range(REAR_PIXELS) for c in rgb]


def kitt(t, speed=0.8, width=2.5, rgb=RED):
    """Lauflicht wie KITT: ein heller Punkt wandert ueber alle 32 Rueckpixel hin und her.

    speed = Hin-und-her-Zyklen pro Sekunde, width = Breite des Lichtpunkts in Pixeln.
    """
    pos = (math.sin(t * speed * 2 * math.pi) + 1) / 2 * (REAR_PIXELS - 1)
    rear = []
    for i in range(REAR_PIXELS):
        rear += scale(rgb, 1 - abs(i - pos) / width)
    return rear


def in_windows(t, windows):
    return any(a <= t < b for a, b in windows)


def frame(state, t, steer=0.0):
    """Frame fuer `state`, `t` Sekunden nachdem der Zustand begonnen hat. `steer` = Lenkwinkel in rad (+ links)."""
    if state == 'listening':          # vorne blau, pulsiert: "ich hoere zu"
        return Frame(solid_front(scale(BLUE, 0.35 + 0.65 * (math.sin(t * 2 * math.pi) + 1) / 2)), solid_rear(scale(RED, 0.2)))
    if state == 'thinking':           # schneller KITT-Scanner
        return Frame(solid_front(scale(WHITE, 0.2)), kitt(t, speed=1.5))
    if state == 'speaking':           # langsamer KITT-Scanner, vorne wach
        return Frame(solid_front(WHITE), kitt(t, speed=0.6))
    if state == 'driving':            # Scheinwerfer, Blinker in Lenkrichtung
        if steer > STEER_THRESHOLD:
            return Frame(solid_front(AMBER), solid_rear(AMBER), MODE_LEFT)
        if steer < -STEER_THRESHOLD:
            return Frame(solid_front(AMBER), solid_rear(AMBER), MODE_RIGHT)
        return Frame(solid_front(WHITE), solid_rear(scale(RED, 0.25)))
    if state == 'refuse':             # Warnblinker = Augenrollen
        return Frame(solid_front(AMBER), solid_rear(AMBER), MODE_HAZARD)
    if state == 'arrived':            # zweimal Lichthupe + zweimal kurz hupen = "Tada!"
        flash = in_windows(t, [(0.0, 0.2), (0.4, 0.6)])
        return Frame(solid_front(WHITE if flash else OFF), solid_rear(scale(RED, 0.25)),
                     horn=in_windows(t, [(0.0, 0.15), (0.3, 0.45)]))
    if state == 'warn':               # Warnblinker + einmal lang hupen
        return Frame(solid_front(AMBER), solid_rear(AMBER), MODE_HAZARD, horn=t < 0.5)
    # idle (und alles Unbekannte): ruhig, vorne gedimmt, hinten dunkelrot
    return Frame(solid_front(scale(WHITE, 0.25)), solid_rear(scale(RED, 0.15)))


def effective_state(state, t):
    """Kurze Zustaende (refuse, arrived, warn) fallen nach ihrer Dauer auf idle zurueck."""
    if state in TRANSIENT and t > TRANSIENT[state]:
        return 'idle'
    return state


def bar(rear):
    """Die 32 Rueckpixel als Textbalken fuers Terminal (Debug ohne Auto)."""
    chars = ' .:-=+*#%@'
    out = ''
    for i in range(REAR_PIXELS):
        b = max(rear[3 * i:3 * i + 3]) / 255
        out += chars[min(len(chars) - 1, int(b * (len(chars) - 1)))]
        if i % 8 == 7 and i < REAR_PIXELS - 1:
            out += '|'
    return f'[{out}]'
