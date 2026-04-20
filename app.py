from flask import Flask, render_template, request, jsonify
import paho.mqtt.publish as publish
import paho.mqtt.subscribe as subscribe
import json
import time
import threading
from datetime import datetime
from collections import deque

app = Flask(__name__)

BROKER      = "broker.hivemq.com"
PORT        = 1883
TOPIC       = "home/led/FINAL/UNIQUE"
WIFI_TOPIC  = "home/led/FINAL/UNIQUE/wifi"
DEBUG_TOPIC = "home/led/FINAL/UNIQUE/debug"

# ── state tracker ──────────────────────────────────────────────
state = {
    "on": True,
    "color": [255, 140, 0],
    "brightness": 255,
    "effect": 0,
    "speed": 128,
    "intensity": 128,
    "scene": None,
    "active_mode": None,   # "scene", "effect", or None
    "party_active": False,
    "alarm_active": False,
    "candle_active": False,
}

# ── debug log ──────────────────────────────────────────────────
debug_log = deque(maxlen=100)  # Store last 100 debug messages
debug_lock = threading.Lock()

def mqtt_send(payload_str):
    try:
        publish.single(
            TOPIC + "/api",
            payload_str,
            hostname=BROKER,
            port=PORT,
            keepalive=10,
        )
    except Exception as e:
        print(f"MQTT error: {e}")

def add_debug_log(message):
    """Add a message to the debug log with timestamp"""
    with debug_lock:
        timestamp = datetime.now().strftime("%H:%M:%S")
        debug_log.append({"time": timestamp, "msg": message})
        print(f"[DEBUG] {timestamp} {message}")

def listen_debug_messages():
    """Background thread to listen for debug messages from ESP32"""
    while True:
        try:
            msg = subscribe.simple(
                DEBUG_TOPIC,
                hostname=BROKER,
                port=PORT,
                keepalive=10,
                msg_count=1
            )
            if msg and msg.payload:
                payload = msg.payload.decode()
                add_debug_log(payload)
        except Exception as e:
            print(f"Debug listener error: {e}")
            time.sleep(2)

# Start debug listener in background
debug_thread = threading.Thread(target=listen_debug_messages, daemon=True)
debug_thread.start()

@app.route("/")
def index():
    return render_template("index.html")

@app.route("/control", methods=["POST"])
def control():
    data = request.get_json()
    command = data.get("command", "")

    if command == "ON":
        state["on"] = True
        mqtt_send('{"on":true}')

    elif command == "OFF":
        state["on"] = False
        mqtt_send('{"on":false}')

    elif command == "TOGGLE":
        state["on"] = not state["on"]
        mqtt_send(json.dumps({"on": state["on"]}))

    elif command.startswith("COLOR:"):
        hex_color = command.split(":")[1].lstrip("#")
        r = int(hex_color[0:2], 16)
        g = int(hex_color[2:4], 16)
        b = int(hex_color[4:6], 16)
        state["color"] = [r, g, b]
        mqtt_send(json.dumps({"on": True, "seg": [{"col": [[r, g, b]]}]}))

    elif command.startswith("BRI:"):
        val = int(command.split(":")[1])
        state["brightness"] = val
        mqtt_send(json.dumps({"on": True, "bri": val}))

    elif command.startswith("FX:"):
        parts = command.split(":")
        val = int(parts[1])
        pal = int(parts[2]) if len(parts) > 2 else None   # optional palette
        state["effect"] = val
        state["active_mode"] = None if val == 0 else "effect"
        if val != 0:
            state["scene"] = None   # clear scene when effect chosen
        seg = {"fx": val}
        if pal is not None:
            seg["pal"] = pal        # e.g. pal=11 for Rainbow palette
        mqtt_send(json.dumps({"on": True, "seg": [seg]}))

    elif command.startswith("SPEED:"):
        val = int(command.split(":")[1])
        state["speed"] = val
        mqtt_send(json.dumps({"seg": [{"sx": val}]}))

    elif command.startswith("INTENSITY:"):
        val = int(command.split(":")[1])
        state["intensity"] = val
        mqtt_send(json.dumps({"seg": [{"ix": val}]}))

    elif command.startswith("SCENE:"):
        scene_name = command.split(":")[1]
        state["scene"] = scene_name
        state["active_mode"] = "scene"
        state["effect"] = 0   # clear effect when scene chosen
        handle_scene(scene_name)

    elif command == "PARTY":
        state["party_active"] = True
        start_party_mode()

    elif command == "STOP_PARTY":
        state["party_active"] = False

    elif command.startswith("ALARM:"):
        minutes = int(command.split(":")[1])
        state["alarm_active"] = True
        schedule_alarm(minutes)

    elif command == "CANCEL_ALARM":
        state["alarm_active"] = False

    elif command == "CANDLE_ON":
        state["candle_active"] = True
        mqtt_send('{"on":true,"bri":100,"seg":[{"fx":56,"col":[[255,80,20]],"sx":120,"ix":150}]}')

    elif command == "CANDLE_OFF":
        state["candle_active"] = False
        mqtt_send('{"on":true,"seg":[{"fx":0}]}')

    elif command.startswith("TEMP:"):
        val = int(command.split(":")[1])
        r = 255
        g = int(180 + val * 0.29)
        b = int(val * 1.1)
        g = min(255, g)
        b = min(255, b)
        mqtt_send(json.dumps({"on": True, "seg": [{"col": [[r, g, b]]}]}))

    return jsonify({"status": "ok", "state": {
        "on": state["on"],
        "brightness": state["brightness"],
        "color": state["color"],
        "effect": state["effect"],
        "party_active": state["party_active"],
        "alarm_active": state["alarm_active"],
        "candle_active": state["candle_active"],
    }})

