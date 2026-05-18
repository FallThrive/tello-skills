#!/usr/bin/env python3
"""实时人员跟随——YOLO检测→比例控制→rc_control闭环"""
import argparse
import time
import json
import signal
import sys
import os
sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))
from _client import send_command
from collections import deque
import numpy as np


class FollowController:
    """比例控制器——参考 ref/tello_track/modules/tracking_controller.py"""

    def __init__(self, center_x=480, center_y=360):
        self.center_x = center_x
        self.center_y = center_y
        self.kp_yaw = 0.2
        self.kp_ud = 0.3
        self.fb_speed_val = 15
        self.target_history = deque(maxlen=5)
        self.area_min = 100000
        self.area_max = 150000

    def update(self, target_info):
        if target_info is None:
            return (0, 0, 0, 0)

        cx, cy = target_info['center']
        area = target_info.get('area', self.area_min + 1)

        self.target_history.append((cx, cy))
        if len(self.target_history) >= 3:
            avg = np.mean(self.target_history, axis=0).astype(int)
            smooth_cx, smooth_cy = avg
        else:
            smooth_cx, smooth_cy = cx, cy

        error_x = smooth_cx - self.center_x
        yaw_speed = int(self.kp_yaw * error_x)
        yaw_speed = max(-50, min(50, yaw_speed))

        error_y = self.center_y - smooth_cy
        ud_speed = int(self.kp_ud * error_y)
        ud_speed = max(-50, min(50, ud_speed))

        if area < self.area_min:
            fb_speed = self.fb_speed_val
        elif area > self.area_max:
            fb_speed = -self.fb_speed_val
        else:
            fb_speed = 0

        return (0, fb_speed, ud_speed, yaw_speed)


def emergency_check():
    tof_str = send_command("sensor tof")
    try:
        dist = int(tof_str)
    except ValueError:
        return False
    return 100 <= dist < 500


def main():
    parser = argparse.ArgumentParser(description='实时人员跟随')
    parser.add_argument('--duration', type=int, default=120, help='跟随时长(秒)')
    parser.add_argument('--model', choices=['seg', 'pose'], default='seg',
                        help='跟踪模型')
    args = parser.parse_args()

    running = True

    def sigint_handler(signum, frame):
        nonlocal running
        print("\n收到中断信号，安全降落...")
        running = False

    signal.signal(signal.SIGINT, sigint_handler)

    send_command("vision stream_on")
    time.sleep(1)

    controller = FollowController(center_x=480, center_y=360)
    send_command("led solid 255 0 0")

    start_time = time.time()
    print(f"跟随模式开始，时长 {args.duration} 秒，模型: {args.model}")

    while running and (time.time() - start_time < args.duration):
        if emergency_check():
            print("TOF 紧急停止——距离过近")
            send_command("flight rc 0 0 0 0")
            break

        result = send_command("yolo detect")
        if result.startswith("error"):
            print(f"检测错误: {result}")
            send_command("flight rc 0 0 0 0")
            time.sleep(0.1)
            continue

        try:
            persons = json.loads(result)
        except json.JSONDecodeError:
            persons = []

        if not persons:
            send_command("flight rc 0 0 0 0")
            send_command("matrix static b ?")
            time.sleep(0.1)
            continue

        target = max(persons, key=lambda p: p['confidence'])
        x1, y1, x2, y2 = target['bbox']
        target['area'] = (x2 - x1) * (y2 - y1)

        lr, fb, ud, yaw = controller.update(target)
        send_command(f"flight rc {lr} {fb} {ud} {yaw}")

        area_k = target['area'] // 1000
        send_command(f"matrix static r {area_k}k")

        time.sleep(0.05)

    send_command("flight rc 0 0 0 0")
    send_command("led off")
    send_command("matrix off")
    send_command("vision stream_off")
    print("跟随结束")


if __name__ == '__main__':
    main()
