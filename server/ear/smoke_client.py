"""Readiness check with silence, Opus framing and cancelled enrollment; no identity saved.

Settings only from SOUL_EAR_URL / SOUL_EAR_TOKEN in environment, never argv.
"""
import asyncio
import json
import os
import struct
import time
import uuid
from urllib.parse import urlsplit, urlunsplit

import opuslib_next
from websockets.asyncio.client import connect


async def check():
    url, token = os.environ['SOUL_EAR_URL'], os.environ['SOUL_EAR_TOKEN']
    parts = urlsplit(url)
    if parts.query or parts.fragment or parts.username or parts.password:
        raise ValueError('smoke URL must not contain credentials')
    events, ticks = [], []
    async with connect(url, additional_headers={'Authorization':'Bearer '+token},
                       open_timeout=10, max_size=16384) as ws:
        async def receive():
            async for payload in ws:
                tick=json.loads(payload)
                ticks.append(tick)
                events.extend(tick.get('events', []))
        reader=asyncio.create_task(receive())
        async def wait_for(predicate, seconds=8):
            until=time.monotonic()+seconds
            while not predicate():
                if reader.done(): await reader
                if time.monotonic()>=until: raise TimeoutError('backend event timeout')
                await asyncio.sleep(.02)
        await wait_for(lambda:any(e.get('type')=='hello' for e in events))
        await ws.send(json.dumps({'type':'audio','format':'opus','sample_rate':24000,'channels':1,
                                 'frame_ms':60,'framing':'sea1','source':'stackchan-protocol-smoke',
                                 'device':{'protocol':'soul-ear/1','fixture':True}}))
        await wait_for(lambda:any(e.get('type')=='audio' and e.get('input_sample_rate')==24000 for e in events))
        encoder=opuslib_next.Encoder(24000,1,'audio')
        for seq in range(20):
            await ws.send(json.dumps({'type':'self','sounding':seq<5,'moving':False}))
            payload=encoder.encode(bytes(2880),1440)
            await ws.send(b'SEA1'+struct.pack('<IQ',seq,1000000+seq*60000)+payload)
            await asyncio.sleep(.06)
        await wait_for(lambda:ticks and ticks[-1].get('rx',0)>=19200)
        request_id=uuid.uuid4().hex
        await ws.send(json.dumps({'type':'enroll','name':'protocol-smoke-'+request_id[:8],
                                 'request_id':request_id,'count':5,'replace':False}))
        await wait_for(lambda:any(e.get('request_id')==request_id and e.get('phase')=='started' for e in events))
        await ws.send('{"type":"enroll_cancel"}')
        await wait_for(lambda:any(e.get('request_id')==request_id and e.get('phase')=='cancelled' for e in events))
        assert not any(e.get('request_id')==request_id and e.get('phase')=='done' for e in events)
        assert not any(e.get('type')=='error' for e in events)
        reader.cancel()
        await asyncio.gather(reader,return_exceptions=True)
    # Legacy Mac query-token client remains compatible; exact wrong token must close.
    from urllib.parse import urlencode
    for supplied, expected in ((token,True),(token+'invalid',False)):
        uri=urlunsplit((parts.scheme,parts.netloc,parts.path,urlencode({'token':supplied}),''))
        received=False
        try:
            async with connect(uri,open_timeout=10) as ws:
                value=json.loads(await asyncio.wait_for(ws.recv(),3))
                received=any(e.get('type')=='hello' for e in value.get('events',[]))
        except Exception:
            if expected: raise RuntimeError('legacy client handshake failed') from None
        assert received==expected
    print(json.dumps({'hello':True,'bearer_auth':True,'legacy_auth':True,'bad_token_rejected':True,
                      'opus_input_hz':24000,'decoded_samples':ticks[-1]['rx'],
                      'enrollment_cancelled':True,'voiceprint_saved':False}))


if __name__=='__main__':
    try: asyncio.run(check())
    except Exception as error:
        # Library exceptions can include a URI. Never print a secret-bearing exception.
        print(json.dumps({'ok':False,'error_type':type(error).__name__}))
        raise SystemExit(1)
