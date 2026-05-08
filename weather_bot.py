import requests
import schedule
import time
import threading
import hashlib
import json
from datetime import datetime, timedelta
from bs4 import BeautifulSoup

# ========== 설정 ==========
BOT_TOKEN       = "8750895415:AAH6MGMctbF-hzW9SaOLyNJQ1vmnjKpcy5U"
CHAT_IDS        = ["1015266367", "-5270166958", "-1002367716873"]
WEATHER_API_KEY = "3c75b5933c9faf470b2d64265a03bc71"
SCHEDULE_URL    = "https://theminjoo.kr/main/sub/news/schedule.php"
SCHEDULE_CHECK_INTERVAL = 180  # 3분마다 변경 체크
# ==========================

last_update_id = 0
schedule_state = {}  # 날짜별 일정 해시 저장 (메모리)
WEEKDAYS = ["월", "화", "수", "목", "금", "토", "일"]


# ──────────────────────────────────────────────
# 텔레그램 전송
# ──────────────────────────────────────────────
def send_message(text, parse_mode=None):
    url = f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage"
    for chat_id in CHAT_IDS:
        data = {"chat_id": chat_id, "text": text}
        if parse_mode:
            data["parse_mode"] = parse_mode
        try:
            requests.post(url, data=data, timeout=10)
            print(f"[{datetime.now().strftime('%H:%M:%S')}] {chat_id} 전송 완료")
        except Exception as e:
            print(f"전송 오류 ({chat_id}): {e}")


# ──────────────────────────────────────────────
# 날씨 기능 (기존)
# ──────────────────────────────────────────────
def get_tomorrow_weather():
    url = "https://api.openweathermap.org/data/2.5/forecast"
    params = {
        "lat": 37.5219, "lon": 126.9245,
        "appid": WEATHER_API_KEY, "units": "metric", "lang": "kr"
    }
    res = requests.get(url, params=params).json()
    tomorrow  = (datetime.now() + timedelta(days=1)).strftime("%Y-%m-%d")
    forecasts = [f for f in res["list"] if f["dt_txt"].startswith(tomorrow)]
    if not forecasts:
        return "❌ 날씨 데이터를 불러올 수 없습니다."
    temps    = [f["main"]["temp"] for f in forecasts]
    mid      = forecasts[len(forecasts) // 2]
    desc     = mid["weather"][0]["description"]
    humidity = mid["main"]["humidity"]
    wind     = mid["wind"]["speed"]
    rain     = any("rain" in f["weather"][0]["main"].lower() for f in forecasts)
    return (
        f"🌤 내일 여의도 날씨 예보 ({tomorrow})\n"
        f"━━━━━━━━━━━━━━\n"
        f"🌡 최고 {max(temps):.0f}°C / 최저 {min(temps):.0f}°C\n"
        f"🌥 {desc}\n"
        f"💧 습도 {humidity}% | 💨 바람 {wind}m/s\n"
        f"{'☂️ 우산 챙기세요!' if rain else '☀️ 맑은 하루 되세요!'}"
    )

def send_weather():
    send_message(get_tomorrow_weather())


# ──────────────────────────────────────────────
# 민주당 일정 기능 (신규)
# ──────────────────────────────────────────────
def fetch_schedule(target: datetime) -> list:
    url = (f"{SCHEDULE_URL}"
           f"?year={target.year}&month={target.month:02d}&day={target.day:02d}")
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/124.0.0.0 Safari/537.36",
        "Referer": SCHEDULE_URL,
        "Accept-Language": "ko-KR,ko;q=0.9"
    }
    try:
        res = requests.get(url, headers=headers, timeout=15)
        res.encoding = "utf-8"
        return _parse_schedule(res.text)
    except Exception as e:
        print(f"일정 요청 오류: {e}")
        return []


def _parse_schedule(html: str) -> list:
    soup  = BeautifulSoup(html, "html.parser")
    items = []

    # selector 순서대로 시도
    for sel in [".schedule-list li", ".cal-list li", ".day-list li",
                ".schedule li", "ul.list li", ".schedule-wrap li",
                ".detail-list li"]:
        found = soup.select(sel)
        if found:
            for el in found:
                text = el.get_text(strip=True)
                if text and len(text) > 3:
                    time_el  = el.find(class_=lambda c: c and "time"  in c)
                    title_el = el.find(class_=lambda c: c and any(k in c for k in ("tit","subject","title")))
                    place_el = el.find(class_=lambda c: c and any(k in c for k in ("place","loc","where")))
                    items.append({
                        "time":     time_el.get_text(strip=True)  if time_el  else "",
                        "category": _detect_category(text),
                        "title":    title_el.get_text(strip=True) if title_el else text[:80],
                        "place":    place_el.get_text(strip=True) if place_el else ""
                    })
            break

    # fallback: 테이블
    if not items:
        for row in soup.select("table tr"):
            cols = row.find_all("td")
            if len(cols) >= 2:
                ev = cols[1].get_text(strip=True)
                if ev and len(ev) > 2:
                    items.append({
                        "time":     cols[0].get_text(strip=True),
                        "category": _detect_category(ev),
                        "title":    ev,
                        "place":    cols[2].get_text(strip=True) if len(cols) > 2 else ""
                    })
    return items


def _detect_category(text: str) -> str:
    if "원내대표" in text: return "원내대표"
    if "당대표"  in text:  return "당대표"
    return "기타"