def handle_scene(scene):
    scenes = {
        # Static colour scenes — fx:0 is correct (Solid)
        "sunset":   '{"on":true,"bri":180,"seg":[{"fx":0,"col":[[255,80,0]]}]}',
        "ocean":    '{"on":true,"bri":200,"seg":[{"fx":28,"col":[[0,100,255],[0,30,120]]}]}',
        "forest":   '{"on":true,"bri":160,"seg":[{"fx":0,"col":[[0,180,60]]}]}',
        "romantic": '{"on":true,"bri":80,"seg":[{"fx":0,"col":[[255,20,60]]}]}',
        "focus":    '{"on":true,"bri":255,"seg":[{"fx":0,"col":[[255,240,200]]}]}',
        "sleep":    '{"on":true,"bri":20,"seg":[{"fx":0,"col":[[255,60,10]]}]}',
        "morning":  '{"on":true,"bri":120,"seg":[{"fx":0,"col":[[255,200,100]]}]}',
        # fx:91 = Fireworks, fx:2 = Breathe, fx:23 = Strobe (WLED 0.14)
        # cinema: deep blue-purple at low brightness — solid is intentional
        "cinema":   '{"on":true,"bri":40,"seg":[{"fx":0,"col":[[20,0,80]]}]}',
        # gaming: fx:9 = Rainbow Cycle with pal:11 (Rainbow palette) + green+pink colour hints
        "gaming":   '{"on":true,"seg":[{"fx":9,"pal":11,"sx":180,"col":[[0,255,120],[255,0,80]]}]}',
        # rave: fx:9 = Rainbow Cycle with pal:11, fast speed, high intensity
        "rave":     '{"on":true,"bri":255,"seg":[{"fx":9,"pal":11,"sx":240,"ix":240}]}',
        # christmas: fx:62 = Two Dots (alternating red+green, perfect for christmas)
        "christmas":'{"on":true,"bri":200,"seg":[{"fx":62,"col":[[255,0,0],[0,200,0],[0,0,0]]}]}',
        # halloween: fx:56 = Candle Multi works but fx:50 = Running is spookier
        "halloween":'{"on":true,"bri":180,"seg":[{"fx":50,"col":[[255,80,0],[80,0,80],[0,0,0]],"sx":120}]}',
    }
    if scene in scenes:
        mqtt_send(scenes[scene])

def start_party_mode():
    party_colors = [
        [255,0,80],[0,255,120],[80,0,255],[255,200,0],
        [0,200,255],[255,0,180],[100,255,0]
    ]
    def party_loop():
        i = 0
        while state["party_active"]:
            c = party_colors[i % len(party_colors)]
            mqtt_send(json.dumps({"on": True, "seg": [{"col": [c]}]}))
            i += 1
            time.sleep(0.8)
    t = threading.Thread(target=party_loop, daemon=True)
    t.start()

def schedule_alarm(minutes):
    def alarm_loop():
        time.sleep(minutes * 60)
        if not state["alarm_active"]:
            return
        steps = 30
        for i in range(steps):
            if not state["alarm_active"]:
                break
            progress = i / steps
            r = 255
            g = int(progress * 200)
            b = int(progress * 120)
            bri = int(10 + progress * 245)
            mqtt_send(json.dumps({"on": True, "bri": bri, "seg": [{"col": [[r, g, b]]}]}))
            time.sleep(2)
        state["alarm_active"] = False
    t = threading.Thread(target=alarm_loop, daemon=True)
    t.start()

@app.route("/wifi", methods=["POST"])
def set_wifi():
    data = request.get_json()
    ssid = data.get("ssid", "").strip()
    password = data.get("password", "")

    if not ssid:
        return jsonify({"status": "error", "message": "SSID cannot be empty"}), 400

    payload = json.dumps({"ssid": ssid, "pass": password})

    try:
        publish.single(
            WIFI_TOPIC,
            payload,
            hostname=BROKER,
            port=PORT,
            keepalive=10,
        )
        return jsonify({
            "status": "ok",
            "message": f"WiFi config sent to ESP32. It will reboot and connect to '{ssid}'."
        })
    except Exception as e:
        return jsonify({"status": "error", "message": str(e)}), 500

@app.route("/debug")
def get_debug_log():
    """Return the debug log as JSON"""
    with debug_lock:
        return jsonify({"logs": list(debug_log)})

@app.route("/state")
def get_state():
    return jsonify({k: v for k, v in state.items()})

if __name__ == "__main__":
    app.run(debug=True, host="0.0.0.0", port=5000)
