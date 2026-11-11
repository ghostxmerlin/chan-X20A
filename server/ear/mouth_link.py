"""/mouth relay (2026-11-11): the robot's speaker downlink.

The robot connects to the ear's public port at /mouth (like /vision); the ear has already checked the
device token, then passes every frame both ways to soul-tts's mouth server on the loopback
(127.0.0.1:52751), adding the same token. Either side closing closes the other. soul-tts not running:
the robot's side is closed with 1011 and it reconnects on its own.
"""
import asyncio

UPSTREAM = "ws://127.0.0.1:52751/mouth"


async def _pump(src, dst):
    async for message in src:
        await dst.send(message)


async def relay(client, upstream_url, token, connect=None):
    """Relay until one side closes; returns why ("device_closed", "upstream_closed", "upstream_unavailable")."""
    if connect is None:
        from websockets.asyncio.client import connect
    try:
        upstream = await connect(upstream_url, additional_headers={"Authorization": f"Bearer {token}"},
                                 max_size=64 * 1024, open_timeout=5)
    except Exception:                           # noqa: BLE001 - soul-tts down or refusing
        await client.close(code=1011, reason="mouth service unavailable")
        return "upstream_unavailable"
    down = asyncio.create_task(_pump(client, upstream))
    up = asyncio.create_task(_pump(upstream, client))
    done, pending = await asyncio.wait({down, up}, return_when=asyncio.FIRST_COMPLETED)
    for task in pending:
        task.cancel()
    await asyncio.gather(*pending, return_exceptions=True)
    await upstream.close()
    await client.close()
    return "device_closed" if down in done else "upstream_closed"
