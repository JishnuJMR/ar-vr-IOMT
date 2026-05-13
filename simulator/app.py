"""
Flask interface for AR/VR-Enabled Smart Healthcare Monitoring
and Emergency Response System sensor simulator.
"""

import json
import queue
import threading
from flask import Flask, Response, request, jsonify

import sys, os
sys.path.insert(0, os.path.dirname(__file__))

# Import the individual runner functions and helpers directly.
# We deliberately do NOT call generate_max30100_data() because it registers
# signal handlers, which Python forbids on any thread other than the main
# thread — causing "ValueError: signal only works in main thread".
from HRgen import (
    run_body_temperature,
    run_max30100,
    parse_selection,
    SENSOR_OPTIONS,
)

app = Flask(__name__)

# ── Global simulation state ──────────────────────────────────────────────────
_stop_event: threading.Event | None = None
_sim_thread: threading.Thread | None = None
_log_queue: queue.Queue = queue.Queue(maxsize=500)
_running = False

RUNNER_MAP = {"1": run_body_temperature, "2": run_max30100}

# ── Intercept print() so we can stream logs to the browser ──────────────────

class _QueueWriter:
    """Tee stdout into _log_queue while keeping normal console output."""
    def __init__(self, original):
        self._orig = original

    def write(self, text):
        self._orig.write(text)
        if text.strip():
            try:
                _log_queue.put_nowait(text.rstrip())
            except queue.Full:
                try:
                    _log_queue.get_nowait()
                except queue.Empty:
                    pass
                _log_queue.put_nowait(text.rstrip())

    def flush(self):
        self._orig.flush()


import sys as _sys
_sys.stdout = _QueueWriter(_sys.stdout)


# ── Routes ───────────────────────────────────────────────────────────────────

@app.route("/")
def index():
    tpl = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                       "templates", "index.html")
    with open(tpl, "r", encoding="utf-8") as f:
        return f.read()


@app.route("/start", methods=["POST"])
def start():
    global _stop_event, _sim_thread, _running

    if _running:
        return jsonify({"status": "already_running"}), 200

    data = request.get_json(silent=True) or {}
    selection = str(data.get("selection", "3"))

    selected = parse_selection(selection)
    if not selected:
        return jsonify({"status": "error", "message": "Invalid selection"}), 400

    _stop_event = threading.Event()
    _running = True

    def _run():
        global _running
        try:
            inner_threads = []
            for key in selected:
                runner = RUNNER_MAP[key]
                t = threading.Thread(
                    target=runner,
                    args=(_stop_event,),
                    name=SENSOR_OPTIONS[key],
                    daemon=True,
                )
                t.start()
                inner_threads.append(t)
                print(f"[INFO] Started {SENSOR_OPTIONS[key]}")

            # Block until stop is requested or all inner threads finish naturally
            while not _stop_event.is_set():
                if not any(t.is_alive() for t in inner_threads):
                    break
                _stop_event.wait(timeout=0.2)

        finally:
            _running = False
            print("[INFO] All simulators stopped.")

    _sim_thread = threading.Thread(target=_run, daemon=True)
    _sim_thread.start()

    label = {
        "1": "Body Temperature",
        "2": "MAX30100 (SpO2 + Heart Rate)",
        "3": "All Sensors",
    }.get(selection, "Selected Sensors")

    return jsonify({"status": "started", "sensor": label})


@app.route("/stop", methods=["POST"])
def stop():
    global _stop_event, _running

    if not _running:
        return jsonify({"status": "not_running"}), 200

    if _stop_event:
        _stop_event.set()
    _running = False

    return jsonify({"status": "stopped"})


@app.route("/status")
def status():
    return jsonify({"running": _running})


@app.route("/stream")
def stream():
    """Server-Sent Events — pushes log lines to the browser in real time."""
    def event_gen():
        while True:
            try:
                line = _log_queue.get(timeout=1.0)
                yield f"data: {json.dumps({'line': line})}\n\n"
            except queue.Empty:
                yield ": ping\n\n"   # keep-alive

    return Response(
        event_gen(),
        mimetype="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


# ── Entry point ──────────────────────────────────────────────────────────────

if __name__ == "__main__":
    print("=" * 60)
    print(" AR/VR Smart Healthcare Monitor - Flask Interface")
    print(" Open  http://127.0.0.1:5000  in your browser")
    print("=" * 60)
    app.run(debug=False, threaded=True, host="0.0.0.0", port=5050)