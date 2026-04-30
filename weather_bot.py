import requests
import schedule
import time
import threading
from datetime import datetime, timedelta

# ========== 설정 ==========
BOT_TOKEN = "8750895415:AAH6MGMctbF-hzW9SaOLyNJQ1vmnjKpcy5U"
CHAT_IDS = ["1015266367", "-5270166958", "-1002367716873"]  # ← 리스트로 변경
WEATHER_API_KEY = "3c75b5933c9faf470b2d64265a03bc71"
# ==========================

last_update_id = 0

def get_tomorrow_weather():
    url = "https://api.openweathermap.org/data/2.5/forecast"
    params = {
        "lat": 37.5219,
        "lon": 126.9245,
        "appid": WEATHER_API_KEY,
        "units": "metric",
        "lang": "kr"
    }
    res = requests.get(url, params=params).json()
    tomorrow = (datetime.now() + timedelta(days=1)).strftime("%Y-%m-%d")
    forecasts = [f for f in res["list"] if f["dt_txt"].startswith(tomorrow)]
    if not forecasts:
        return "❌ 날씨 데이터를 불러올 수 없습니다."
    temps = [f["main"]["temp"] for f in forecasts]
    desc = forecasts[len(forecasts)//2]["weather"][0]["description"]
    humidity = forecasts[len(forecasts)//2]["main"]["humidity"]
    wind = forecasts[len(forecasts)//2]["wind"]["speed"]
    rain = any("rain" in f["weather"][0]["main"].lower() for f in forecasts)
    msg = f"""🌤 내일 여의도 날씨 예보 ({tomorrow})
━━━━━━━━━━━━━━
🌡 최고 {max(temps):.0f}°C / 최저 {min(temps):.0f}°C
🌥 {desc}
💧 습도 {humidity}% | 💨 바람 {wind}m/s
{"☂️ 우산 챙기세요!" if rain else "☀️ 맑은 하루 되세요!"}"""
    return msg

def send_weather():
    msg = get_tomorrow_weather()
    url = f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage"
    for chat_id in CHAT_IDS:  # ← 루프로 각각 전송
        requests.post(url, data={"chat_id": chat_id, "text": msg})
        print(f"[{datetime.now()}] {chat_id} 전송 완료")

def check_messages():
    global last_update_id
    while True:
        try:
            url = f"https://api.telegram.org/bot{BOT_TOKEN}/getUpdates"
            params = {"offset": last_update_id + 1, "timeout": 30}
            res = requests.get(url, params=params, timeout=35).json()
            for update in res.get("result", []):
                last_update_id = update["update_id"]
                text = update.get("message", {}).get("text", "")
                if "열려라 날씨" in text:
                    print(f"[{datetime.now()}] 즉시 날씨 요청 받음")
                    send_weather()
        except Exception as e:
            print(f"오류: {e}")
        time.sleep(1)

schedule.every().day.at("08:58").do(send_weather)

thread = threading.Thread(target=check_messages, daemon=True)
thread.start()

print("날씨 봇 시작됨 ✅")
while True:
    schedule.run_pending()
    time.sleep(60)
