#!/usr/bin/env python3
"""HTTP-Bridge zwischen der Sprach-App (Mac) und BITT (Sim oder echtes Auto).

    ros2 run bitt_voice bridge                                    # echtes Auto (Preview-JPEG)
    ros2 run bitt_voice bridge --ros-args -p use_sim_time:=true \
        -p camera_topic:=/visual_slam/image_0 -p camera_raw:=true  # Sim

Endpoints (Port 8770, JSON rein/raus):
    GET  /snapshot      letztes Frontbild als JPEG
    GET  /camera_info   {"fx", "cx", "width"} passend zum Snapshot
    POST /locate        {"objects": [{"label": "BMW", "u": 410}]} -> Winkel, Abstand, Position; Marker fuer Foxglove
    POST /park          {"label": "BMW", "relation": "behind|left|right|front"} -> faehrt per Nav2 hin
    POST /stop          bricht alle Nav2-Ziele ab
    POST /state         {"state": "speaking"} -> /bitt/state (fuer Ds Lichter-Knoten)
    GET  /status        {"nav": "idle|driving|reached|failed|cancelled", "dist_left": m, "objects": [...]}

Die Bridge publiziert NIE Lichter oder Hupe, das macht nur bitt_body.
"""
import json
import math
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import cv2
import numpy as np
import rclpy
from action_msgs.msg import GoalStatus
from action_msgs.srv import CancelGoal
from geometry_msgs.msg import PoseStamped
from nav2_msgs.action import NavigateToPose
from nav_msgs.msg import OccupancyGrid
from rclpy.action import ActionClient
from rclpy.executors import ExternalShutdownException, MultiThreadedExecutor
from rclpy.node import Node
from rclpy.qos import QoSDurabilityPolicy, QoSProfile, QoSReliabilityPolicy, qos_profile_sensor_data
from rclpy.time import Time
from sensor_msgs.msg import CameraInfo, CompressedImage, Image
from std_msgs.msg import String
from tf2_ros import Buffer, TransformException, TransformListener
from visualization_msgs.msg import Marker, MarkerArray

from bitt_voice.geometry import Grid, bearing_from_pixel, choose_goal, raycast, yaw_of

STATES = {'idle', 'listening', 'thinking', 'speaking', 'driving', 'refuse', 'arrived', 'warn'}


