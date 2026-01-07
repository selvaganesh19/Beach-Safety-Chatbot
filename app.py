import os
import requests
from flask import Flask, request, jsonify, make_response
from flask_cors import CORS
from dotenv import load_dotenv
from bs4 import BeautifulSoup

# ---------------------------------------------
# Load environment variables
# ---------------------------------------------
load_dotenv()
GROQ_API_KEY = os.getenv("GROQ_API_KEY")

app = Flask(__name__)

# Fix CORS - allow all origins and handle preflight
CORS(app)

# @app.after_request
# def after_request(response):
#     response.headers.add('Access-Control-Allow-Origin', '*')
#     response.headers.add('Access-Control-Allow-Headers', 'Content-Type,Authorization')
#     response.headers.add('Access-Control-Allow-Methods', 'GET,PUT,POST,DELETE,OPTIONS')
#     return response

# ---------------------------------------------
# Static coordinates (fast path)
# ---------------------------------------------
BEACH_COORDS = {
    "marina beach": (13.0500, 80.2824),
    "kovalam beach": (8.4000, 76.9780),
    "goa beach": (15.2993, 74.1240),
    "puri beach": (19.7983, 85.8245)
}

# ---------------------------------------------
# Dynamic geocoding (ANY beach)
# ---------------------------------------------
def get_coordinates(beach):
    try:
        url = "https://nominatim.openstreetmap.org/search"
        params = {"q": f"{beach}, India", "format": "json", "limit": 1}
        headers = {"User-Agent": "BeachSafetyBot/1.0"}
        r = requests.get(url, params=params, headers=headers, timeout=10).json()
        if r:
            return float(r[0]["lat"]), float(r[0]["lon"])
    except Exception as e:
        print("Geocoding error:", e)
    return None, None

# ---------------------------------------------
# Weather (Open-Meteo)
# ---------------------------------------------
def get_weather(lat, lon):
    try:
        url = (
            "https://api.open-meteo.com/v1/forecast"
            f"?latitude={lat}&longitude={lon}"
            "&daily=temperature_2m_max,temperature_2m_min"
            "&current_weather=true&timezone=auto"
        )
        d = requests.get(url, timeout=10).json()
        return {
            "temp": d["current_weather"]["temperature"],
            "wind": d["current_weather"]["windspeed"],
            "min": d["daily"]["temperature_2m_min"][0],
            "max": d["daily"]["temperature_2m_max"][0]
        }
    except:
        return {"temp": "N/A", "wind": "N/A", "min": "N/A", "max": "N/A"}

# ---------------------------------------------
# INCOIS alert (simple crawl)
# ---------------------------------------------
def has_alert():
    try:
        html = requests.get("https://incois.gov.in/portal/tsunami.jsp", timeout=10).text
        return "WARNING" in html.upper()
    except:
        return False

# ---------------------------------------------
# Safety logic (UNCHANGED)
# ---------------------------------------------
def evaluate(wind, alert, beach):
    if alert:
        return "NOT SUITABLE", "RED"
    try:
        wind = float(wind)
    except:
        wind = 0
    if wind > 12:
        return "CAUTION", "YELLOW"
    if "marina" in beach:
        return "CAUTION", "YELLOW"
    return "SUITABLE", "GREEN"

# ---------------------------------------------
# Wikipedia crawl (SAFE & LIMITED)
# ---------------------------------------------
def crawl_beach_details(beach):
    url = f"https://en.wikipedia.org/wiki/{beach.replace(' ', '_')}"
    details = {
        "famous_for": "Scenic coastal destination",
        "hotspots": ["Local shoreline"],
        "safety_rules": ["Follow local advisories", "Avoid swimming during rough sea"],
        "best_time": "Morning and evening hours"
    }
    try:
        r = requests.get(url, timeout=10)
        soup = BeautifulSoup(r.text, "html.parser")
        p = soup.select("p")[:4]

        if p:
            details["famous_for"] = p[0].get_text().split(".")[0]

        for para in p:
            t = para.get_text().lower()
            if "lighthouse" in t or "promenade" in t or "tourist" in t:
                details["hotspots"].append(para.get_text().split(".")[0])
            if "swim" in t or "current" in t or "unsafe" in t:
                details["safety_rules"].append(para.get_text().split(".")[0])
            if "monsoon" in t:
                details["best_time"] = "October to March"
    except:
        pass

    return details

# ---------------------------------------------
# Groq AI (OPTIONAL, SAFE)
# ---------------------------------------------
def groq_rewrite(prompt):
    if not GROQ_API_KEY:
        return None

    try:
        prompt = prompt[:2000]  # HARD LIMIT

        r = requests.post(
            "https://api.groq.com/openai/v1/chat/completions",
            headers={
                "Authorization": f"Bearer {GROQ_API_KEY}",
                "Content-Type": "application/json"
            },
            json={
                "model": "llama-3.3-70b-versatile",
                "messages": [
                    {"role": "system", "content": "You are a beach safety assistant."},
                    {"role": "user", "content": prompt}
                ],
                "max_tokens": 500,
                "temperature": 0.3
            },
            timeout=20
        )

        if r.status_code != 200:
            return None

        return r.json()["choices"][0]["message"]["content"]

    except:
        return None

