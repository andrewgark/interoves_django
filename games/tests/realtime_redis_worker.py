"""Small subprocess worker used by the opt-in real-Redis integration tests."""

import argparse
import asyncio
import json
import os
from pathlib import Path


def setup_django():
    os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'interoves_django.settings')
    import django

    django.setup()


async def receive_group_message(group, ready_file, timeout):
    from channels.layers import get_channel_layer

    layer = get_channel_layer()
    channel = await layer.new_channel('interoves.redis_test.')
    await layer.group_add(group, channel)
    Path(ready_file).write_text('ready', encoding='utf-8')
    try:
        message = await asyncio.wait_for(layer.receive(channel), timeout=timeout)
    finally:
        await layer.group_discard(group, channel)
        await layer.close_pools()
    print(json.dumps(message, sort_keys=True), flush=True)


async def publish_group_message(group, raw_message):
    from channels.layers import get_channel_layer

    layer = get_channel_layer()
    try:
        await layer.group_send(group, json.loads(raw_message))
    finally:
        await layer.close_pools()


def allocate_sequences(namespace, count):
    from games.views.track import next_track_seq

    values = [next_track_seq(namespace) for _ in range(count)]
    print(json.dumps(values), flush=True)


def main():
    parser = argparse.ArgumentParser()
    subparsers = parser.add_subparsers(dest='action', required=True)

    receive = subparsers.add_parser('receive')
    receive.add_argument('group')
    receive.add_argument('ready_file')
    receive.add_argument('--timeout', type=float, default=10)

    publish = subparsers.add_parser('publish')
    publish.add_argument('group')
    publish.add_argument('message')

    sequence = subparsers.add_parser('sequence')
    sequence.add_argument('namespace')
    sequence.add_argument('count', type=int)

    args = parser.parse_args()
    setup_django()
    if args.action == 'receive':
        asyncio.run(receive_group_message(args.group, args.ready_file, args.timeout))
    elif args.action == 'publish':
        asyncio.run(publish_group_message(args.group, args.message))
    else:
        allocate_sequences(args.namespace, args.count)


if __name__ == '__main__':
    main()
