#!/usr/bin/env bash
# Beendet alles, was zur Sim und zu unseren Knoten gehoert, auch haengende Gazebo-Reste (gz-sim-main).
#   tools/stop_all.sh
# Danach ist die CPU wieder frei und ein neuer Start klappt sauber.

patterns=(
    gz-sim                       # Gazebo-Server und -GUI (heisst gz-sim-main, nicht "gz sim")
    "ros2 launch bitt_sim"       # die Launch-Datei selbst
    parameter_bridge ros_gz_sim foxglove_bridge robot_state_publisher
    lib/bitt_sim/                # sim_esp.py, sim_odom.py, sim_camera_info.py, ackermann_cmd_node.py
    controller_server planner_server smoother_server behavior_server bt_navigator velocity_smoother
    lifecycle_manager
    "bitt_voice bridge" lib/bitt_voice/bridge
    "bitt_body body" lib/bitt_body/body
)
for p in "${patterns[@]}"; do
    pkill -9 -f "$p" 2>/dev/null
done
sleep 1
# ROS-Daemon neu, sonst zeigt "ros2 topic list" noch alte Topics
if [ -f /opt/ros/lyrical/setup.bash ]; then
    # shellcheck disable=SC1091
    source /opt/ros/lyrical/setup.bash
    ros2 daemon stop >/dev/null 2>&1
fi
left=$(pgrep -fa gz-sim | grep -v pgrep)
if [ -n "$left" ]; then
    echo "Achtung, Gazebo laeuft noch:"
    echo "$left"
    echo "Mit 'kill -9 <PID>' beenden (PID = erste Zahl)."
    exit 1
fi
echo "Alles beendet."