# ---------------------------------------------
# Health Check
# ---------------------------------------------
@app.route("/", methods=["GET"])
def home():
    return jsonify({"status": "running", "message": "Beach Safety API is running!"})

# ---------------------------------------------
# Ask API (matches frontend)
# ---------------------------------------------
@app.route("/ask", methods=["POST", "OPTIONS"])
def ask():
    # Handle preflight OPTIONS request
    if request.method == "OPTIONS":
        response = make_response()
        response.headers.add('Access-Control-Allow-Origin', '*')
        response.headers.add('Access-Control-Allow-Headers', 'Content-Type,Authorization')
        response.headers.add('Access-Control-Allow-Methods', 'POST,OPTIONS')
        return response, 200

    data = request.get_json()
    question = data.get("question", "").strip() if data else ""

    if not question:
        return jsonify({"error": "Please enter a question about a beach"})

    # Extract beach name from question
    msg = question.lower()
    beach = msg if "beach" in msg else f"{msg} beach"
    
    # Clean up beach name
    for word in ["safety", "rules", "regulations", "guidelines", "hotspots", "what", "is", "are", "the", "about", "tell", "me"]:
        beach = beach.replace(word, "").strip()
    beach = " ".join(beach.split())  # Remove extra spaces

    lat, lon = BEACH_COORDS.get(beach, get_coordinates(beach))
    if not lat:
        # Try AI response for general questions
        ai_response = groq_rewrite(f"Answer this question about Indian beaches: {question}")
        if ai_response:
            return jsonify({
                "answer": ai_response,
                "sources": ["AI Knowledge Base"]
            })
        return jsonify({"error": "Unable to locate this beach in India. Please try a specific beach name."})

    weather = get_weather(lat, lon)
    alert = has_alert()
    status, color = evaluate(weather["wind"], alert, beach)
    details = crawl_beach_details(beach)

    # Build comprehensive response
    response_text = f"""**{beach.title()}** - Status: **{status}**

🌡️ **Current Weather:**
- Temperature: {weather['temp']}°C (Min: {weather['min']}°C, Max: {weather['max']}°C)
- Wind Speed: {weather['wind']} km/h

📍 **Famous For:**
{details['famous_for']}

🏖️ **Hotspots:**
{chr(10).join(['• ' + h for h in details['hotspots'][:3]])}

⚠️ **Safety Rules:**
{chr(10).join(['• ' + r for r in details['safety_rules'][:4]])}

🕐 **Best Time to Visit:**
{details['best_time']}

📊 **Water Quality:**
Baseline coastal water quality monitored under NWMP guidelines.
"""

    # Enhance with AI if available
    ai_enhanced = groq_rewrite(f"Enhance this beach safety information in a friendly, helpful way: {response_text}")
    
    return jsonify({
        "answer": ai_enhanced if ai_enhanced else response_text,
        "sources": ["Wikipedia", "Open-Meteo Weather", "INCOIS"],
        "beach": beach.title(),
        "status": status,
        "color": color,
        "lat": lat,
        "lon": lon
    })

# ---------------------------------------------
# Chat API (legacy support)
# ---------------------------------------------
@app.route("/chat", methods=["POST", "OPTIONS"])
def chat():
    if request.method == "OPTIONS":
        response = make_response()
        response.headers.add('Access-Control-Allow-Origin', '*')
        response.headers.add('Access-Control-Allow-Headers', 'Content-Type,Authorization')
        response.headers.add('Access-Control-Allow-Methods', 'POST,OPTIONS')
        return response, 200

    data = request.get_json()
    msg = data.get("message", "").lower().strip()

    if not msg:
        return jsonify({"error": "Please enter a beach name"})

    beach = msg if "beach" in msg else f"{msg} beach"

    lat, lon = BEACH_COORDS.get(beach, get_coordinates(beach))
    if not lat:
        return jsonify({"error": "Unable to locate this beach in India"})

    weather = get_weather(lat, lon)
    alert = has_alert()
    status, color = evaluate(weather["wind"], alert, beach)
    details = crawl_beach_details(beach)

    base_reply = (
        f"{beach.title()} is currently {status}. "
        f"Temperature is around {weather['temp']}°C with wind speed "
        f"of {weather['wind']} km/h. Visitors should follow safety rules."
    )

    ai_reply = groq_rewrite(base_reply)
    reply = ai_reply if ai_reply else base_reply

    return jsonify({
        "beach": beach.title(),
        "status": status,
        "color": color,
        "weather": weather,
        "lat": lat,
        "lon": lon,
        "water_details": "Baseline coastal water quality (NWMP) with current weather check",
        "famous_for": details["famous_for"],
        "hotspots": details["hotspots"][:3],
        "safety_rules": details["safety_rules"][:4],
        "best_time": details["best_time"],
        "reply": reply
    })

# ---------------------------------------------
# Run server
# ---------------------------------------------
if __name__ == "__main__":
    print("🏖️ Beach Safety API Starting...")
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port)
