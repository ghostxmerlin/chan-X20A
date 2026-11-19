"""Tool fillers by situation (owner 2026-11-19: 「我查查」 needs ~10 more, picked by the situation, at random).

Makes the clips with the Qwen realtime model itself in a voice (like make_fillers.py), into
~/soul-spike/fillers/<voice>-<group>/ (fillers.json + PCM16 24 kHz mono). Existing clips are kept; a line already there
is not made again. Run on the server:  ~/soul-spike/.venv/bin/python make_tool_fillers.py ly01   (then me01)
"""
import asyncio, base64, json, os, sys
import websockets

GROUPS = {   # the ear picks the group from the tool (realtime_link.FILLER_GROUP); 看 uses the look fillers (<voice>/)
    "search": ["稍等，我上网看看", "我找找看", "马上，我查一下", "我看看网上怎么说"],      # + the 4 made 2026-11-16/18
    "recall": ["我想想啊", "让我回忆一下", "我翻翻记录", "等我想一下"],
    "daily": ["好，我记一下", "稍等，我设一下", "我给你记上"],
}
VOICE = sys.argv[1]
env = {}
for l in open(os.path.expanduser("~/soul-spike/hello.env")):
    if "=" in l and not l.startswith("#"):
        k, v = l.strip().split("=", 1); env[k] = v.strip().strip('"')
key = env["DASHSCOPE_API_KEY"]
vid = json.load(open(os.path.expanduser("~/soul-tts/soul_tts/voices.json")))["voices"][VOICE]["voice"]
URL = "wss://dashscope.aliyuncs.com/api-ws/v1/realtime?model=qwen-audio-3.1-realtime-flash"
BASE = os.path.expanduser("~/soul-spike/fillers")
norm = lambda s: "".join(ch for ch in s if ch.isalnum())   # noqa: E731


async def say(text):
    async with websockets.connect(URL, additional_headers={"Authorization": f"Bearer {key}"}, max_size=None) as ws:
        await ws.send(json.dumps({"type": "session.update", "session": {"modalities": ["text", "audio"], "voice": vid,
            "output_audio_format": "pcm", "turn_detection": None,
            "instructions": "你是配音员。把用户发来的那句话原样念出来，一个字都不加、不改、不回答，语气自然口语。"}}))
        await ws.send(json.dumps({"type": "conversation.item.create", "item": {"type": "message", "role": "user",
                                  "content": [{"type": "input_text", "text": text}]}}))
        await ws.send(json.dumps({"type": "response.create"}))
        pcm, tx = b"", ""
        while True:
            m = json.loads(await asyncio.wait_for(ws.recv(), 20))
            if m.get("type") == "response.audio.delta":
                pcm += base64.b64decode(m.get("delta") or "")
            if m.get("type") == "response.audio_transcript.delta":
                tx += m.get("delta") or ""
            if m.get("type") in ("response.done", "error"):
                break
        return pcm, tx


async def main():
    for group, lines in GROUPS.items():
        dst = os.path.join(BASE, f"{VOICE}-{group}")
        os.makedirs(dst, exist_ok=True)
        meta_p = os.path.join(dst, "fillers.json")
        meta = json.load(open(meta_p)) if os.path.exists(meta_p) else {"sample_rate": 24000, "clips": [], "voice": VOICE}
        have = {norm(c["text"]) for c in meta["clips"]}
        n = max([int(os.path.splitext(c["file"])[0]) for c in meta["clips"] if c["file"][0].isdigit()] or [0])
        for text in lines:
            if norm(text) in have:
                continue
            for _ in range(3):
                pcm, tx = await say(text)
                if pcm and norm(tx) == norm(text):
                    break
            if not pcm or norm(tx) != norm(text) or len(pcm) > 24000 * 2 * 4:
                print("skip", group, text, "->", tx)
                continue
            n += 1
            open(os.path.join(dst, f"{n}.pcm"), "wb").write(pcm)
            meta["clips"].append({"file": f"{n}.pcm", "text": text, "ms": len(pcm) * 1000 // (24000 * 2)})
        meta["made"] = "qwen realtime, 2026-11-19 (tool fillers by situation)"
        json.dump(meta, open(meta_p + ".tmp", "w"), ensure_ascii=False, indent=1)
        os.replace(meta_p + ".tmp", meta_p)
        print(dst, [(c["text"], c["ms"]) for c in meta["clips"]])


asyncio.run(main())
