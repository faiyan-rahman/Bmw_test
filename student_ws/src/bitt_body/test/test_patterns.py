"""Tests fuer die Lichtmuster, ohne ROS:  python3 -m pytest student_ws/src/bitt_body/test -q"""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))
from bitt_body.patterns import (MODE_HAZARD, MODE_LEFT, MODE_RIGHT, STATES,  # noqa: E402
                                effective_state, frame, kitt)


def test_every_state_has_valid_sizes():
    for s in STATES:
        for t in (0.0, 0.1, 0.33, 1.0, 1.9):
            f = frame(s, t)
            assert len(f.front) == 12 and len(f.rear) == 96
            assert all(0 <= v <= 255 for v in f.front + f.rear)


def test_kitt_moves():
    peak = lambda r: max(range(32), key=lambda i: r[3 * i])  # noqa: E731
    assert peak(kitt(0.0)) != peak(kitt(0.3))


def test_indicator_follows_steering():
    assert frame('driving', 0, steer=0.2).mode == MODE_LEFT
    assert frame('driving', 0, steer=-0.2).mode == MODE_RIGHT
    assert frame('driving', 0, steer=0.0).mode == 0


def test_hazard_and_horn():
    assert frame('refuse', 0).mode == MODE_HAZARD and not frame('refuse', 0).horn
    assert frame('warn', 0.1).horn and not frame('warn', 0.8).horn
    arrived = [frame('arrived', t / 100).horn for t in range(0, 60)]
    assert sum(1 for a, b in zip(arrived, arrived[1:]) if b and not a) == 1   # zweiter Hupton startet bei 0.3
    assert frame('arrived', 0.05).horn and frame('arrived', 0.35).horn and not frame('arrived', 0.2).horn


def test_transient_states_return_to_idle():
    assert effective_state('arrived', 1.0) == 'arrived'
    assert effective_state('arrived', 2.5) == 'idle'
    assert effective_state('speaking', 99) == 'speaking'
