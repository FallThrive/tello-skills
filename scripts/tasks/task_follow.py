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


class SegFollowController:
    """分割模式比例控制器——用像素面积控制前后距离"""

    def __init__(self, center_x=480, center_y=360):
        self.center_x = center_x
        self.center_y = center_y
        self.kp_yaw = 0.2
        self.kp_ud = 0.3
        self.fb_speed_val = 15
        self.area_min = 100000
        self.area_max = 150000

    def update(self, target_info):
        if target_info is None:
            return (0, 0, 0, 0)

        cx, cy = target_info['center']
        area = target_info.get('area', self.area_min + 1)

        error_x = cx - self.center_x
        yaw_speed = int(self.kp_yaw * error_x)
        yaw_speed = max(-50, min(50, yaw_speed))

        error_y = self.center_y - cy
        ud_speed = int(self.kp_ud * error_y)
        ud_speed = max(-50, min(50, ud_speed))

        if area < self.area_min:
            fb_speed = self.fb_speed_val
        elif area > self.area_max:
            fb_speed = -self.fb_speed_val
        else:
            fb_speed = 0

        return (0, fb_speed, ud_speed, yaw_speed)


class PoseFollowController:
    """姿态模式比例控制器——用躯干高度控制前后距离"""

    def __init__(self, center_x=480, center_y=360):
        self.center_x = center_x
        self.center_y = center_y
        self.kp_yaw = 0.2
        self.kp_ud = 0.3
        self.fb_speed_val = 15
        self.height_min = 200
        self.height_max = 250

    def update(self, target_info):
        if target_info is None:
            return (0, 0, 0, 0)

        cx, cy = target_info['center']
        torso_height = target_info.get('torso_height', 0)
        has_hips = target_info.get('has_hips', False)

        error_x = cx - self.center_x
        yaw_speed = int(self.kp_yaw * error_x)
        yaw_speed = max(-50, min(50, yaw_speed))

        error_y = self.center_y - cy
        ud_speed = int(self.kp_ud * error_y)
        ud_speed = max(-50, min(50, ud_speed))

        if has_hips:
            if torso_height < self.height_min:
                fb_speed = self.fb_speed_val
            elif torso_height > self.height_max:
                fb_speed = -self.fb_speed_val
            else:
                fb_speed = 0
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
    parser.add_argument('--model', choices=['seg', 'pose'], default='pose',
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

    if args.model == 'pose':
        controller = PoseFollowController(center_x=480, center_y=360)
    else:
        controller = SegFollowController(center_x=480, center_y=360)
    send_command("led solid 255 0 0")

    start_time = time.time()
    print(f"跟随模式开始，时长 {args.duration} 秒，模型: {args.model}")

    while running and (time.time() - start_time < args.duration):
        if emergency_check():
            print("TOF 紧急停止——距离过近")
            send_command("flight rc 0 0 0 0")
            break

        result = send_command(f"yolo detect --model {args.model}")
        if result.startswith("error"):
            print(f"检测错误: {result}")
            send_command("flight rc 0 0 0 0")
            time.sleep(0.1)
            continue

        try:
            target = json.loads(result)
        except json.JSONDecodeError:
            target = {}

        if not target:
            send_command("flight rc 0 0 0 0")
            send_command("matrix static b ?")
            time.sleep(0.1)
            continue

        lr, fb, ud, yaw = controller.update(target)
        send_command(f"flight rc {lr} {fb} {ud} {yaw}")

        # LED 屏显距离信息
        if args.model == 'seg':
            area_k = int(target.get('area', 0) // 1000)
            send_command(f"matrix static r {area_k}k")
        else:
            h = int(target.get('torso_height', 0))
            send_command(f"matrix static r {h}h")

        time.sleep(0.05)

    send_command("flight rc 0 0 0 0")
    send_command("led off")
    send_command("matrix off")
    send_command("vision stream_off")
    print("跟随结束")


if __name__ == '__main__':
    main()
