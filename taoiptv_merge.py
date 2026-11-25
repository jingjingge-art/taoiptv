#!/usr/bin/env python3
"""Build a categorized playlist from the newest usable tao IPTV token."""
from __future__ import annotations

import os
import re
import sys
import tempfile
from pathlib import Path
from urllib.parse import urlsplit

import requests

SOURCE_ENDPOINTS = (
    "https://taoiptv.com/lives/31023.txt",
    "https://taoiptv.com/lives/32023.txt",
    "https://taoiptv.com/lives/33023.txt",
)
ALLOWED_SCHEMES = {"http", "https", "rtsp", "rtmp", "rtp", "udp"}
PLACEHOLDERS = {"your_token_here", "yourtokenhere", "replace_me", "token", "changeme"}
CATEGORY_ORDER = ["央视", "江苏省", "浙江省", "上海市", "江浙沪地区", "卫视", "调频广播", "歌曲MV", "其他"]


def read_latest_token(path: Path) -> str:
    if not path.is_file():
        raise FileNotFoundError(f"Token file not found: {path}")
    lines = path.read_text(encoding="utf-8-sig").splitlines()
    for raw in reversed(lines):
        token = raw.strip()
        if not token or token.startswith("#"):
            continue
        compact = re.sub(r"[\s_-]+", "", token).casefold()
        if compact in {"yourtokenhere", "token", "replace", "replaceme", "changeme"}:
            continue
        if any(char.isspace() for char in token):
            raise ValueError("The newest token line contains whitespace; use one token with no spaces.")
        if token.casefold() in PLACEHOLDERS:
            continue
        return token
    raise ValueError(f"No usable token in {path}; remove placeholders and add one token per line.")


def is_stream_url(value: str) -> bool:
    try:
        return urlsplit(value).scheme.lower() in ALLOWED_SCHEMES
    except ValueError:
        return False


def normalize_name(value: str) -> str:
    value = re.sub(r"\s+", " ", value).strip()
    value = re.sub(r"\s*[\[(【].*?[\])】]\s*", " ", value)
    value = re.sub(r"(?i)(高清|超清|标清|流畅|HD|4K|8K|IPV[46])", "", value)
    return re.sub(r"\s+", " ", value).strip() or "未知频道"


def classify(name: str) -> str:
    if re.search(r"CCTV|中央电视台|央视频道|中央台", name, re.I):
        return "央视"
    if re.search(r"江苏|南京|苏州|无锡|常州|徐州|南通|连云港|淮安|盐城|扬州|镇江|泰州|宿迁", name):
        return "江苏省"
    if re.search(r"浙江|杭州|宁波|温州|嘉兴|湖州|绍兴|金华|衢州|舟山|台州|丽水", name):
        return "浙江省"
    if "上海" in name:
        return "上海市"
    if "卫视" in name:
        return "卫视"
    if re.search(r"广播|FM|电台", name, re.I):
        return "调频广播"
    if re.search(r"MTV|MV|歌曲|音乐", name, re.I):
        return "歌曲MV"
    if re.search(r"新闻|综合|都市|民生|公共|生活|教育|影视|经济|科教|文艺|少儿|体育|纪录", name):
        return "江浙沪地区"
    return "其他"