def _hash(items: list) -> str:
    return hashlib.md5(
        json.dumps(items, ensure_ascii=False, sort_keys=True).encode()
    ).hexdigest()


def _diff(old: list, new: list) -> dict:
    def key(i): return f"{i['time']}|{i['title']}"
    old_map = {key(i): i for i in old}
    new_map = {key(i): i for i in new}
    return {
        "added":   [i for k, i in new_map.items() if k not in old_map],
        "removed": [i for k, i in old_map.items() if k not in new_map]
    }


def _fmt_item(item: dict) -> str:
    emoji = {"당대표": "🔵", "원내대표": "🟢"}.get(item["category"], "⚪")
    t = f"{item['time']} " if item["time"]  else ""
    p = f" 📍{item['place']}" if item["place"] else ""
    return f"{emoji} {t}{item['title']}{p}"


def fmt_full_schedule(items: list, target: datetime) -> str:
    dow      = WEEKDAYS[target.weekday()]
    date_str = target.strftime(f"%Y년 %m월 %d일({dow})")
    lines    = [f"📅 {date_str} 일정", "─" * 20]
    if not items:
        lines.append("등록된 일정이 없습니다.")
    else:
        groups = {}
        for it in items:
            groups.setdefault(it["category"], []).append(it)
        for cat, cat_items in groups.items():
            emoji = {"당대표": "🔵", "원내대표": "🟢"}.get(cat, "⚪")
            lines.append(f"\n{emoji} [{cat}]")
            for it in cat_items:
                t = f"🕐{it['time']}  " if it["time"]  else ""
                p = f"\n    📍{it['place']}" if it["place"] else ""
                lines.append(f"  • {t}{it['title']}{p}")
    lines.append(f"\n{SCHEDULE_URL}")
    return "\n".join(lines)


def fmt_diff_schedule(diff: dict, target: datetime) -> str:
    dow      = WEEKDAYS[target.weekday()]
    date_str = target.strftime(f"%m월 %d일({dow})")
    lines    = [f"🔔 {date_str} 일정 변경", "─" * 20]
    if diff["added"]:
        lines.append("\n✅ 추가")
        for it in diff["added"]:
            lines.append(f"  {_fmt_item(it)}")
    if diff["removed"]:
        lines.append("\n❌ 삭제")
        for it in diff["removed"]:
            lines.append(f"  {_fmt_item(it)}")
    lines.append(f"\n{SCHEDULE_URL}")
    return "\n".join(lines)


def check_schedule_for(target: datetime):
    global schedule_state
    date_key = target.strftime("%Y-%m-%d")
    items    = fetch_schedule(target)
    h        = _hash(items)
    prev     = schedule_state.get(date_key)

    if prev is None:
        # 최초 감지 → 전체 전송
        print(f"[일정] {date_key} 최초 등록 ({len(items)}개)")
        send_message(fmt_full_schedule(items, target))
        schedule_state[date_key] = {"hash": h, "items": items}

    elif prev["hash"] != h:
        # 변경 감지 → diff 전송
        diff = _diff(prev["items"], items)
        if diff["added"] or diff["removed"]:
            print(f"[일정] {date_key} 변경 감지 추가={len(diff['added'])} 삭제={len(diff['removed'])}")
            send_message(fmt_diff_schedule(diff, target))
        schedule_state[date_key] = {"hash": h, "items": items}
    else:
        print(f"[일정] {date_key} 변경 없음")


def schedule_monitor_loop():
    """일정 모니터링 루프 (별도 스레드)"""
    print("[일정] 모니터링 스레드 시작")
    while True:
        try:
            today    = datetime.now()
            tomorrow = today + timedelta(days=1)
            for target in [today, tomorrow]:
                check_schedule_for(target)
                time.sleep(3)
        except Exception as e:
            print(f"[일정] 오류: {e}")
        time.sleep(SCHEDULE_CHECK_INTERVAL)


# ──────────────────────────────────────────────
# 명령어 처리 (기존 + 신규)
# ──────────────────────────────────────────────
def check_messages():
    global last_update_id
    while True:
        try:
            url    = f"https://api.telegram.org/bot{BOT_TOKEN}/getUpdates"
            params = {"offset": last_update_id + 1, "timeout": 30}
            res    = requests.get(url, params=params, timeout=35).json()
            for update in res.get("result", []):
                last_update_id = update["update_id"]
                text = update.get("message", {}).get("text", "")
                if "열려라 날씨" in text:
                    print(f"[{datetime.now().strftime('%H:%M:%S')}] 즉시 날씨 요청")
                    send_weather()
                elif "열려라 일정" in text:
                    print(f"[{datetime.now().strftime('%H:%M:%S')}] 즉시 일정 요청")
                    today = datetime.now()
                    send_message(fmt_full_schedule(fetch_schedule(today), today))
        except Exception as e:
            print(f"메시지 확인 오류: {e}")
        time.sleep(1)


# ──────────────────────────────────────────────
# 시작
# ──────────────────────────────────────────────
schedule.every().day.at("08:58").do(send_weather)

# 스레드 시작
threading.Thread(target=check_messages,      daemon=True).start()
threading.Thread(target=schedule_monitor_loop, daemon=True).start()

print("봇 시작됨 ✅  (날씨 + 민주당 일정 모니터링)")

while True:
    schedule.run_pending()
    time.sleep(60)
