import re
import requests
import schedule
import time
import threading
import hashlib
from datetime import datetime, timedelta
from bs4 import BeautifulSoup
from playwright.sync_api import sync_playwright, TimeoutError as PWTimeout

# ========== 설정 ==========
BOT_TOKEN       = "8750895415:AAH6MGMctbF-hzW9SaOLyNJQ1vmnjKpcy5U"
CHAT_IDS        = ["1015266367", "-5270166958", "-1002367716873"]
WEATHER_API_KEY = "3c75b5933c9faf470b2d64265a03bc71"
SCHEDULE_URL    = "https://theminjoo.kr/main/sub/news/schedule.php"
SCHEDULE_CHECK_INTERVAL = 180
# ==========================

last_update_id = 0
schedule_state = {}
WEEKDAYS       = ["월", "화", "수", "목", "금", "토", "일"]

ITEM_RE   = re.compile(r'^[①②③④⑤⑥⑦⑧⑨⑩]')
DATE_RE   = re.compile(r'^\d{4}-\d{2}-\d{2}$')
PERSON_KW = ["당대표", "원내대표", "비상대책위원장"]


def send_message(text):
    url = f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage"
    for chat_id in CHAT_IDS:
        try:
            requests.post(url, data={"chat_id": chat_id, "text": text}, timeout=10)
            print(f"[{datetime.now().strftime('%H:%M:%S')}] {chat_id} 전송 완료")
        except Exception as e:
            print(f"전송 오류 ({chat_id}): {e}")


