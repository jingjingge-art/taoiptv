#!/usr/bin/env python3
from pathlib import Path
import re
import sys

SOURCE_TEMPLATES = (
    "https://taoiptv.com/lives/31023.txt?token={token}",
    "https://taoiptv.com/lives/32023.txt?token={token}",
    "https://taoiptv.com/lives/33023.txt?token={token}",
)


def read_tokens(path: Path) -> list[str]:
    tokens = []
    if not path.exists():
        raise FileNotFoundError(f"Missing token file: {path}")
    for line in path.read_text(encoding="utf-8").splitlines():
        value = line.strip()
        if not value or value.startswith("#"):
            continue
        tokens.append(value)
    if not tokens:
        raise ValueError(f"No token found in {path}")
    return tokens


def fetch_lines(url: str) -> list[str]:
    try:
        import requests
        response = requests.get(url, timeout=20, headers={"User-Agent": "Mozilla/5.0"})
        response.raise_for_status()
        return response.text.splitlines()
    except Exception as exc:
        print(f"WARN: failed to fetch {url}: {exc}", file=sys.stderr)
        return []


def normalize_name(value: str) -> str:
    value = re.sub(r"\s+", "", value)
    value = re.sub(r"\[.*?\]|\(.*?\)|【.*?】", "", value)
    value = re.sub(r"(?i)(高清|超清|标清|流畅|HD|4K|8K|IPV[46])", "", value)
    return value.strip()


def classify(name: str) -> str:
    upper = name.upper()
    if re.search(r"CCTV|中央电视台|央视频道|中央台", name, re.I):
        return "央视"
    if re.search(r"江苏|南京|苏州|无锡|常州|徐州|南通|连云港|淮安|盐城|扬州|镇江|泰州|宿迁", name):
        return "江苏省"
    if re.search(r"浙江|杭州|宁波|温州|嘉兴|湖州|绍兴|金华|衢州|舟山|台州|丽水", name):
        return "浙江省"
    if re.search(r"上海", name):
        return "上海市"
    if re.search(r"卫视", name):
        return "卫视"
    if re.search(r"新闻|综合|都市|民生|公共|生活|教育|影视|经济|科教|文艺|少儿|体育|纪录", name):
        return "江浙沪地区"
    if re.search(r"广播|FM|电台", name, re.I):
        return "调频广播"
    if re.search(r"MTV|MV|歌曲|音乐", name):
        return "歌曲MV"
    return "其他"


def parse_playlist(lines: list[str]) -> list[tuple[str, str, str, str]]:
    channels = []
    name = ""
    attributes = ""
    for line in lines:
        line = line.strip()
        if not line:
            continue
        if line.upper().startswith("#EXTINF"):
            attributes = line
            match = re.search(r",\s*(.+)$", line)
            name = normalize_name(match.group(1).strip()) if match else "未知频道"
        elif not line.startswith("#"):
            if name:
                category = classify(name)
                logo_match = re.search(r'tvg-logo="([^"]*)"', attributes)
                logo = logo_match.group(1) if logo_match else ""
                channels.append((category, name, line, logo))
            name = ""
            attributes = ""
    return channels


def main() -> int:
    token_file = Path("taoiptv_tokens.txt")
    output_file = Path("iptv.txt")
    try:
        tokens = read_tokens(token_file)
    except Exception as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        print("Create taoiptv_tokens.txt with one token per line.", file=sys.stderr)
        return 1

    merged = []
    seen = set()
    for token in tokens:
        for template in SOURCE_TEMPLATES:
            url = template.format(token=token)
            for category, name, stream_url, logo in parse_playlist(fetch_lines(url)):
                key = (name, stream_url)
                if key not in seen:
                    seen.add(key)
                    merged.append((category, name, stream_url, logo))

    if not merged:
        print("ERROR: no channels downloaded", file=sys.stderr)
        return 2

    category_order = ["央视", "江苏省", "浙江省", "上海市", "江浙沪地区", "卫视", "调频广播", "歌曲MV", "其他"]
    grouped = {category: [] for category in category_order}
    for item in merged:
        grouped[item[0]].append(item)

    lines = ["#EXTM3U"]
    for category in category_order:
        items = grouped[category]
        if not items:
            continue
        lines.append(f"# ===== {category} =====")
        for _, name, stream_url, logo in sorted(items, key=lambda x: x[1]):
            logo_attr = f' tvg-logo="{logo}"' if logo else ""
            lines.append(f'#EXTINF:-1 tvg-name="{name}" group-title="{category}"{logo_attr},{name}')
            lines.append(stream_url)

    output_file.write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(f"Wrote {len(merged)} channels to {output_file}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
