import re
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
schedule_state = {}  # {날짜: {"hash": ..., "text": ...}}
WEEKDAYS = ["월", "화", "수", "목", "금", "토", "일"]

# 일정 항목 패턴: ① ② ... 또는 1. 2. 또는 HH:MM 으로 시작하는 줄
ITEM_PATTERN = re.compile(r'^[①②③④⑤⑥⑦⑧⑨⑩]|^\d{2}:\d{2}|^\d+\.')
# 인물 헤더 패턴: "홍길동 당대표" 또는 "당대표" 단독
PERSON_PATTERN = re.compile(r'(당대표|원내대표|비상대책위원장|대표)')
# 네비게이션 노이즈 패턴 (제거 대상)
NOISE_PATTERN  = re.compile(r'^(Home|2\d{3}년|0?\d월|\d{4}년\s*\d{2}월\s*\d{2}일|검색|전체메뉴|Skip|바로가기)$')


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
# 날씨 기능 (기존 유지)
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
# 민주당 일정 크롤링 + 파싱
# ──────────────────────────────────────────────
def fetch_schedule_text(target: datetime) -> str:
    """
    민주당 일정 페이지에서 당대표/원내대표 일정만 추출해 텍스트로 반환
    """
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
    except Exception as e:
        print(f"일정 요청 오류: {e}")
        return ""

    soup = BeautifulSoup(res.text, "html.parser")

    # 불필요한 태그 제거
    for tag in soup.select("script, style, nav, header, footer, "
                           ".gnb, .lnb, .top-wrap, .header-wrap, "
                           ".footer-wrap, .side, .util-nav"):
        tag.decompose()

    # 본문 영역 우선 탐색
    main = (soup.find(id="main") or
            soup.find("main") or
            soup.find(id="container") or
            soup.find(class_="contents") or
            soup.body)

    if not main:
        return ""

    raw_lines = [l.strip() for l in main.get_text("\n", strip=True).split("\n")
                 if l.strip()]

    return _extract_schedule_text(raw_lines, target)


def _extract_schedule_text(lines: list, target: datetime) -> str:
    """
    전체 텍스트 라인에서 일정 관련 블록만 추출
    """
    blocks   = []   # [{"header": "정청래 당대표", "items": [...]}]
    current  = None

    for line in lines:
        # 노이즈 제거
        if NOISE_PATTERN.match(line):
            continue
        if len(line) < 2:
            continue

        # 인물 헤더 감지 (예: "정청래 당대표 2026-05-08" 또는 "당대표" 단독)
        if PERSON_PATTERN.search(line) and not ITEM_PATTERN.match(line):
            # 날짜 문자열은 제거
            header = re.sub(r'\d{4}-\d{2}-\d{2}', '', line).strip()
            current = {"header": header, "items": []}
            blocks.append(current)
            continue

        # 일정 항목 감지
        if current is not None and ITEM_PATTERN.match(line):
            current["items"].append(line)

    # 결과 조합
    if not blocks:
        return ""

    dow      = WEEKDAYS[target.weekday()]
    date_str = target.strftime(f"%Y년 %m월 %d일 ({dow})")
    result   = [f"📅 {date_str} 일정", "─" * 22]

    for block in blocks:
        if not block["items"]:
            continue
        # 인물 이모지
        hdr = block["header"]
        if "원내대표" in hdr:
            emoji = "🟢"
        elif "당대표" in hdr or "대표" in hdr:
            emoji = "🔵"
        else:
            emoji = "⚪"
        result.append(f"\n{emoji} {hdr}")
        for item in block["items"]:
            result.append(f"  {item}")

    result.append(f"\n🔗 {SCHEDULE_URL}")
    return "\n".join(result)


# ──────────────────────────────────────────────
# 변경 감지
# ──────────────────────────────────────────────
def _hash(text: str) -> str:
    return hashlib.md5(text.encode()).hexdigest()


def _make_diff_msg(old_text: str, new_text: str, target: datetime) -> str:
    """변경 전후 비교 메시지 (줄 단위 diff)"""
    dow      = WEEKDAYS[target.weekday()]
    date_str = target.strftime(f"%m월 %d일({dow})")

    old_lines = set(old_text.split("\n"))
    new_lines = set(new_text.split("\n"))

    added   = [l for l in new_lines - old_lines
               if l.strip() and (ITEM_PATTERN.match(l.strip()) or PERSON_PATTERN.search(l))]
    removed = [l for l in old_lines - new_lines
               if l.strip() and (ITEM_PATTERN.match(l.strip()) or PERSON_PATTERN.search(l))]

    if not added and not removed:
        return ""

    lines = [f"🔔 {date_str} 일정 변경", "─" * 22]
    if added:
        lines.append("\n✅ 추가")
        lines += [f"  {l.strip()}" for l in sorted(added)]
    if removed:
        lines.append("\n❌ 삭제/변경")
        lines += [f"  {l.strip()}" for l in sorted(removed)]
    lines.append(f"\n🔗 {SCHEDULE_URL}")
    return "\n".join(lines)


def check_schedule_for(target: datetime):
    global schedule_state
    date_key = target.strftime("%Y-%m-%d")
    text     = fetch_schedule_text(target)

    if not text:
        print(f"[일정] {date_key} 내용 없음")
        return

    h    = _hash(text)
    prev = schedule_state.get(date_key)

    if prev is None:
        # 최초 → 전체 전송
        print(f"[일정] {date_key} 최초 등록")
        send_message(text)
        schedule_state[date_key] = {"hash": h, "text": text}

    elif prev["hash"] != h:
        # 변경 → diff 전송
        diff_msg = _make_diff_msg(prev["text"], text, target)
        if diff_msg:
            print(f"[일정] {date_key} 변경 감지")
            send_message(diff_msg)
        schedule_state[date_key] = {"hash": h, "text": text}
    else:
        print(f"[일정] {date_key} 변경 없음")


def schedule_monitor_loop():
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
# 명령어 처리
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
                    today    = datetime.now()
                    result   = fetch_schedule_text(today)
                    send_message(result if result else "오늘 등록된 일정이 없습니다.")
        except Exception as e:
            print(f"메시지 확인 오류: {e}")
        time.sleep(1)


# ──────────────────────────────────────────────
# 시작
# ──────────────────────────────────────────────
schedule.every().day.at("08:58").do(send_weather)

threading.Thread(target=check_messages,        daemon=True).start()
threading.Thread(target=schedule_monitor_loop, daemon=True).start()

print("봇 시작됨 ✅  (날씨 + 민주당 일정 모니터링)")

while True:
    schedule.run_pending()
    time.sleep(60)