def get_tomorrow_weather():
    url = "https://api.openweathermap.org/data/2.5/forecast"
    params = {"lat": 37.5219, "lon": 126.9245,
              "appid": WEATHER_API_KEY, "units": "metric", "lang": "kr"}
    res = requests.get(url, params=params).json()
    tomorrow  = (datetime.now() + timedelta(days=1)).strftime("%Y-%m-%d")
    forecasts = [f for f in res["list"] if f["dt_txt"].startswith(tomorrow)]
    if not forecasts:
        return "❌ 날씨 데이터를 불러올 수 없습니다."
    temps = [f["main"]["temp"] for f in forecasts]
    mid   = forecasts[len(forecasts) // 2]
    rain  = any("rain" in f["weather"][0]["main"].lower() for f in forecasts)
    return (f"🌤 내일 여의도 날씨 예보 ({tomorrow})\n"
            f"━━━━━━━━━━━━━━\n"
            f"🌡 최고 {max(temps):.0f}°C / 최저 {min(temps):.0f}°C\n"
            f"🌥 {mid['weather'][0]['description']}\n"
            f"💧 습도 {mid['main']['humidity']}% | 💨 바람 {mid['wind']['speed']}m/s\n"
            f"{'☂️ 우산 챙기세요!' if rain else '☀️ 맑은 하루 되세요!'}")

def send_weather():
    send_message(get_tomorrow_weather())


def get_html(url: str) -> str:
    try:
        with sync_playwright() as p:
            browser = p.chromium.launch(
                headless=True,
                args=["--no-sandbox", "--disable-dev-shm-usage",
                      "--disable-gpu", "--disable-setuid-sandbox"]
            )
            ctx  = browser.new_context(
                user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/124.0.0.0 Safari/537.36",
                locale="ko-KR"
            )
            page = ctx.new_page()
            page.goto(url, wait_until="networkidle", timeout=30000)
            try:
                page.wait_for_function(
                    "document.body.innerText.includes('①')", timeout=10000
                )
            except PWTimeout:
                pass
            html = page.content()
            browser.close()
            return html
    except Exception as e:
        print(f"[일정] 브라우저 오류: {e}")
        return ""


def fetch_schedule_text(target: datetime) -> str:
    url  = (f"{SCHEDULE_URL}"
            f"?year={target.year}&month={target.month:02d}&day={target.day:02d}")
    html = get_html(url)
    if not html:
        return ""
    return _parse(html, target)


def _parse(html: str, target: datetime) -> str:
    """
    페이지 구조:
      정청래 당대표       ← 이름 줄 (PERSON_KW 포함)
      2026-05-08         ← 날짜 줄 (별도 줄, DATE_RE 매칭)
      ① 08:00 봉사활동   ← 항목
      ② 09:00 회의
      한병도 원내대표
      2026-05-08
      ① 09:00 ...

    → 이름 줄 다음 줄이 오늘 날짜면 수집 시작
    → 다른 날짜면 수집 안 함
    """
    target_date = target.strftime("%Y-%m-%d")

    soup = BeautifulSoup(html, "html.parser")
    for tag in soup.select("script, style, nav, header, footer, .gnb, .lnb, .header-wrap, .footer-wrap"):
        tag.decompose()

    lines = [l.strip() for l in soup.get_text("\n", strip=True).split("\n") if l.strip()]

    blocks  = []
    current = None
    pending_header = None  # 이름 줄을 임시 저장

    for line in lines:
        is_person = any(kw in line for kw in PERSON_KW) and not ITEM_RE.match(line)
        is_date   = DATE_RE.match(line)
        is_item   = ITEM_RE.match(line)

        if is_person:
            # 이름 줄 → 다음 줄 날짜 확인 위해 임시 저장
            pending_header = re.sub(r'\s*\d{4}-\d{2}-\d{2}.*', '', line).strip()
            current = None  # 일단 수집 중단

        elif is_date and pending_header:
            # 날짜 줄 — 오늘 날짜면 수집 시작, 아니면 무시
            if line == target_date:
                current = {"header": pending_header, "items": []}
                blocks.append(current)
            else:
                current = None
            pending_header = None

        elif is_item and current is not None:
            current["items"].append(line)

        elif not is_person and not is_date and not is_item:
            # 관계없는 줄 — pending_header 유지 (날짜 줄이 바로 안 올 수도 있으니)
            pass

    # 항목 있는 블록만
    blocks = [b for b in blocks if b["items"]]

    print(f"[일정] {target_date} 블록: {len(blocks)}개")
    for b in blocks:
        print(f"  - {b['header']} ({len(b['items'])}개)")

    if not blocks:
        return ""

    dow      = WEEKDAYS[target.weekday()]
    date_str = target.strftime(f"%Y년 %m월 %d일 ({dow})")
    out      = [f"📅 {date_str} 일정", "─" * 22]

    for block in blocks:
        emoji = "🟢" if "원내대표" in block["header"] else "🔵"
        out.append(f"\n{emoji} {block['header']}")
        for item in block["items"]:
            out.append(f"  {item}")

    out.append(f"\n🔗 {SCHEDULE_URL}")
    return "\n".join(out)


def _hash(text: str) -> str:
    return hashlib.md5(text.encode()).hexdigest()

def _diff_msg(old: str, new: str, target: datetime) -> str:
    dow      = WEEKDAYS[target.weekday()]
    date_str = target.strftime(f"%m월 %d일({dow})")
    old_items = {l.strip() for l in old.split("\n") if ITEM_RE.match(l.strip())}
    new_items = {l.strip() for l in new.split("\n") if ITEM_RE.match(l.strip())}
    added   = sorted(new_items - old_items)
    removed = sorted(old_items - new_items)
    if not added and not removed:
        return ""
    out = [f"🔔 {date_str} 일정 변경", "─" * 22]
    if added:
        out.append("\n✅ 추가")
        out += [f"  {l}" for l in added]
    if removed:
        out.append("\n❌ 삭제/변경")
        out += [f"  {l}" for l in removed]
    out.append(f"\n🔗 {SCHEDULE_URL}")
    return "\n".join(out)


def check_schedule():
    global schedule_state
    today    = datetime.now()
    date_key = today.strftime("%Y-%m-%d")
    text     = fetch_schedule_text(today)

    if not text:
        print(f"[일정] {date_key} 내용 없음")
        return

    h    = _hash(text)
    prev = schedule_state.get(date_key)

    if prev is None:
        print(f"[일정] {date_key} 최초 → 전송")
        send_message(text)
        schedule_state[date_key] = {"hash": h, "text": text}
    elif prev["hash"] != h:
        diff = _diff_msg(prev["text"], text, today)
        if diff:
            print(f"[일정] {date_key} 변경 → 전송")
            send_message(diff)
        schedule_state[date_key] = {"hash": h, "text": text}
    else:
        print(f"[일정] {date_key} 변경 없음")

    cutoff = (today - timedelta(days=7)).strftime("%Y-%m-%d")
    for k in [k for k in schedule_state if k < cutoff]:
        del schedule_state[k]


def schedule_monitor_loop():
    print("[일정] 모니터링 스레드 시작")
    while True:
        try:
            check_schedule()
        except Exception as e:
            print(f"[일정] 오류: {e}")
        time.sleep(SCHEDULE_CHECK_INTERVAL)


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
                    send_weather()
                elif any(k in text for k in ["열려라 일정", "오늘의 일정", "오늘 일정"]):
                    result = fetch_schedule_text(datetime.now())
                    send_message(result if result else "오늘 등록된 일정이 없습니다.")
        except Exception as e:
            print(f"메시지 확인 오류: {e}")
        time.sleep(1)


schedule.every().day.at("08:58").do(send_weather)

threading.Thread(target=check_messages,        daemon=True).start()
threading.Thread(target=schedule_monitor_loop, daemon=True).start()

print("봇 시작됨 ✅  (날씨 + 민주당 일정 모니터링)")

while True:
    schedule.run_pending()
    time.sleep(60)
