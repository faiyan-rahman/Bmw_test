#!/usr/bin/env python3
"""BITTs Koerpersprache: setzt Zustaende in Lichter und Hupe um.

    ros2 run bitt_body body                      # mit --ros-args -p debug_bar:=true zeigt er den Scanner im Terminal

Eingang:  /bitt/state   std_msgs/String  (idle, listening, thinking, speaking, driving, refuse, arrived, warn)
          /steer_state  car_interfaces/SteerState  (fuer den Blinker in Fahrtrichtung)
Ausgang:  /lights_cmd   car_interfaces/LightsCmd   10 Hz
          /horn_cmd     std_msgs/Bool              nur bei Aenderung

Nur dieser Knoten schreibt Lichter und Hupe. Test ohne Auto:
    ros2 topic pub --once /bitt/state std_msgs/String "data: thinking"
"""
import rclpy
from car_interfaces.msg import LightsCmd, SteerState
from rclpy.executors import ExternalShutdownException
from rclpy.node import Node
from rclpy.qos import QoSDurabilityPolicy, QoSProfile, ReliabilityPolicy
from std_msgs.msg import Bool, String

from bitt_body.patterns import STATES, bar, effective_state, frame


class Body(Node):
    def __init__(self):
        super().__init__('bitt_body')
        self.rate = self.declare_parameter('rate_hz', 10.0).value
        self.debug_bar = self.declare_parameter('debug_bar', False).value
        self.state = 'idle'
        self.since = self.now()
        self.steer = 0.0
        self.horn_on = False

        qos = QoSProfile(depth=10, reliability=ReliabilityPolicy.RELIABLE)   # wie im ESP-Vertrag
        self.lights_pub = self.create_publisher(LightsCmd, '/lights_cmd', qos)
        self.horn_pub = self.create_publisher(Bool, '/horn_cmd', qos)
        # transient local: der letzte Zustand der Bridge kommt auch an, wenn wir spaeter starten
        self.create_subscription(String, '/bitt/state', self.on_state,
                                 QoSProfile(depth=10, durability=QoSDurabilityPolicy.TRANSIENT_LOCAL))
        self.create_subscription(SteerState, '/steer_state', self.on_steer, 10)
        self.create_timer(1.0 / self.rate, self.tick)
        self.get_logger().info('bitt_body bereit, warte auf /bitt/state')

    def now(self):
        return self.get_clock().now().nanoseconds * 1e-9

    def on_state(self, msg):
        if msg.data not in STATES:
            self.get_logger().warning(f'unbekannter Zustand {msg.data!r}')
            return
        self.state, self.since = msg.data, self.now()
        self.get_logger().info(f'Zustand: {self.state}')

    def on_steer(self, msg):
        self.steer = msg.angle_rad

    def tick(self):
        t = self.now() - self.since
        state = effective_state(self.state, t)
        if state != self.state:                     # kurzer Zustand vorbei -> idle
            self.state, self.since, t = state, self.now(), 0.0
        f = frame(state, t, self.steer)

        msg = LightsCmd()
        msg.header.stamp = self.get_clock().now().to_msg()
        msg.front_rgb = f.front
        msg.rear_rgb = f.rear
        msg.mode = f.mode
        msg.blink_period_ms = 500
        msg.flags = 0
        self.lights_pub.publish(msg)

        if f.horn != self.horn_on:                  # Hupe nur bei Wechsel senden
            self.horn_on = f.horn
            self.horn_pub.publish(Bool(data=f.horn))

        if self.debug_bar:
            print(f'\r{state:<10} {bar(f.rear)} mode={f.mode} horn={int(f.horn)}  ', end='', flush=True)

    def shutdown(self):
        if self.horn_on:
            self.horn_pub.publish(Bool(data=False))


def main():
    rclpy.init()
    node = Body()
    try:
        rclpy.spin(node)
    except (KeyboardInterrupt, ExternalShutdownException):
        pass
    finally:
        node.shutdown()
        node.destroy_node()
        rclpy.try_shutdown()


if __name__ == '__main__':
    main()
