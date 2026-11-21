"""Reine Geometrie fuer "parke hinter / neben / vor Objekt X". Kein ROS, damit man es ohne Sim testen kann.

Koordinaten: alles im Frame `odom` (x, y in Metern, Winkel in rad, gegen den Uhrzeigersinn).
Das Auto: base_link = Mitte der Hinterachse, die Karosserie reicht von -0.22 m (hinten) bis +1.05 m (vorne).
"""
import math
from dataclasses import dataclass

import numpy as np

REAR_OVERHANG = 0.22   # base_link bis Heck (m), aus robot_config.yaml
FRONT_REACH = 1.05     # base_link bis Front (m)
HALF_WIDTH = 0.38      # halbe Fahrzeugbreite (m)


@dataclass
class Grid:
    """Das Noetigste aus einer nav_msgs/OccupancyGrid (Ursprung ohne Drehung, wie bei Nav2-Costmaps)."""
    data: np.ndarray        # (height, width), Werte -1 (unbekannt), 0..100
    resolution: float       # m pro Zelle
    origin_x: float         # Weltkoordinate der Ecke von Zelle (0, 0)
    origin_y: float

    @classmethod
    def from_msg(cls, msg):
        info = msg.info
        data = np.array(msg.data, dtype=np.int8).reshape(info.height, info.width)
        return cls(data, info.resolution, info.origin.position.x, info.origin.position.y)

    def cost(self, x, y):
        """Kosten an einem Weltpunkt, None ausserhalb der Karte."""
        col = int((x - self.origin_x) / self.resolution)
        row = int((y - self.origin_y) / self.resolution)
        if 0 <= row < self.data.shape[0] and 0 <= col < self.data.shape[1]:
            return int(self.data[row, col])
        return None


def yaw_of(q):
    """Gierwinkel aus einer Quaternion (x, y, z, w)."""
    return math.atan2(2 * (q.w * q.z + q.x * q.y), 1 - 2 * (q.y * q.y + q.z * q.z))


def bearing_from_pixel(u, fx, cx):
    """Bildspalte u -> Winkel zur Kamerachse in rad, positiv = links (Lochkameramodell)."""
    return math.atan2(cx - u, fx)


def raycast(grid, x0, y0, angle, max_range=8.0, threshold=100):
    """Laeuft vom Punkt (x0, y0) in Richtung `angle`, bis eine Zelle >= threshold ist.

    Gibt (x, y, Abstand) des ersten Hindernispunkts zurueck oder None.
    threshold 100 = nur echte Hindernisse (Nav2: 99 ist schon der Inflations-Saum am Fahrzeug).
    """
    step = grid.resolution / 2
    dx, dy = math.cos(angle), math.sin(angle)
    r = 0.0
    while r <= max_range:
        x, y = x0 + r * dx, y0 + r * dy
        c = grid.cost(x, y)
        if c is not None and c >= threshold:
            return x, y, r
        r += step
    return None


def goal_for_relation(px, py, angle, relation, depth=0.6, heading=None):
    """Zielpose (x, y, yaw) von base_link fuer eine Relation zum Objekt.

    (px, py) = Vorderkante des Objekts (Treffer des Raycasts), `angle` = Richtung vom Auto zum Objekt.
    `heading` = Blickrichtung des Autos am Ziel; Standard: die aktuelle Fahrtrichtung (vom Aufrufer),
    sonst `angle`. Gleiche Richtung wie jetzt ist fuer Ackermann am leichtesten zu fahren
    (kein S-Bogen). "hinter/neben/vor" wird relativ zu dieser Richtung gemessen.
      behind: zwischen uns und dem Objekt, Front ~0.85 m vor dem Objekt ("hinter dem BMW")
      left / right: neben dem Objekt, Fahrzeugmitte auf Hoehe der Objektmitte, 1.2 m seitlich
      front: hinter dem Objekt aus unserer Sicht, Heck ~0.5 m nach dem Objekt
    """
    yaw = angle if heading is None else heading
    ray = np.array([math.cos(angle), math.sin(angle)])   # Strahl vom Auto zum Objekt
    d = np.array([math.cos(yaw), math.sin(yaw)])         # Fahrtrichtung am Ziel
    n = np.array([-d[1], d[0]])                          # Normale nach links
    p = np.array([px, py])
    centre = p + depth / 2 * ray                         # Objektmitte (geschaetzt)
    car_mid = (FRONT_REACH - REAR_OVERHANG) / 2          # Fahrzeugmitte liegt 0.42 m vor base_link
    if relation == 'behind':
        g = p - (FRONT_REACH + 0.85) * d
    elif relation == 'left':
        g = centre + 1.2 * n - car_mid * d
    elif relation == 'right':
        g = centre - 1.2 * n - car_mid * d
    elif relation == 'front':
        g = p + (depth + 0.5 + REAR_OVERHANG) * d
    else:
        raise ValueError(f'unbekannte Relation {relation!r} (behind, left, right, front)')
    return float(g[0]), float(g[1]), yaw


