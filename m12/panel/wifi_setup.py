#!/usr/bin/env python3
"""Make the UnitV2 join WiFi networks (wlan0 / wpa_supplicant). Its own hotspot (wlan1) is untouched.

You type the passwords here (hidden). They are turned into WPA PSK hashes on this Mac; only the hashes
are written to the device's /etc/wpa_supplicant.conf (a backup is kept as .bak).

Usage:  python3 wifi_setup.py            add/replace networks, then show connection status
        python3 wifi_setup.py --status   only show connection status
        python3 wifi_setup.py --dry-run  show the new config (PSKs masked) without writing
Env:    UNITV2_HOST (10.254.239.1)  UNITV2_USER (m5stack)  UNITV2_PASS (12345678, also used for sudo)
"""
import getpass
import hashlib
import os
import re
import subprocess
import sys
import time

HOST = os.environ.get("UNITV2_HOST", "10.254.239.1")
USER = os.environ.get("UNITV2_USER", "m5stack")
PASS = os.environ.get("UNITV2_PASS", "12345678")
HERE = os.path.dirname(os.path.abspath(__file__))
CONF = "/etc/wpa_supplicant.conf"


def ssh(cmd, stdin_text=None, timeout=60):
    argv = ["ssh", "-T", "-o", "StrictHostKeyChecking=no", "-o", "UserKnownHostsFile=/dev/null",
            "-o", "LogLevel=ERROR", "-o", "PubkeyAuthentication=no",
            "-o", "PreferredAuthentications=password,keyboard-interactive",
            "-o", "NumberOfPasswordPrompts=1", "-o", "ConnectTimeout=8", f"{USER}@{HOST}", cmd]
    env = dict(os.environ, SSH_ASKPASS=os.path.join(HERE, "askpass.sh"), SSH_ASKPASS_REQUIRE="force",
               UNITV2_PASS=PASS)
    env.setdefault("DISPLAY", ":0")
    # Never let ssh inherit our stdin: it would swallow what the user types at the prompts.
    kw = {"input": stdin_text} if stdin_text is not None else {"stdin": subprocess.DEVNULL}
    r = subprocess.run(argv, capture_output=True, text=True, env=env, timeout=timeout, **kw)
    return r.returncode, r.stdout, r.stderr


def sudo(cmd, extra_stdin=""):
    # sudo -S reads the password line from stdin; the rest of stdin goes to the command.
    return ssh(f"sudo -S -p '' {cmd}", stdin_text=PASS + "\n" + extra_stdin)


def parse_conf(text):
    header, blocks, cur = [], [], None
    for line in text.splitlines():
        s = line.strip()
        if cur is None and s.startswith("network={"):
            cur = [line]
        elif cur is not None:
            cur.append(line)
            if s == "}":
                blocks.append(cur)
                cur = None
        else:
            header.append(line)
    return header, blocks


def block_ssid(block):
    for line in block:
        m = re.match(r"\s*ssid=(.*)$", line)
        if m:
            v = m.group(1).strip()
            if len(v) >= 2 and v[0] == '"' and v[-1] == '"':
                return v[1:-1].encode("utf-8")
            try:
                return bytes.fromhex(v)
            except ValueError:
                return v.encode("utf-8")
    return None


def make_block(ssid, password, priority):
    sb = ssid.encode("utf-8")
    psk = hashlib.pbkdf2_hmac("sha1", password.encode("utf-8"), sb, 4096, 32).hex()
    return ["network={",
            f"\t# {ssid}  (added by wifi_setup.py)",
            f"\tssid={sb.hex()}",
            "\tscan_ssid=1",
            "\tkey_mgmt=WPA-PSK",
            f"\tpsk={psk}",
            f"\tpriority={priority}",
            "}"]


