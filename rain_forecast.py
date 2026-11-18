import argparse
from datetime import datetime, timedelta, timezone
import json
import os
from urllib.parse import urlencode, urlsplit, parse_qs
from urllib.request import Request, urlopen

BEIJING = timezone(timedelta(hours=8))
LOCATIONS = (
    ("榕城区梅云街道", 23.51608, 116.30350),
    ("普宁市南溪镇", 23.49757, 116.24353),
)


def fetch_json(request):
    with urlopen(request, timeout=20) as response:
        return json.load(response)


def forecast(day):
    start = datetime.combine(day, datetime.min.time(), tzinfo=BEIJING)
    expected = [start + timedelta(hours=i) for i in range(1, 25)]
    lines = []
    for name, latitude, longitude in LOCATIONS:
        query = urlencode({
            "latitude": latitude,
            "longitude": longitude,
            "hourly": "rain,showers",
            "forecast_days": 3,
            "timezone": "Asia/Shanghai",
        })
        hourly = fetch_json("https://api.open-meteo.com/v1/forecast?" + query)["hourly"]
        amounts = dict(zip(hourly["time"], zip(hourly["rain"], hourly["showers"])))
        periods = []
        for hour in expected:
            rain, showers = amounts[hour.strftime("%Y-%m-%dT%H:%M")]
            if rain is None or showers is None:
                raise ValueError("预报数据缺失")
            if rain + showers > 0:
                previous_hour = hour - timedelta(hours=1)
                if periods and periods[-1][1] == previous_hour:
                    periods[-1] = (periods[-1][0], hour)
                else:
                    periods.append((previous_hour, hour))
        if periods:
            windows = "、".join(f"{a:%m-%d %H:%M} 至 {b:%m-%d %H:%M}" for a, b in periods)
            lines.append(f"{name}预计有雨：{windows}")

    if not lines:
        return None
    return "\n".join([
        f"☔ {day:%m-%d} 降雨预报（北京时间）",
        *lines,
        "来源：Open-Meteo 逐小时预报",
    ])


def send(message):
    webhook = os.environ.get("WECOM_WEBHOOK_URL", "").strip()
    address = urlsplit(webhook)
    if (address.scheme, address.netloc, address.path) != (
        "https", "qyapi.weixin.qq.com", "/cgi-bin/webhook/send"
    ) or not parse_qs(address.query).get("key"):
        raise SystemExit("请在密钥配置中填写有效的 WECOM_WEBHOOK_URL。")
    request = Request(
        webhook,
        data=json.dumps({"msgtype": "text", "text": {"content": message}}).encode("utf-8"),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        result = fetch_json(request)
    except (OSError, ValueError):
        raise SystemExit("企业微信发送请求失败，请检查网络和机器人设置。") from None
    if result.get("errcode") != 0:
        raise SystemExit("企业微信未接受消息，请检查机器人的有效性。")


def main():
    parser = argparse.ArgumentParser()
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--test", action="store_true")
    mode.add_argument("--preview", action="store_true")
    parser.add_argument("--hour", type=int, choices=(7, 19))
    args = parser.parse_args()

    if args.test:
        send("【降雨提醒测试】企业微信天气推送已接通。")
        print("企业微信已接受测试消息，请在目标群核对。")
        return

    now = datetime.now(BEIJING)
    hour = args.hour if args.hour is not None else now.hour
    if args.hour is not None and hour > now.hour:
        print("已跳过跨日延迟的定时运行。")
        return

    day = now.date() + timedelta(days=int(hour >= 19))
    message = forecast(day)
    if not message:
        print(f"{day} 两地均无降雨预报。")
    elif args.preview:
        print(message)
    else:
        send(message)
        print("企业微信已接受降雨预报。")


if __name__ == "__main__":
    main()