def footprint_free(grid, gx, gy, yaw, max_cost=50):
    """Sind alle Costmap-Zellen unter der Karosserie (plus 10 cm Rand) guenstig genug?

    Unbekannte Zellen (-1) gelten als frei: die Sim-Tiefenkamera sieht nur 5 m weit.
    """
    c, s = math.cos(yaw), math.sin(yaw)
    for t in np.arange(-REAR_OVERHANG - 0.1, FRONT_REACH + 0.1 + 1e-9, grid.resolution):
        for lat in np.arange(-HALF_WIDTH - 0.1, HALF_WIDTH + 0.1 + 1e-9, grid.resolution):
            cost = grid.cost(gx + t * c - lat * s, gy + t * s + lat * c)
            if cost is not None and cost > max_cost:
                return False
    return True


TURN_RADIUS = 2.56     # m, minimaler Wenderadius aus nav2_params.yaml (minimum_turning_radius)
TURN_MARGIN = 1.2      # Sicherheitsfaktor: Nav2 faehrt nicht exakt auf dem kleinsten Radius


def s_curve_length(lateral, radius=TURN_RADIUS):
    """Wie viel Strecke nach vorne braucht ein Ackermann-Auto, um bei gleicher Blickrichtung `lateral` Meter
    seitlich zu versetzen? Zwei gleiche Kreisboegen (links, dann rechts): seitlich 2R(1-cos a), vorne 2R sin a."""
    lateral = abs(lateral)
    if lateral >= 2 * radius:
        return math.inf
    cos_a = 1 - lateral / (2 * radius)
    return 2 * radius * math.sqrt(max(0.0, 1 - cos_a * cos_a))


def reachable(car_x, car_y, car_yaw, gx, gy, gyaw, radius=TURN_RADIUS, margin=TURN_MARGIN):
    """Kann das Auto (vorwaerts, ohne Rangieren) die Zielpose erreichen? Rueckgabe: (ok, noetige Vorwaertsstrecke).

    Grob, aber ehrlich: nur fuer Ziele, die ungefaehr in Fahrtrichtung zeigen (so baut goal_for_relation sie).
    """
    c, s = math.cos(car_yaw), math.sin(car_yaw)
    dx, dy = gx - car_x, gy - car_y
    ahead = dx * c + dy * s                 # Strecke nach vorne im Fahrzeugrahmen
    side = -dx * s + dy * c                 # seitlicher Versatz
    turn = abs(math.atan2(math.sin(gyaw - car_yaw), math.cos(gyaw - car_yaw)))
    need = margin * s_curve_length(side, radius) + 0.3
    if turn > math.radians(30):             # Ziel stark gedreht: brauchen mindestens einen Bogen dafuer
        need = max(need, margin * radius * math.sin(turn))
    return ahead >= need, need


def choose_goal(grid, car_x, car_y, px, py, angle, relation, min_dist=2.0, depth=0.6, heading=None):
    """Zielpose suchen und pruefen. Rueckgabe: (ok, (x, y, yaw) oder None, Grund).

    Ist die erste Pose belegt oder mit dem Wenderadius nicht erreichbar, wird sie bis zu fuenfmal um 0.5 m
    verschoben (bei "behind" vom Objekt weg, sonst weiter am Objekt entlang nach vorne). Ziele naeher als
    `min_dist` lehnt Nav2 oft ab, deshalb sagen wir das gleich ehrlich. `heading` = aktuelle Fahrtrichtung
    des Autos; ohne sie wird die Erreichbarkeit nicht geprueft.
    """
    gx, gy, yaw = goal_for_relation(px, py, angle, relation, depth, heading)
    sign = -1.0 if relation == 'behind' else 1.0
    last = 'belegt'
    for i in range(6):
        x = gx + sign * 0.5 * i * math.cos(yaw)
        y = gy + sign * 0.5 * i * math.sin(yaw)
        if math.hypot(x - car_x, y - car_y) < min_dist:
            if last == 'lenkung':
                break
            return False, None, f'zu nah: das Ziel liegt unter {min_dist:.0f} m vor mir, ich brauche Anlauf'
        if heading is not None:
            ok, need = reachable(car_x, car_y, heading, x, y, yaw)
            if not ok:
                last = 'lenkung'
                if relation == 'behind':        # zurueckschieben macht es nur schlimmer
                    break
                continue
        if footprint_free(grid, x, y, yaw):
            return True, (x, y, yaw), ''
        last = 'belegt'
    if last == 'lenkung':
        return False, None, ('mit meinem Wenderadius komme ich da nicht sauber hin, '
                             'ich brauche mehr Anlauf oder das Ziel weiter vorne')
    return False, None, 'dort ist kein freier Platz'