def status():
    code, out, err = sudo("wpa_cli -i wlan0 status")
    if code != 0:
        return None, (err or out).strip()
    st = dict(l.split("=", 1) for l in out.splitlines() if "=" in l)
    _, ipout, _ = ssh("ip -4 addr show wlan0")
    m = re.search(r"inet (\d+\.\d+\.\d+\.\d+)", ipout)
    st["ip"] = m.group(1) if m else ""
    return st, ""


def show_status(wait=0, hints=True):
    end = time.time() + wait
    while True:
        st, err = status()
        if st is None:
            print(f"读取状态失败：{err}")
            return False
        state = st.get("wpa_state", "?")
        ssid = st.get("ssid", "")
        if state == "COMPLETED" and st["ip"]:
            print(f"\n✓ 已连接到「{ssid}」，设备在这个网络里的 IP 是 {st['ip']}")
            print(f"  面板可以这样启动（在仓库根目录）：UNITV2_HOST={st['ip']} python3 m12/panel/server.py")
            return True
        if time.time() >= end:
            print(f"\n✕ 没有连上 WiFi（wpa_state={state}{'，ssid=' + ssid if ssid else ''}{'，IP=' + st['ip'] if st['ip'] else ''}）")
            if hints:
                print("  请检查：名字是否完全一致（大小写、空格）、密码是否正确、这个 WiFi 是否有 2.4GHz 频段。")
            return False
        print(f"  等待连接…（{state}）", flush=True)
        time.sleep(3)


def main():
    if "--status" in sys.argv:
        show_status(hints=False)
        return
    dry = "--dry-run" in sys.argv

    code, current, err = ssh(f"cat {CONF}")
    if code != 0:
        sys.exit(f"连不上设备 {HOST}：{(err or current).strip()}")
    header, blocks = parse_conf(current)
    have = [block_ssid(b) for b in blocks]
    print(f"设备上现有 {len(blocks)} 个已保存的 WiFi"
          + (f"：{', '.join(s.decode('utf-8', 'replace') for s in have if s)}" if blocks else "") + "。")
    print("按优先级依次输入要连接的 WiFi（先输入的优先连接）。名字直接回车表示输入完毕。\n")

    new = []
    while True:
        try:
            ssid = input(f"第 {len(new) + 1} 个 WiFi 的名字：").strip("\r\n")
        except EOFError:
            ssid = ""
        if not ssid:
            break
        if len(ssid.encode("utf-8")) > 32:
            print("  名字太长（最多 32 字节），请重新输入。")
            continue
        while True:
            pw = getpass.getpass("  密码（输入时不显示）：")
            if 8 <= len(pw) <= 63:
                break
            print("  WPA 密码长度必须是 8～63 个字符，请重新输入。")
        new.append((ssid, pw))
    if not new:
        print("没有输入任何 WiFi，未做修改。")
        return

    new_ssids = {s.encode("utf-8") for s, _ in new}
    kept = [b for b in blocks if block_ssid(b) not in new_ssids]
    base = max([0] + [int(m.group(1)) for b in kept for l in b if (m := re.match(r"\s*priority=(\d+)", l))])
    added = [make_block(s, p, base + len(new) - i) for i, (s, p) in enumerate(new)]
    text = "\n".join(header + [l for b in kept + added for l in b]) + "\n"

    if dry:
        print("\n--- 新配置预览（PSK 已隐藏，没有写入设备）---")
        print(re.sub(r"psk=[0-9a-f]{64}", "psk=<hidden>", text))
        return

    code, out, err = sudo(f"sh -c 'cp {CONF} {CONF}.bak && cat > {CONF} && wpa_cli -i wlan0 reconfigure'", text)
    if code != 0:
        sys.exit(f"写入失败：{(err or out).strip()}")
    print(f"\n已写入 {len(added)} 个 WiFi（原配置备份在 {CONF}.bak），正在连接…")
    show_status(wait=40)


if __name__ == "__main__":
    try:
        main()
    except (KeyboardInterrupt, EOFError):
        print("\n已取消，未做修改。")
