#!/usr/bin/env python3
"""仅供模拟器联调：本机回环 8080/8081 原样转发到机器人，不解析或记录媒体。"""
import argparse
import asyncio
import ipaddress


async def relay(reader, writer, host, port):
    upstream = None
    tasks = []
    try:
        remote, upstream = await asyncio.wait_for(asyncio.open_connection(host, port), 3)

        async def copy(source, target, read_timeout=None):
            while True:
                data = await asyncio.wait_for(source.read(65536), read_timeout)
                if not data:
                    return
                target.write(data)
                await asyncio.wait_for(target.drain(), 5)

        tasks = [asyncio.create_task(copy(reader, upstream)),
                 asyncio.create_task(copy(remote, writer, 15))]
        await asyncio.wait(tasks, return_when=asyncio.FIRST_COMPLETED)
    except (OSError, asyncio.TimeoutError):
        pass
    finally:
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
        for stream in (writer, upstream):
            if stream:
                stream.close()
                try:
                    await stream.wait_closed()
                except OSError:
                    pass


async def main(host):
    servers = []
    for port in (8080, 8081):
        server = await asyncio.start_server(
            lambda reader, writer, port=port: relay(reader, writer, host, port),
            "127.0.0.1", port, limit=65536)
        servers.append(server)
    print("模拟器联调转发已启动：10.0.2.2:8080 → %s:8080；音频 8081。仅监听本机回环。" % host,
          flush=True)
    try:
        await asyncio.gather(*(server.serve_forever() for server in servers))
    finally:
        for server in servers:
            server.close()
        await asyncio.gather(*(server.wait_closed() for server in servers))


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("robot_ip", help="机器人当前私有局域网 IPv4 地址")
    args = parser.parse_args()
    address = ipaddress.ip_address(args.robot_ip)
    if address.version != 4 or not address.is_private or address.is_loopback or address.is_unspecified:
        parser.error("必须提供机器人实际私有局域网 IPv4 地址")
    try:
        asyncio.run(main(str(address)))
    except KeyboardInterrupt:
        pass