class Bridge(Node):
    def __init__(self):
        super().__init__('bitt_voice_bridge')
        p = self.declare_parameter
        self.camera_topic = p('camera_topic', '/preview/left/compressed').value
        self.camera_raw = p('camera_raw', False).value
        self.port = p('port', 8770).value
        self.frame = p('global_frame', 'odom').value
        self.object_depth = p('object_depth', 0.6).value
        self.min_goal_dist = p('min_goal_dist', 2.0).value
        p('camera_info_topic', '/visual_slam/camera_info_0')
        p('costmap_topic', '/global_costmap/costmap')

        self.lock = threading.Lock()
        self.jpeg = None             # letztes Bild als JPEG-Bytes
        self.img_width = None        # Breite des Snapshots (Preview kann kleiner sein als die Kalibrierung)
        self.info = None             # CameraInfo
        self.grid = None             # Grid aus der Costmap
        self.objects = {}            # label -> {"x", "y", "angle", "hit_x", "hit_y", ...}
        self.nav = 'idle'
        self.dist_left = None
        self.goal_handle = None

        if self.camera_raw:
            self.create_subscription(Image, self.camera_topic, self.on_raw, qos_profile_sensor_data)
        else:
            self.create_subscription(CompressedImage, self.camera_topic, self.on_jpeg, qos_profile_sensor_data)
        self.create_subscription(CameraInfo, self.get_parameter('camera_info_topic').value,
                                 self.on_info, qos_profile_sensor_data)
        # Reliable + volatile passt zu den Costmap-Publishern (auch transient local)
        self.create_subscription(OccupancyGrid, self.get_parameter('costmap_topic').value, self.on_grid,
                                 QoSProfile(depth=1, reliability=QoSReliabilityPolicy.RELIABLE))

        self.tf = Buffer()
        self.tf_listener = TransformListener(self.tf, self)
        self.nav_client = ActionClient(self, NavigateToPose, 'navigate_to_pose')
        self.cancel_client = self.create_client(CancelGoal, '/navigate_to_pose/_action/cancel_goal')
        self.state_pub = self.create_publisher(
            String, '/bitt/state', QoSProfile(depth=10, durability=QoSDurabilityPolicy.TRANSIENT_LOCAL))
        self.marker_pub = self.create_publisher(MarkerArray, '/bitt/markers', 10)

    # ---------- ROS-Callbacks: nur das Neueste merken ----------

    def on_raw(self, msg):
        # Sim: mono8 (oder rgb8) roh -> JPEG
        ch = 1 if msg.encoding in ('mono8', '8UC1') else 3
        img = np.frombuffer(msg.data, np.uint8).reshape(msg.height, msg.step)[:, :msg.width * ch]
        if ch == 3:
            img = img.reshape(msg.height, msg.width, 3)
            if msg.encoding == 'rgb8':
                img = cv2.cvtColor(img, cv2.COLOR_RGB2BGR)
        ok, jpg = cv2.imencode('.jpg', img, [cv2.IMWRITE_JPEG_QUALITY, 80])
        if ok:
            with self.lock:
                self.jpeg, self.img_width = jpg.tobytes(), msg.width

    def on_jpeg(self, msg):
        # Echtes Auto: schon JPEG; Breite einmal durch Dekodieren bestimmen
        width = self.img_width
        if width is None:
            img = cv2.imdecode(np.frombuffer(msg.data, np.uint8), cv2.IMREAD_GRAYSCALE)
            width = img.shape[1] if img is not None else None
        with self.lock:
            self.jpeg, self.img_width = bytes(msg.data), width

    def on_info(self, msg):
        with self.lock:
            self.info = msg

    def on_grid(self, msg):
        g = Grid.from_msg(msg)
        with self.lock:
            self.grid = g

    # ---------- Hilfen ----------

    def pose(self, child):
        """(x, y, yaw) von `child` im globalen Frame, oder None."""
        try:
            t = self.tf.lookup_transform(self.frame, child, Time())
        except TransformException as e:
            self.get_logger().warning(f'kein TF {self.frame} -> {child}: {e}')
            return None
        tr = t.transform.translation
        return tr.x, tr.y, yaw_of(t.transform.rotation)

    def intrinsics(self):
        """fx, cx skaliert auf die Breite des Snapshots."""
        with self.lock:
            info, width = self.info, self.img_width
        if info is None or not info.width:
            return None
        scale = (width or info.width) / info.width
        return info.k[0] * scale, info.k[2] * scale, int(width or info.width)

    # ---------- Endpoints ----------

    def api_camera_info(self, _body):
        intr = self.intrinsics()
        if intr is None:
            return 503, {'error': 'noch keine camera_info'}
        return 200, {'fx': intr[0], 'cx': intr[1], 'width': intr[2]}

    def api_locate(self, body):
        intr, cam = self.intrinsics(), self.pose('camera_link')
        with self.lock:
            grid = self.grid
        if intr is None or cam is None or grid is None:
            return 503, {'error': 'camera_info, TF oder Costmap fehlt noch'}
        fx, cx, _ = intr
        result = []
        for obj in body.get('objects', []):
            label, u = str(obj['label']), float(obj['u'])
            bearing = bearing_from_pixel(u, fx, cx)
            angle = cam[2] + bearing
            hit = raycast(grid, cam[0], cam[1], angle)
            entry = {'label': label, 'bearing_deg': round(math.degrees(bearing), 1)}
            if hit is None:
                entry.update(found=False, note='in dieser Richtung kein Hindernis in der Costmap')
            else:
                entry.update(found=True, dist_m=round(hit[2], 2), x=round(hit[0], 2), y=round(hit[1], 2))
                with self.lock:
                    self.objects[label.lower()] = {'label': label, 'hit_x': hit[0], 'hit_y': hit[1],
                                                   'angle': angle, 'dist_m': hit[2]}
            result.append(entry)
        self.publish_markers()
        return 200, {'objects': result}

    def api_park(self, body):
        label, relation = str(body.get('label', '')), str(body.get('relation', ''))
        with self.lock:
            obj, grid = self.objects.get(label.lower()), self.grid
        if obj is None:
            return 404, {'ok': False, 'reason': f'"{label}" kenne ich nicht, erst /locate aufrufen'}
        car = self.pose('base_link')
        if car is None or grid is None:
            return 503, {'ok': False, 'reason': 'TF oder Costmap fehlt'}
        try:
            ok, goal, why = choose_goal(grid, car[0], car[1], obj['hit_x'], obj['hit_y'], obj['angle'],
                                        relation, self.min_goal_dist, self.object_depth,
                                        heading=car[2])
        except ValueError as e:
            return 400, {'ok': False, 'reason': str(e)}
        if not ok:
            return 200, {'ok': False, 'reason': why}
        self.send_goal(*goal)
        self.publish_markers(goal)
        return 200, {'ok': True, 'goal': {'x': round(goal[0], 2), 'y': round(goal[1], 2)},
                     'dist_m': round(math.hypot(goal[0] - car[0], goal[1] - car[1]), 1)}

    def api_stop(self, _body):
        if not self.cancel_client.service_is_ready():
            return 503, {'ok': False, 'reason': 'Nav2 nicht erreichbar'}
        self.cancel_client.call_async(CancelGoal.Request())   # leere Anfrage = alle Ziele abbrechen
        with self.lock:
            self.nav = 'cancelled'
        return 200, {'ok': True}

    def api_state(self, body):
        state = str(body.get('state', ''))
        if state not in STATES:
            return 400, {'error': f'state muss einer von {sorted(STATES)} sein'}
        self.state_pub.publish(String(data=state))
        return 200, {'ok': True}

    def api_status(self, _body):
        with self.lock:
            return 200, {'nav': self.nav, 'dist_left': self.dist_left,
                         'objects': [o['label'] for o in self.objects.values()]}

    # ---------- Nav2 ----------

    def send_goal(self, x, y, yaw):
        if not self.nav_client.server_is_ready():
            self.get_logger().error('navigate_to_pose nicht bereit')
            with self.lock:
                self.nav = 'failed'
            return
        goal = NavigateToPose.Goal()
        goal.pose = PoseStamped()
        goal.pose.header.frame_id = self.frame   # Stamp bleibt 0 = "neuester TF", robust gegen Uhrzeit-Probleme
        goal.pose.pose.position.x, goal.pose.pose.position.y = x, y
        goal.pose.pose.orientation.z, goal.pose.pose.orientation.w = math.sin(yaw / 2), math.cos(yaw / 2)
        with self.lock:
            self.nav, self.dist_left = 'driving', None
        self.get_logger().info(f'Ziel x={x:.2f} y={y:.2f} yaw={math.degrees(yaw):.0f} deg')
        future = self.nav_client.send_goal_async(goal, feedback_callback=self.on_feedback)
        future.add_done_callback(self.on_goal_response)

    def on_feedback(self, fb):
        with self.lock:
            self.dist_left = round(fb.feedback.distance_remaining, 2)

    def on_goal_response(self, future):
        handle = future.result()
        if not handle.accepted:
            with self.lock:
                self.nav = 'failed'
            return
        self.goal_handle = handle
        handle.get_result_async().add_done_callback(self.on_result)

    def on_result(self, future):
        status = future.result().status
        names = {GoalStatus.STATUS_SUCCEEDED: 'reached', GoalStatus.STATUS_CANCELED: 'cancelled'}
        with self.lock:
            self.nav = names.get(status, 'failed')
        self.get_logger().info(f'Ziel: {self.nav}')

    # ---------- Foxglove ----------

    def publish_markers(self, goal=None):
        arr = MarkerArray()
        clear = Marker()
        clear.action = Marker.DELETEALL
        arr.markers.append(clear)
        with self.lock:
            objs = list(self.objects.values())
        for i, o in enumerate(objs):
            cx = o['hit_x'] + self.object_depth / 2 * math.cos(o['angle'])
            cy = o['hit_y'] + self.object_depth / 2 * math.sin(o['angle'])
            cyl = self.marker(2 * i, Marker.CYLINDER, cx, cy, 0.3, (0.2, 0.6, 1.0))
            cyl.scale.x = cyl.scale.y = self.object_depth
            cyl.scale.z = 0.6
            txt = self.marker(2 * i + 1, Marker.TEXT_VIEW_FACING, cx, cy, 0.9, (1.0, 1.0, 1.0))
            txt.scale.z = 0.3
            txt.text = o['label']
            arr.markers += [cyl, txt]
        if goal is not None:
            arrow = self.marker(1000, Marker.ARROW, goal[0], goal[1], 0.1, (0.2, 1.0, 0.3))
            arrow.pose.orientation.z, arrow.pose.orientation.w = math.sin(goal[2] / 2), math.cos(goal[2] / 2)
            arrow.scale.x, arrow.scale.y, arrow.scale.z = 1.0, 0.15, 0.15
            arr.markers.append(arrow)
        self.marker_pub.publish(arr)

    def marker(self, mid, mtype, x, y, z, rgb):
        m = Marker()
        m.header.frame_id = self.frame
        m.ns, m.id, m.type, m.action = 'bitt', mid, mtype, Marker.ADD
        m.pose.position.x, m.pose.position.y, m.pose.position.z = x, y, z
        m.pose.orientation.w = 1.0
        m.color.r, m.color.g, m.color.b, m.color.a = (*rgb, 0.9)
        return m


