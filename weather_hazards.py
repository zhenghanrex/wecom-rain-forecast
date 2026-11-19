import argparse
from datetime import datetime, timedelta, timezone
import json
from math import asin, cos, radians, sin, sqrt
from pathlib import Path
from urllib.parse import urlencode
from urllib.request import urlopen

from rain_forecast import BEIJING, LOCATIONS, fetch_json, send

NMC = "https://www.nmc.cn"
TYPHOON = "https://typhoon.nmc.cn"
TARGETS = {"445200", "445202", "445281"}
RADIUS_KM = 250


def local_warnings(_now):
    page = 1
    while True:
        query = urlencode({
            "pageNo": page, "pageSize": 20, "signaltype": "",
            "signallevel": "", "province": "广东省",
        })
        result = fetch_json(f"{NMC}/rest/findAlarm?{query}")
        if result["code"] != 0:
            raise ValueError("中央气象台未返回预警列表")
        records = result["data"]["page"]
        for alert in records["list"]:
            alert_id = alert["alertid"]
            if alert_id[:6] in TARGETS:
                yield f"warning:{alert_id}", (
                    f"⚠️ 气象预警\n{alert['title']}\n"
                    f"发布时间：{alert['issuetime']}（北京时间）\n"
                    f"{NMC}/publish/alarm/{alert_id}.html"
                )
        if page >= records["totalPage"]:
            return
        page += 1


def typhoon_json(resource):
    with urlopen(f"{TYPHOON}/weatherservice/typhoon/jsons/{resource}", timeout=20) as response:
        body = response.read().decode("utf-8")
    return json.loads(body[body.index("{"):body.rindex("}") + 1])


def distance(lat_a, lon_a, lat_b, lon_b):
    lat_a, lon_a, lat_b, lon_b = map(radians, (lat_a, lon_a, lat_b, lon_b))
    value = sin((lat_b - lat_a) / 2)**2 + (
        cos(lat_a) * cos(lat_b) * sin((lon_b - lon_a) / 2)**2
    )
    return 12742 * asin(sqrt(min(value, 1)))


def approach_time(analysis, now):
    base = datetime.strptime(analysis[1], "%Y%m%d%H%M").replace(tzinfo=timezone.utc)
    forecasts = (analysis[11] or {}).get("BABJ", []) if len(analysis) > 11 else []
    points = [(0, float(analysis[5]), float(analysis[4]))]
    points.extend(sorted((int(row[0]), float(row[3]), float(row[2])) for row in forecasts))
    for (first, lat_a, lon_a), (last, lat_b, lon_b) in zip(points, points[1:]):
        if last <= first:
            continue
        for offset in range(first, last + 1):
            arrival = base + timedelta(hours=offset)
            if not now <= arrival <= now + timedelta(hours=72):
                continue
            fraction = (offset - first) / (last - first)
            latitude = lat_a + (lat_b - lat_a) * fraction
            longitude = lon_a + (lon_b - lon_a) * fraction
            if any(distance(latitude, longitude, lat, lon) <= RADIUS_KM
                   for _, lat, lon in LOCATIONS):
                return arrival.astimezone(BEIJING)
    return None


def typhoon_outlook(now):
    years = [now.year]
    if now.month == 1 and now.day <= 3:
        years.append(now.year - 1)
    seen = set()
    for year in years:
        for storm in typhoon_json(f"list_{year}")["typhoonList"]:
            storm_id, english, chinese = storm[:3]
            if storm[-1] == "stop" or storm_id in seen:
                continue
            seen.add(storm_id)
            observations = typhoon_json(f"view_{storm_id}")["typhoon"][8]
            latest = max(observations, key=lambda item: item[1])
            arrival = approach_time(latest, now)
            if arrival:
                name = chinese.strip() if chinese and chinese != "null" else english
                yield f"typhoon:{storm_id}", (
                    f"🌀 未来72小时台风路径提醒｜{name}\n"
                    f"按中央气象台路径估算，{arrival:%m-%d %H:%M}前后"
                    f"台风中心可能进入距梅云街道或南溪镇{RADIUS_KM}公里范围"
                    "（北京时间）。\n"
                    f"实际风雨影响请关注揭阳当地气象台预警。\n{TYPHOON}/web.html"
                )


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--preview", action="store_true")
    args = parser.parse_args()
    now = datetime.now(BEIJING)
    state_file = Path(".hazard-state/sent.json")
    state = json.loads(state_file.read_text()) if not args.preview and state_file.exists() else {}
    cutoff = (now - timedelta(days=30)).timestamp()
    state = {key: value for key, value in state.items() if value >= cutoff}
    if not args.preview:
        state_file.parent.mkdir(exist_ok=True)
        state_file.write_text(json.dumps(state))

    failures = []
    for source, label in ((local_warnings, "本地气象预警"), (typhoon_outlook, "台风路径")):
        try:
            count = 0
            for key, message in source(now):
                count += 1
                if args.preview:
                    print(message)
                elif key not in state:
                    send(message)
                    state[key] = now.timestamp()
                    state_file.write_text(json.dumps(state))
                    print(f"{label}已推送")
            print(f"{label}查询成功，匹配 {count} 条")
        except (OSError, ValueError, KeyError, IndexError, TypeError, SystemExit) as error:
            print(f"{label}检查或发送失败：{type(error).__name__}")
            failures.append(label)
    if failures:
        raise SystemExit("未完成：" + "、".join(failures))


if __name__ == "__main__":
    main()
