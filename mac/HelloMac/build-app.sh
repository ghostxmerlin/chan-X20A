#!/bin/sh
# Build hello.app (release): mac/HelloMac/build/hello.app, eyes GIFs inside, ad-hoc signed (camera / microphone
# permission prompts need the usage strings in Info.plist). Run setup.sh once before the first start.
set -eu
HERE=$(cd "$(dirname "$0")" && pwd)
ROOT=$(cd "$HERE/../.." && pwd)
cd "$HERE"
swift build -c release --product HelloMac 2>&1 | grep -v "ld: warning" | tail -2
APP="$HERE/build/hello.app"
rm -rf "$APP"
mkdir -p "$APP/Contents/MacOS" "$APP/Contents/Resources/eyes"
cp .build/release/HelloMac "$APP/Contents/MacOS/hello"
for s in v05 v14 v18; do mkdir -p "$APP/Contents/Resources/eyes/$s"; cp "$ROOT/assets/soul-eyes/v2-front/previews/$s/"*.gif "$APP/Contents/Resources/eyes/$s/"; done
# hello's own voice lines (the robot's SD library; .private, never in the repo): the main checkout's copy
MAIN=$(git -C "$ROOT" worktree list --porcelain | sed -n '1s/^worktree //p')
LIB=""
for d in "$ROOT/.private/voice-library" "$MAIN/.private/voice-library"; do [ -s "$d/PLAY-001.reference.pcm" ] && { LIB="$d"; break; }; done
if [ -n "$LIB" ]; then
  mkdir -p "$APP/Contents/Resources/voices"
  for f in "$LIB"/PLAY-*.reference.pcm; do b=$(basename "$f" .reference.pcm); cp "$f" "$APP/Contents/Resources/voices/$b.pcm"; done
  cp "$ROOT/config/voice-emotions.json" "$APP/Contents/Resources/voice-emotions.json"
  echo "voices: $(ls "$APP/Contents/Resources/voices" | wc -l | tr -d ' ') clips"
else echo "voices: none (no .private/voice-library): no response clips"; fi
VER=$(git -C "$ROOT" rev-parse --short HEAD 2>/dev/null || echo dev)
cat > "$APP/Contents/Info.plist" <<PLIST
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0"><dict>
 <key>CFBundleExecutable</key><string>hello</string>
 <key>CFBundleIdentifier</key><string>local.soul.hello-mac</string>
 <key>CFBundleName</key><string>hello</string>
 <key>CFBundleDisplayName</key><string>hello</string>
 <key>CFBundlePackageType</key><string>APPL</string>
 <key>CFBundleShortVersionString</key><string>0.1.0</string>
 <key>CFBundleVersion</key><string>$VER</string>
 <key>LSMinimumSystemVersion</key><string>14.0</string>
 <key>NSHighResolutionCapable</key><true/>
 <key>NSMicrophoneUsageDescription</key><string>hello 用麦克风听你说话（送到你自己的服务器上的耳朵）。</string>
 <key>NSSpeechRecognitionUsageDescription</key><string>hello 的语音口令（hello、play、turn left……）只在这台 Mac 上识别，声音不上传。</string>
 <key>NSCameraUsageDescription</key><string>hello 用摄像头看人和画面（认人在本机；画面变了才传到你自己的服务器）。</string>
</dict></plist>
PLIST
codesign --force --sign - "$APP" >/dev/null 2>&1 && echo "signed (ad hoc)"
du -sh "$APP" | cut -f1 | sed 's/^/size: /'
echo "$APP"