def parse_playlist(text: str) -> list[tuple[str, str, str, str]]:
    """Parse M3U and plain-text name/URL rows (comma, pipe, or whitespace separated)."""
    channels: list[tuple[str, str, str, str]] = []
    pending_name = ""
    pending_extinf = ""
    for raw in text.lstrip("\ufeff").splitlines():
        line = raw.strip()
        if not line:
            continue
        if line.upper().startswith("#EXTINF"):
            pending_extinf = line
            match = re.search(r",\s*(.+)$", line)
            pending_name = match.group(1).strip() if match else "未知频道"
            continue
        if line.startswith("#"):
            continue
        if pending_name and is_stream_url(line):
            name = normalize_name(pending_name)
            logo_match = re.search(r'tvg-logo="([^"]*)"', pending_extinf)
            logo = logo_match.group(1).strip() if logo_match else ""
            channels.append((classify(name), name, line, logo))
            pending_name = pending_extinf = ""
            continue

        # Common tao IPTV text formats: channel,url; channel|url; channel URL.
        match = re.match(
            r"^\s*(.*?)\s*(?:,|\|\s*|\s+)\s*((?:https?|rtsp|rtmp|rtp|udp)://\S+)\s*$",
            line,
            re.I,
        )
        if match and match.group(1).strip() and is_stream_url(match.group(2)):
            name = normalize_name(match.group(1))
            channels.append((classify(name), name, match.group(2), ""))
        pending_name = pending_extinf = ""
    return channels


def fetch_endpoint(session: requests.Session, endpoint: str, token: str) -> list[tuple[str, str, str, str]]:
    response = session.get(endpoint, params={"token": token}, timeout=(10, 30))
    if not response.ok:
        raise RuntimeError(f"{urlsplit(endpoint).path}: HTTP {response.status_code}")
    channels = parse_playlist(response.text)
    if not channels:
        filename = urlsplit(endpoint).path.rsplit("/", 1)[-1]
        raise RuntimeError(
            f"{filename}: response had no parseable channel entries "
            f"(HTTP {response.status_code}, {len(response.content)} bytes)"
        )
    return channels


def render_playlist(channels: list[tuple[str, str, str, str]]) -> str:
    grouped: dict[str, list[tuple[str, str, str, str]]] = {}
    for item in channels:
        grouped.setdefault(item[0], []).append(item)
    lines = ["#EXTM3U"]
    order = CATEGORY_ORDER + [category for category in grouped if category not in CATEGORY_ORDER]
    for category in order:
        items = grouped.get(category, [])
        if not items:
            continue
        lines.append(f"# ===== {category} =====")
        for _, name, stream_url, logo in sorted(items, key=lambda item: (item[1].casefold(), item[2])):
            safe_name = name.replace('"', "'")
            safe_logo = logo.replace('"', "'")
            logo_attr = f' tvg-logo="{safe_logo}"' if safe_logo else ""
            lines.append(f'#EXTINF:-1 tvg-name="{safe_name}" group-title="{category}"{logo_attr},{safe_name}')
            lines.append(stream_url)
    return "\n".join(lines) + "\n"


def atomic_write(path: Path, content: str) -> None:
    """Only replace the playlist after a complete successful refresh."""
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temp_name = tempfile.mkstemp(prefix=f".{path.name}.", suffix=".tmp", dir=path.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as output:
            output.write(content)
            output.flush()
            os.fsync(output.fileno())
        os.replace(temp_name, path)
    except Exception:
        try:
            os.unlink(temp_name)
        except FileNotFoundError:
            pass
        raise


def main() -> int:
    output_path = Path("iptv.txt")
    try:
        token = read_latest_token(Path("taoiptv_tokens.txt"))
        print("Using exactly one newest usable token (value hidden).")
        with requests.Session() as session:
            session.headers.update({"User-Agent": "Mozilla/5.0", "Accept": "text/plain,application/x-mpegURL,*/*"})
            collected: list[tuple[str, str, str, str]] = []
            for endpoint in SOURCE_ENDPOINTS:
                entries = fetch_endpoint(session, endpoint, token)
                collected.extend(entries)
                print(f"Fetched {len(entries)} channels from {urlsplit(endpoint).path}")

        unique = list(dict.fromkeys(collected))
        if not unique:
            raise RuntimeError("No channels collected")
        atomic_write(output_path, render_playlist(unique))
        print(f"Successfully wrote {len(unique)} channels to {output_path}.")
        return 0
    except Exception as exc:
        # Never include request URLs in error messages: they contain the token.
        print(f"ERROR: {exc}", file=sys.stderr)
        print("Refresh failed; existing iptv.txt was left untouched.", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
