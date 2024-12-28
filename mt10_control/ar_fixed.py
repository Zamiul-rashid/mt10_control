import cv2
import numpy as np
from cv2 import aruco
import rclpy
from rclpy.node import Node
from std_msgs.msg import String
from geometry_msgs.msg import Twist
from sbg_driver.msg import SbgEkfEuler
from sbg_driver.msg import SbgGpsPos

from math import radians, degrees, sin, cos

# Marker size in meters (side length)
MARKER_SIZE = 0.15

# Rectangle dimensions
RECT_WIDTH = 150
RECT_HEIGHT = 90

# Movement parameters
LINEAR_SPEED = 50.0
ANGULAR_SPEED = 7.0
STOP_DISTANCE = 1.0  # Distance threshold in meters

# Hexagon parameters
HEX_RADIUS = 2.0  # Radius of the hexagon in meters
EARTH_RADIUS = 6371000  # Earth radius in meters


class ArucoTrackingNode(Node):
    def __init__(self):
        super().__init__("aruco_tracking_node")

        # Create publisher for cmd_vel
        self.vel_publisher = self.create_publisher(Twist, "/cmd_vel", 10)

        # Initialize video capture
        self.cap = cv2.VideoCapture(2)
        self.cap.set(cv2.CAP_PROP_FPS, 60)

        # Load ArUco dictionary
        self.aruco_dict = aruco.getPredefinedDictionary(aruco.DICT_4X4_50)
        self.parameters = aruco.DetectorParameters()

        # self.parameters.minMarkerPerimeterRate = 0.02

        # Create subscriber for GPS data and orientation
        self.gps_sub = self.create_subscription(
            SbgGpsPos, "/sbg/gps_pos", self.gps_callback, 10
        )
        self.orientation = self.create_subscription(
            SbgEkfEuler, "/sbg/ekf_euler", self.orientation_callback, 10
        )

        # Create timer for camera callback
        self.timer = self.create_timer(0.05, self.camera_callback)  # 30Hz

        # Create publisher for target co-ordianates
        self.gps = self.create_publisher(SbgGpsPos, "/coordinates", 10)

        # Create subscriber for point_follow status
        self.point_status = self.create_subscription(
            String, "/point_status", self.point_status_callback, 10
        )

        # Initialize variables
        self.my_lat = 0.0
        self.my_lon = 0.0
        self.my_yaw = 0.0

        # State variables
        self.goal_reached = False
        self.prev_msg = None
        self.mode = None
        self.prev_yaw = None
        self.search_status = True
        self.visited_tag = []
        self.hexagon_vertices = []
        self.in_transit = False
        self.aruco_detected = False

        self.get_logger().info("ArUco Tracking Node initialized")

    def calculate_distance(self, marker_center, image_center, marker_size_pixels):
        # focal_length_pixels = 1000
        focal_length_pixels = 600
        distance = (MARKER_SIZE * focal_length_pixels) / marker_size_pixels
        return distance

    def draw_guides(self, frame, draw_frame):
        height, width = frame.shape[:2]
        center_x, center_y = width // 2, height // 2

        cv2.line(draw_frame, (0, center_y), (width, center_y), (255, 255, 255), 1)
        cv2.line(draw_frame, (center_x, 0), (center_x, height), (255, 255, 255), 1)

        rect_x1 = center_x - RECT_WIDTH // 2
        rect_y1 = center_y - RECT_HEIGHT // 2
        rect_x2 = center_x + RECT_WIDTH // 2
        rect_y2 = center_y + RECT_HEIGHT // 2
        cv2.rectangle(
            draw_frame, (rect_x1, rect_y1), (rect_x2, rect_y2), (0, 255, 0), 1
        )

        return rect_x1, rect_y1, rect_x2, rect_y2

    def gps_callback(self, msg: SbgGpsPos):
        self.my_lat = msg.latitude
        self.my_lon = msg.longitude

    def orientation_callback(self, msg: SbgEkfEuler):
        self.my_yaw = degrees(msg.angle.z)
        self.my_yaw = (self.my_yaw + 360) % 360

    def get_movement_instruction(self, marker_center, rect_bounds, distance):
        if distance < STOP_DISTANCE:
            return "Stop"
        rect_x1, _, rect_x2, _ = rect_bounds
        x, _ = marker_center

        if x < rect_x1:
            return "Move Left"
        elif x > rect_x2:
            return "Move Right"
        else:
            return "Move Forward"

    def ar_detection(self, frame):
        display_frame = frame.copy()
        gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)

        corners, ids, _ = aruco.detectMarkers(
            gray, self.aruco_dict, parameters=self.parameters
        )
        rect_bounds = self.draw_guides(frame, display_frame)

        return corners, ids, display_frame, rect_bounds

    def searching(self):
        if self.search_status:
            msg = Twist()

            if self.mode != "rotating":
                self.prev_yaw = self.my_yaw
                self.mode = "rotating"
                self.get_logger().info("Aruco not detected. Rotating --->>")

            self.get_logger().info(f"prev yaw = {self.prev_yaw}")
            yaw_diff = ((self.my_yaw - self.prev_yaw) + 360) % 360

            if yaw_diff < 350.0 or yaw_diff >= 357:
                msg.angular.z = -ANGULAR_SPEED
                self.get_logger().info(
                    f"Rotating... Current yaw: {self.my_yaw:.2f}, Yaw diff: {yaw_diff:.2f}"
                )

            else:
                self.get_logger().info(
                    "360-degree search complete. Aruco tag not found."
                )
                self.mode = "point follow"
                msg.linear.x = 0.0
                msg.angular.z = 0.0
                self.search_status = False
                # self.in_transit = True
        return msg

    def point_status_callback(self, msg: String):
        if msg.data == "Reached":
            self.search_status = True
            self.in_transit = False
            self.mode = None

    # def gps_callback(self, msg: SbgGpsPos):
    #     self.my_lat = msg.latitude
    #     self.my_lon = msg.longitude

    # def orientation_callback(self, msg: SbgEkfEuler):
    #     self.my_yaw = degrees(msg.angle.z)
    #     self.my_yaw = (self.my_yaw + 360) % 360

    def publish_movement_command(self, instruction, ids):
        msg = Twist()

        if instruction == "Stop":
            msg.linear.x = 0.0
            msg.angular.z = 0.0
            self.goal_reached = True
            self.search_status = True
            self.aruco_detected = False
            self.hexagon_vertices = []
            self.visited_tag.append(ids)
            self.get_logger().info("Goal reached! Stopping robot.")
        else:
            if instruction == "Move Forward":
                msg.linear.x = LINEAR_SPEED
            elif instruction == "Move Left":
                msg.angular.z = ANGULAR_SPEED
            elif instruction == "Move Right":
                msg.angular.z = -ANGULAR_SPEED

        if msg != self.prev_msg:
            self.vel_publisher.publish(msg)
            self.prev_msg = msg

    def calculate_hexagon_vertices(self, lat, lon):
        vertices = []
        for i in range(6):
            angle = radians(60 * i)
            delta_lat = (HEX_RADIUS / EARTH_RADIUS) * cos(angle)
            delta_lon = (HEX_RADIUS / (EARTH_RADIUS * cos(radians(lat)))) * sin(angle)
            vertex_lat = lat + degrees(delta_lat)
            vertex_lon = lon + degrees(delta_lon)
            vertices.append((vertex_lat, vertex_lon))
        return vertices

    def camera_callback(self):
        if self.in_transit:
            return

        ret, frame = self.cap.read()
        if not ret:
            self.get_logger().error("Failed to capture frame")
            return
        else:
            corners, ids, display_frame, rect_bounds = self.ar_detection(frame)
            print(ids)

            if ids is not None:
                ids = list(ids)
                for id in range(len(ids)):
                    if ids[id] in self.visited_tag:
                        ids.pop(id)
                if ids == []:
                    ids = None

            if ids is not None:
                self.aruco_detected = True
                self.get_logger().info(f"Aruco tag detected: {ids}")

                for i in range(len(ids)):
                    marker_corners = corners[i][0]
                    marker_center = tuple(np.mean(marker_corners, axis=0).astype(int))
                    marker_width_pixels = np.linalg.norm(
                        marker_corners[0] - marker_corners[1]
                    )

                    distance = self.calculate_distance(
                        marker_center, frame.shape[:2], marker_width_pixels
                    )

                    instruction = self.get_movement_instruction(
                        marker_center, rect_bounds, distance
                    )

                    self.get_logger().info(f"Distance to marker: {distance:.2f} m")
                    self.get_logger().info(f"Movement instruction: {instruction}")

                    self.publish_movement_command(instruction, ids[i])

                    aruco.drawDetectedMarkers(display_frame, corners)

                    # Display instruction
                    cv2.putText(
                        display_frame,
                        f"ID: {ids[i][0]} Distance: {distance:.2f}m",
                        (10, 30 + i * 30),
                        cv2.FONT_HERSHEY_SIMPLEX,
                        0.6,
                        (0, 255, 0),
                        2,
                    )

                    instruction_color = (
                        (0, 0, 255) if instruction == "Stop" else (255, 255, 255)
                    )
                    cv2.putText(
                        display_frame,
                        f"Instruction: {instruction}",
                        (10, 60),
                        cv2.FONT_HERSHEY_SIMPLEX,
                        0.8,
                        instruction_color,
                        2,
                    )

            else:
                if self.aruco_detected:
                    pass
                else:
                    if self.search_status:
                        msg = self.searching()
                        if self.prev_msg != msg:
                            self.vel_publisher.publish(msg)
                    elif self.mode == "point follow":
                        self.in_transit = True
                        self.search_status = False
                        self.get_logger().info(
                            "Point follow mode activated. Moving to next point."
                        )
                        if self.hexagon_vertices == []:
                            self.hexagon_vertices = self.calculate_hexagon_vertices(
                                self.my_lat, self.my_lon
                            )
                        nav_msg = SbgGpsPos()
                        nav_msg.latitude = self.hexagon_vertices[0][0]
                        nav_msg.longitude = self.hexagon_vertices[0][1]
                        self.gps.publish(nav_msg)
                        self.hexagon_vertices.pop(0)

            cv2.imshow("Aruco Tracking", display_frame)
            cv2.waitKey(1)

    def __del__(self):
        self.cap.release()
        cv2.destroyAllWindows()


def main(args=None):
    rclpy.init(args=args)
    node = ArucoTrackingNode()
    try:
        rclpy.spin(node)
    except KeyboardInterrupt:
        pass
    finally:
        node.destroy_node()
        rclpy.shutdown()


if __name__ == "__main__":
    main()
