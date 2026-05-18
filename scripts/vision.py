#!/usr/bin/env python3
"""视觉 CLI"""
import argparse, sys, os
sys.path.insert(0, os.path.dirname(__file__))
from _client import send_command


def main():
    parser = argparse.ArgumentParser(description='Tello 视觉')
    sub = parser.add_subparsers(dest='action', required=True)

    sub.add_parser('stream_on', help='开启视频流')
    sub.add_parser('stream_off', help='关闭视频流')

    p_photo = sub.add_parser('photo', help='拍照')
    p_photo.add_argument('--name', '-n', default='photo.jpg')

    p_rec_start = sub.add_parser('record_start', help='开始录像')
    p_rec_start.add_argument('--name', '-n', default='video.avi')

    sub.add_parser('record_stop', help='停止录像')

    args = parser.parse_args()

    if args.action == 'photo':
        cmd = f"vision photo {args.name}"
    elif args.action == 'record_start':
        cmd = f"vision record_start {args.name}"
    else:
        cmd = f"vision {args.action}"

    print(send_command(cmd))


if __name__ == '__main__':
    main()
