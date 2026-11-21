"""Tests fuer die Parkgeometrie, laufen ohne ROS:  python3 -m pytest student_ws/src/bitt_voice/test -q"""
import math
import os
import sys

import numpy as np

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))
from bitt_voice.geometry import (
    Grid,
    bearing_from_pixel,
    choose_goal,
    footprint_free,
    goal_for_relation,
    raycast,
)


def world_with_box(bx, by, size=0.6, res=0.1, extent=20.0):
    """Leere 20x20-m-Karte um (0, 0) mit einem quadratischen Hindernis (Kosten 100) um (bx, by)."""
    n = int(extent / res)
    data = np.zeros((n, n), dtype=np.int8)
    g = Grid(data, res, -extent / 2, -extent / 2)
    for row in range(n):
        for col in range(n):
            x = g.origin_x + (col + 0.5) * res
            y = g.origin_y + (row + 0.5) * res
            if abs(x - bx) <= size / 2 and abs(y - by) <= size / 2:
                data[row, col] = 100
    return g


def test_bearing_sign():
    assert bearing_from_pixel(341.7, 552.0, 341.7) == 0.0
    assert bearing_from_pixel(100, 552.0, 341.7) > 0      # links im Bild = positiv
    assert bearing_from_pixel(600, 552.0, 341.7) < 0


def test_raycast_hits_front_edge():
    g = world_with_box(5.0, 0.0)
    hit = raycast(g, 0.0, 0.0, 0.0)
    assert hit is not None
    assert 4.6 <= hit[0] <= 4.8 and abs(hit[1]) < 0.1


def test_raycast_misses():
    g = world_with_box(5.0, 3.0)
    assert raycast(g, 0.0, 0.0, 0.0) is None


def test_relations_geometry():
    px, py = 5.0, 0.0
    b = goal_for_relation(px, py, 0.0, 'behind')
    left = goal_for_relation(px, py, 0.0, 'left')
    right = goal_for_relation(px, py, 0.0, 'right')
    front = goal_for_relation(px, py, 0.0, 'front')
    assert b[0] < px - 1.05                     # Front bleibt vor dem Objekt
    assert left[1] > 1.0 and right[1] < -1.0    # links ist +y
    assert front[0] - 0.22 > px + 0.6           # Heck hinter dem Objekt


def test_choose_goal_ok_for_all_relations():
    g = world_with_box(5.3, 0.0)
    hit = raycast(g, 0.92, 0.0, 0.0)
    for rel in ('behind', 'left', 'right', 'front'):
        ok, goal, why = choose_goal(g, 0.0, 0.0, hit[0], hit[1], 0.0, rel)
        assert ok, (rel, why)
        assert footprint_free(g, *goal)


def test_too_close_is_rejected():
    g = world_with_box(2.0, 0.0)
    hit = raycast(g, 0.92, 0.0, 0.0)
    ok, _goal, why = choose_goal(g, 0.0, 0.0, hit[0], hit[1], 0.0, "behind")
    assert not ok and 'zu nah' in why


def test_blocked_side_is_shifted_or_rejected():
    g = world_with_box(5.3, 0.0)
    # zweites Hindernis genau dort, wo "links" landen wuerde
    lx, ly, _ = goal_for_relation(5.0, 0.0, 0.0, 'left')
    for row in range(g.data.shape[0]):
        for col in range(g.data.shape[1]):
            x = g.origin_x + (col + 0.5) * g.resolution
            y = g.origin_y + (row + 0.5) * g.resolution
            if abs(x - (lx + 0.4)) < 0.3 and abs(y - ly) < 0.3:
                g.data[row, col] = 100
    ok, goal, why = choose_goal(g, 0.0, 0.0, 5.0, 0.0, 0.0, 'left')
    if ok:
        assert goal[0] > lx                        # nach vorne verschoben
    else:
        assert why


def test_diagonal_object():
    angle = math.radians(20)
    bx, by = 6 * math.cos(angle), 6 * math.sin(angle)
    g = world_with_box(bx, by)
    hit = raycast(g, 0.92, 0.0, math.atan2(by, bx - 0.92))
    assert hit is not None
    ok, _goal, why = choose_goal(g, 0.0, 0.0, hit[0], hit[1], math.atan2(by, bx - 0.92), 'right')
    assert ok, why


def test_heading_keeps_current_direction():
    # Objekt 18 Grad rechts, Auto faehrt geradeaus (heading 0): Ziel links vom Objekt, Blick geradeaus
    angle = math.radians(-18)
    px, py = 3.3 * math.cos(angle) + 0.92, 3.3 * math.sin(angle)
    x, y, yaw = goal_for_relation(px, py, angle, 'left', heading=0.0)
    assert yaw == 0.0
    assert y > py + 1.0          # links vom Objekt, gemessen quer zur Fahrtrichtung


def test_s_curve_length_grows_with_offset():
    from bitt_voice.geometry import s_curve_length
    assert s_curve_length(0.0) == 0.0
    assert 3.0 < s_curve_length(1.2) < 3.6          # 1,2 m seitlich braucht gut 3 m nach vorne
    assert s_curve_length(10.0) == math.inf


def test_reachable_rejects_sharp_sideways_goal():
    from bitt_voice.geometry import reachable
    assert reachable(0, 0, 0, 5.0, 0.2, 0.0)[0]       # fast geradeaus: ok
    assert not reachable(0, 0, 0, 2.5, 1.5, 0.0)[0]   # 1,5 m seitlich auf 2,5 m: zu eng


def test_choose_goal_shifts_forward_when_too_tight():
    # Objekt nah und seitlich: "left" waere zu eng, also schiebt choose_goal das Ziel nach vorne
    g = world_with_box(3.6, 1.0)
    ok, goal, why = choose_goal(g, 0.0, 0.0, 3.3, 1.0, math.atan2(1.0, 3.3 - 0.92), 'left', heading=0.0)
    if ok:
        from bitt_voice.geometry import reachable
        assert reachable(0, 0, 0, goal[0], goal[1], goal[2])[0]
    else:
        assert 'Wenderadius' in why or 'nah' in why or 'frei' in why


def test_choose_goal_behind_too_tight_is_honest():
    g = world_with_box(3.4, 1.6)
    ok, _goal, why = choose_goal(g, 0.0, 0.0, 3.1, 1.6, math.atan2(1.6, 3.1 - 0.92), 'behind', heading=0.0)
    assert not ok and ('Wenderadius' in why or 'nah' in why)