def make_handler(bridge):
    routes = {
        ('GET', '/camera_info'): bridge.api_camera_info,
        ('GET', '/status'): bridge.api_status,
        ('POST', '/locate'): bridge.api_locate,
        ('POST', '/park'): bridge.api_park,
        ('POST', '/stop'): bridge.api_stop,
        ('POST', '/state'): bridge.api_state,
    }

    class Handler(BaseHTTPRequestHandler):
        def _send(self, code, payload, ctype='application/json'):
            data = payload if isinstance(payload, bytes) else json.dumps(payload).encode()
            self.send_response(code)
            self.send_header('Content-Type', ctype)
            self.send_header('Content-Length', str(len(data)))
            self.end_headers()
            self.wfile.write(data)

        def _route(self, method):
            path = self.path.split('?')[0]
            if method == 'GET' and path == '/snapshot':
                with bridge.lock:
                    jpg = bridge.jpeg
                return self._send(200, jpg, 'image/jpeg') if jpg else self._send(503, {'error': 'noch kein Bild'})
            fn = routes.get((method, path))
            if fn is None:
                return self._send(404, {'error': f'{method} {path} gibt es nicht'})
            body = {}
            length = int(self.headers.get('Content-Length') or 0)
            if length:
                try:
                    body = json.loads(self.rfile.read(length))
                except json.JSONDecodeError:
                    return self._send(400, {'error': 'Body ist kein JSON'})
            try:
                code, payload = fn(body)
            except Exception as e:   # eine kaputte Anfrage soll die Bridge nicht toeten
                bridge.get_logger().error(f'{path}: {e!r}')
                code, payload = 500, {'error': repr(e)}
            self._send(code, payload)

        def do_GET(self):
            self._route('GET')

        def do_POST(self):
            self._route('POST')

        def log_message(self, fmt, *args):
            bridge.get_logger().debug(fmt % args)

    return Handler


def main():
    rclpy.init()
    bridge = Bridge()
    server = ThreadingHTTPServer(('0.0.0.0', bridge.port), make_handler(bridge))
    threading.Thread(target=server.serve_forever, daemon=True).start()
    bridge.get_logger().info(f'HTTP-Bridge auf Port {bridge.port}, Kamera {bridge.camera_topic}')
    executor = MultiThreadedExecutor()
    executor.add_node(bridge)
    try:
        executor.spin()
    except (KeyboardInterrupt, ExternalShutdownException):
        pass
    finally:
        server.shutdown()
        bridge.destroy_node()
        rclpy.try_shutdown()


if __name__ == '__main__':
    main()
