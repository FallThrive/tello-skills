#!/usr/bin/env python3
"""飞行控制 CLI"""
import argparse
import sys
import os
sys.path.insert(0, os.path.dirname(__file__))
from _client import send_command


def main():
    parser = argparse.ArgumentParser(description='Tello 飞行控制')
    sub = parser.add_subparsers(dest='action', required=True)

    sub.add_parser('takeoff', help='起飞')
    sub.add_parser('land', help='降落')

    p_move = sub.add_parser('move', help='距离移动')
    p_move.add_argument('--direction', '-d', required=True,
                        choices=['f', 'b', 'l', 'r', 'u', 'd'],
                        help='方向: f=前 b=后 l=左 r=右 u=上 d=下')
    p_move.add_argument('--dist', type=int, required=True, help='距离(cm)')

    p_rotate = sub.add_parser('rotate', help='旋转')
    p_rotate.add_argument('--direction', '-d', required=True,
                          choices=['cw', 'ccw'], help='cw=顺时针 ccw=逆时针')
    p_rotate.add_argument('--deg', type=int, required=True, help='角度')

    p_rc = sub.add_parser('rc', help='速度控制')
    p_rc.add_argument('--lr', type=int, default=0, help='左右速度(右+左-)')
    p_rc.add_argument('--fb', type=int, default=0, help='前后速度(前+后-)')
    p_rc.add_argument('--ud', type=int, default=0, help='上下速度(上+下-)')
    p_rc.add_argument('--yaw', type=int, default=0, help='偏航速度(顺+逆-)')

    args = parser.parse_args()

    if args.action in ('takeoff', 'land'):
        cmd = f"flight {args.action}"
    elif args.action == 'move':
        cmd = f"flight move {args.direction} {args.dist}"
    elif args.action == 'rotate':
        cmd = f"flight rotate {args.direction} {args.deg}"
    elif args.action == 'rc':
        cmd = f"flight rc {args.lr} {args.fb} {args.ud} {args.yaw}"

    print(send_command(cmd))


if __name__ == '__main__':
    main()
