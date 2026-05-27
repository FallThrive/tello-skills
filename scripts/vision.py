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
    p_photo.add_argument('--name', '-n', default='')

    p_rec_start = sub.add_parser('record_start', help='开始录像')
    p_rec_start.add_argument('--name', '-n', default='')

    sub.add_parser('record_stop', help='停止录像')

    p_prev_start = sub.add_parser('preview_start', help='开启纯净预览窗口')
    p_prev_start.add_argument('direction', choices=['forward', 'downward'])

    p_prev_stop = sub.add_parser('preview_stop', help='关闭纯净预览窗口')
    p_prev_stop.add_argument('direction', choices=['forward', 'downward'])

    sub.add_parser('preview_yolo_stop', help='关闭 YOLO 标注预览窗口')

    p_ylo_start = sub.add_parser('preview_yolo_start', help='手动开启 YOLO 标注预览窗口')
    p_ylo_start.add_argument('--model', '-m', choices=['pose', 'seg'], default='pose',
                              help='模型类型（默认 pose）')

    args = parser.parse_args()

    if args.action == 'photo':
        cmd = f"vision photo {args.name}"
    elif args.action == 'record_start':
        cmd = f"vision record_start {args.name}"
    elif args.action in ('preview_start', 'preview_stop'):
        cmd = f"vision {args.action} {args.direction}"
    elif args.action == 'preview_yolo_start':
        cmd = f"vision preview_yolo_start --model {args.model}"
    else:
        cmd = f"vision {args.action}"

    print(send_command(cmd))


if __name__ == '__main__':
    main()
