"""Make a voice's filler clips (我看看 / 我查查) with the Qwen realtime model itself, like the ly01 ones."""
import asyncio, base64, json, os, sys
import websockets
VOICE = sys.argv[1]
env = {}
for l in open(os.path.expanduser("~/soul-spike/hello.env")):
    if "=" in l and not l.startswith("#"):
        k, v = l.strip().split("=", 1); env[k] = v.strip().strip('"')
key = env["DASHSCOPE_API_KEY"]
vid = json.load(open(os.path.expanduser("~/soul-tts/soul_tts/voices.json")))["voices"][VOICE]["voice"]
URL = "wss://dashscope.aliyuncs.com/api-ws/v1/realtime?model=qwen-audio-3.1-realtime-flash"
base = os.path.expanduser("~/soul-spike/fillers")
async def say(text):
    async with websockets.connect(URL, additional_headers={"Authorization": f"Bearer {key}"}, max_size=None) as ws:
        await ws.send(json.dumps({"type": "session.update", "session": {"modalities": ["text", "audio"], "voice": vid,
            "output_audio_format": "pcm", "turn_detection": None,
            "instructions": "你是配音员。把用户发来的那句话原样念出来，一个字都不加、不改、不回答，语气自然口语。"}}))
        await ws.send(json.dumps({"type": "conversation.item.create", "item": {"type": "message", "role": "user", "content": [{"type": "input_text", "text": text}]}}))
        await ws.send(json.dumps({"type": "response.create"}))
        pcm, tx = b"", ""
        while True:
            m = json.loads(await asyncio.wait_for(ws.recv(), 20))
            if m.get("type") == "response.audio.delta": pcm += base64.b64decode(m.get("delta") or "")
            if m.get("type") == "response.audio_transcript.delta": tx += m.get("delta") or ""
            if m.get("type") in ("response.done", "error"): break
        return pcm, tx
async def main():
    for src in ("ly01", "ly01-search"):
        dst = os.path.join(base, src.replace("ly01", VOICE))
        meta = json.load(open(os.path.join(base, src, "fillers.json")))
        os.makedirs(dst, exist_ok=True)
        clips = []
        for c in meta["clips"]:
            for _ in range(3):
                pcm, tx = await say(c["text"])
                norm = lambda s: "".join(ch for ch in s if ch.isalnum())
                if pcm and norm(tx) == norm(c["text"]): break
            if not pcm or norm(tx) != norm(c["text"]):
                print("skip", c["text"], "->", tx); continue
            open(os.path.join(dst, c["file"]), "wb").write(pcm)
            clips.append({"file": c["file"], "text": c["text"], "ms": len(pcm) * 1000 // (24000 * 2)})
        json.dump({"sample_rate": 24000, "clips": clips, "voice": VOICE, "made": "qwen realtime, 2026-11-18"},
                  open(os.path.join(dst, "fillers.json"), "w"), ensure_ascii=False)
        print(dst, [(c["text"], c["ms"]) for c in clips])
asyncio.run(main())
