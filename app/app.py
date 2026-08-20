import os
import glob
import json
import re
import csv
import io
import threading
import time
import socket
from datetime import datetime
import pandas as pd
import numpy as np

from flask import (
    Flask, render_template, request, redirect, 
    url_for, session, jsonify, flash, send_file
)
from sklearn.ensemble import IsolationForest

# ReportLab & Matplotlib Imports for PDF Generation
import matplotlib
matplotlib.use('Agg')  # Non-interactive backend for server environments
import matplotlib.pyplot as plt

from reportlab.lib.pagesizes import letter
from reportlab.lib import colors
from reportlab.platypus import SimpleDocTemplate, Paragraph, Spacer, Table, TableStyle, Image
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle

app = Flask(__name__)
app.secret_key = 'lora_gateway_secret_key_change_me'

CSV_PATH = os.path.join(os.path.dirname(__file__), 'data', 'sensor_history.csv')
CSV_HEADERS = [
    'timestamp', 'raw_payload', 'data_type', 
    'parsed_json', 'rssi', 'snr', 'is_anomaly', 'anomaly_reason'
]

def init_csv():
    os.makedirs(os.path.dirname(CSV_PATH), exist_ok=True)
    if not os.path.exists(CSV_PATH) or os.path.getsize(CSV_PATH) == 0:
        with open(CSV_PATH, mode='w', newline='') as f:
            writer = csv.writer(f)
            writer.writerow(CSV_HEADERS)

init_csv()

# -------------------------------------------------------------------
# Advanced AI: Dynamic Velocity Engine + EWMA Drift
# -------------------------------------------------------------------
class DynamicTrendMLModel:
    def __init__(self, alpha=0.15):
        self.alpha = alpha
        self.ewma_temp = None
        self.ewma_hum = None
        self.last_ts = None
        self.last_temp = None
        self.last_hum = None
        
        self.temp_velocities = []
        self.hum_velocities = []
        
        self.iso_forest = IsolationForest(contamination=0.05, random_state=42)
        self.is_fitted = False
        self.fit_model()

    def fit_model(self):
        if not os.path.exists(CSV_PATH) or os.path.getsize(CSV_PATH) == 0:
            return
        try:
            df = pd.read_csv(CSV_PATH)
            if 'parsed_json' not in df.columns:
                return

            feature_rows = []
            for _, row in df.iterrows():
                try:
                    parsed = json.loads(row['parsed_json'])
                    if 'temperature' in parsed and 'humidity' in parsed:
                        feature_rows.append([parsed['temperature'], parsed['humidity'], float(row['rssi'])])
                except Exception:
                    continue
            
            if len(feature_rows) >= 15:
                X = np.array(feature_rows)
                self.iso_forest.fit(X)
                self.is_fitted = True
        except Exception as e:
            print(f"[ML Model Fit Error] {e}")

    def classify_and_parse(self, raw):
        raw = raw.strip()
        th_match = re.search(r'H:?\s*(\d+\.?\d*)%?\s*T:?\s*(\d+\.?\d*)C?', raw, re.IGNORECASE) or \
                   re.search(r'T:?\s*(\d+\.?\d*)C?\s*H:?\s*(\d+\.?\d*)%?', raw, re.IGNORECASE)
        if th_match:
            groups = th_match.groups()
            if "H:" in raw[:3].upper():
                h, t = float(groups[0]), float(groups[1])
            else:
                t, h = float(groups[0]), float(groups[1])
            return "Temp & Humidity", {"temperature": t, "humidity": h}

        level_match = re.search(r'Level:\s*([01]{3})', raw, re.IGNORECASE)
        if level_match:
            bits = level_match.group(1)
            percent = {"111": 100, "011": 66, "001": 33, "000": 0}.get(bits, -1)
            return "Water Level Probe", {"probe_bits": bits, "level_percent": percent}

        count_match = re.search(r'Count:\s*(\d+)', raw, re.IGNORECASE)
        if count_match:
            return "Counter / Heartbeat", {"count": int(count_match.group(1))}

        return "Unclassified Raw Data", {"raw": raw}

    def detect_anomaly(self, timestamp_str, data_type, parsed, rssi):
        is_anomaly = 0
        reasons = []

        if data_type == "Temp & Humidity":
            temp = parsed.get("temperature", 0)
            hum = parsed.get("humidity", 0)
            
            try:
                curr_ts = datetime.strptime(timestamp_str, "%Y-%m-%d %H:%M:%S")
            except Exception:
                curr_ts = datetime.now()

            if self.last_ts is not None and self.last_temp is not None and self.last_hum is not None:
                time_delta_min = max((curr_ts - self.last_ts).total_seconds() / 60.0, 0.001)

                temp_vel = (temp - self.last_temp) / time_delta_min
                hum_vel = (hum - self.last_hum) / time_delta_min

                if len(self.temp_velocities) >= 5:
                    mean_v_temp = np.mean(self.temp_velocities)
                    std_v_temp = np.std(self.temp_velocities) + 1e-5
                    if (abs(temp_vel - mean_v_temp) / std_v_temp) > 3.0:
                        is_anomaly = 1
                        reasons.append(f"Sudden Temp Spike ({temp_vel:+.1f}°C/min)")

                if len(self.hum_velocities) >= 5:
                    mean_v_hum = np.mean(self.hum_velocities)
                    std_v_hum = np.std(self.hum_velocities) + 1e-5
                    if (abs(hum_vel - mean_v_hum) / std_v_hum) > 3.0:
                        is_anomaly = 1
                        reasons.append(f"Sudden Humidity Shift ({hum_vel:+.1f}%/min)")

                self.temp_velocities.append(temp_vel)
                self.hum_velocities.append(hum_vel)
                if len(self.temp_velocities) > 50:
                    self.temp_velocities.pop(0)
                    self.hum_velocities.pop(0)

                self.ewma_temp = (self.alpha * temp) + ((1 - self.alpha) * self.ewma_temp)
                self.ewma_hum = (self.alpha * hum) + ((1 - self.alpha) * self.ewma_hum)
            else:
                self.ewma_temp = temp
                self.ewma_hum = hum

            self.last_ts = curr_ts
            self.last_temp = temp
            self.last_hum = hum

            if self.is_fitted:
                features = np.array([[temp, hum, rssi]])
                if self.iso_forest.predict(features)[0] == -1:
                    is_anomaly = 1
                    reasons.append("Isolation Forest ML Outlier")

            if temp > 55 or temp < -10:
                is_anomaly = 1
                reasons.append(f"Critical Temp Limit ({temp}°C)")

        elif data_type == "Water Level Probe":
            if parsed.get("level_percent") == 0:
                is_anomaly = 1
                reasons.append("Tank Empty Warning")

        reason_str = " | ".join(reasons) if is_anomaly else "Normal"
        return is_anomaly, reason_str

ml_engine = DynamicTrendMLModel()

def store_to_csv(raw, rssi, snr):
    init_csv()
    ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    data_type, parsed = ml_engine.classify_and_parse(raw)
    is_anomaly, reason = ml_engine.detect_anomaly(ts, data_type, parsed, rssi)

    with open(CSV_PATH, mode='a', newline='') as f:
        writer = csv.writer(f)
        writer.writerow([
            ts, raw, data_type, json.dumps(parsed), rssi, snr, is_anomaly, reason
        ])

# -------------------------------------------------------------------
# TCP Socket Reader Thread (replaces Serial)
# -------------------------------------------------------------------
def tcp_bridge_reader_thread():
    host = '127.0.0.1'
    port = 7500
    
    while True:
        try:
            print(f"Connecting to LoRa bridge at {host}:{port}...")
            with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
                s.connect((host, port))
                print(f"[TCP Bridge] Connected successfully to {host}:{port}")
                
                buffer = ""
                while True:
                    data = s.recv(1024)
                    if not data:
                        print("[TCP Bridge] Connection closed by remote host.")
                        break
                    
                    buffer += data.decode('utf-8', errors='ignore')
                    while '\n' in buffer:
                        line, buffer = buffer.split('\n', 1)
                        line = line.strip()
                        if line.startswith("{") and line.endswith("}"):
                            try:
                                pkt = json.loads(line)
                                store_to_csv(
                                    raw=pkt.get("payload", ""),
                                    rssi=float(pkt.get("rssi", 0)),
                                    snr=float(pkt.get("snr", 0))
                                )
                            except json.JSONDecodeError:
                                pass
        except Exception as e:
            print(f"LoRa connection error: {e}. Retrying in 5 seconds...")
            time.sleep(5)

if os.environ.get("WERKZEUG_RUN_MAIN") == "true" or not app.debug:
    threading.Thread(target=tcp_bridge_reader_thread, daemon=True).start()

# -------------------------------------------------------------------
# Auth Guard & Routes
# -------------------------------------------------------------------
def login_required(f):
    def wrapper(*args, **kwargs):
        if not session.get('logged_in'):
            return redirect(url_for('login'))
        return f(*args, **kwargs)
    wrapper.__name__ = f.__name__
    return wrapper

@app.route('/login', methods=['GET', 'POST'])
def login():
    if request.method == 'POST':
        if request.form.get('username') == 'admin' and request.form.get('password') == 'admin123':
            session['logged_in'] = True
            return redirect(url_for('index'))
        else:
            flash("Invalid Username or Password!", "danger")
    return render_template('login.html')

@app.route('/logout')
def logout():
    session.clear()
    return redirect(url_for('login'))

@app.route('/')
@login_required
def index():
    return render_template('index.html')

@app.route('/history')
@login_required
def history():
    return render_template('history.html')

@app.route('/analytics')
@login_required
def analytics():
    return render_template('analytics.html')

# -------------------------------------------------------------------
# PDF Chart Helper
# -------------------------------------------------------------------
def generate_chart_image(timestamps, values, title, ylabel, color):
    fig, ax = plt.subplots(figsize=(6, 2.2), dpi=150)
    fig.patch.set_facecolor('#1e293b')
    ax.set_facecolor('#0f172a')
    
    ax.plot(timestamps, values, color=color, linewidth=1.5)
    ax.set_title(title, color='#f8fafc', fontsize=9, fontweight='bold', pad=6)
    ax.set_ylabel(ylabel, color='#94a3b8', fontsize=8)
    
    ax.tick_params(colors='#94a3b8', labelsize=7)
    ax.grid(True, color='#334155', linestyle='--', linewidth=0.5)
    
    if len(timestamps) > 6:
        step = len(timestamps) // 5
        ax.set_xticks(range(0, len(timestamps), step))
        ax.set_xticklabels([timestamps[i].split(' ')[1] for i in range(0, len(timestamps), step)], rotation=15)
    else:
        plt.xticks(rotation=15)

    plt.tight_layout()
    
    img_buf = io.BytesIO()
    plt.savefig(img_buf, format='png', facecolor=fig.get_facecolor(), edgecolor='none')
    plt.close(fig)
    img_buf.seek(0)
    return img_buf

# -------------------------------------------------------------------
# PDF Report Generator
# -------------------------------------------------------------------
@app.route('/download_report')
@login_required
def download_report():
    records = parse_csv_dataframe()
    buffer = io.BytesIO()
    
    doc = SimpleDocTemplate(buffer, pagesize=letter, rightMargin=30, leftMargin=30, topMargin=30, bottomMargin=30)
    styles = getSampleStyleSheet()
    
    title_style = ParagraphStyle('TitleStyle', parent=styles['Heading1'], fontSize=18, textColor=colors.HexColor('#0f172a'))
    meta_style = ParagraphStyle('MetaStyle', parent=styles['Normal'], fontSize=9, textColor=colors.HexColor('#475569'))
    body_style = ParagraphStyle('BodyStyle', parent=styles['Normal'], fontSize=8, leading=10)
    
    elements = []
    
    elements.append(Paragraph("📡 UNO Q Gateway - Telemetry & Analytics Audit Report", title_style))
    elements.append(Paragraph(f"Generated On: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')} | Total Readings: {len(records)}", meta_style))
    elements.append(Spacer(1, 12))
    
    total_anomalies = sum(1 for r in records if r['is_anomaly'] == 1)
    temps = [r['parsed'].get('temperature') for r in records if 'temperature' in r['parsed']]
    avg_temp = f"{np.mean(temps):.1f}°C" if temps else "N/A"
    
    summary_data = [
        ["Total Logged Frames", "Flagged Anomalies", "Average Temp", "Gateway Signal State"],
        [str(len(records)), str(total_anomalies), avg_temp, "Active (TCP 7500 Bridge)"]
    ]
    t_summary = Table(summary_data, colWidths=[130, 130, 130, 140])
    t_summary.setStyle(TableStyle([
        ('BACKGROUND', (0,0), (-1,0), colors.HexColor('#1e293b')),
        ('TEXTCOLOR', (0,0), (-1,0), colors.white),
        ('FONTNAME', (0,0), (-1,0), 'Helvetica-Bold'),
        ('ALIGN', (0,0), (-1,-1), 'CENTER'),
        ('GRID', (0,0), (-1,-1), 0.5, colors.HexColor('#cbd5e1')),
        ('BACKGROUND', (0,1), (-1,1), colors.HexColor('#f8fafc')),
    ]))
    elements.append(t_summary)
    elements.append(Spacer(1, 15))

    if records:
        elements.append(Paragraph("<b>Telemetry Analytics Trends</b>", styles['Heading3']))
        elements.append(Spacer(1, 6))

        timestamps = [r['timestamp'] for r in records[-40:]]
        temp_vals = [r['parsed'].get('temperature', 0) for r in records[-40:]]
        hum_vals = [r['parsed'].get('humidity', 0) for r in records[-40:]]
        rssi_vals = [r['rssi'] for r in records[-40:]]
        snr_vals = [r['snr'] for r in records[-40:]]

        img_temp = generate_chart_image(timestamps, temp_vals, "Temperature Stream (°C)", "°C", "#f43f5e")
        img_hum = generate_chart_image(timestamps, hum_vals, "Humidity Stream (%)", "%", "#38bdf8")
        img_rssi = generate_chart_image(timestamps, rssi_vals, "Signal Strength (RSSI)", "dBm", "#10b981")
        img_snr = generate_chart_image(timestamps, snr_vals, "Signal-to-Noise Ratio (SNR)", "dB", "#a855f7")

        chart_table_data = [
            [Image(img_temp, width=260, height=100), Image(img_hum, width=260, height=100)],
            [Image(img_rssi, width=260, height=100), Image(img_snr, width=260, height=100)]
        ]
        t_charts = Table(chart_table_data, colWidths=[270, 270])
        t_charts.setStyle(TableStyle([('ALIGN', (0,0), (-1,-1), 'CENTER'), ('VALIGN', (0,0), (-1,-1), 'MIDDLE')]))
        elements.append(t_charts)
        elements.append(Spacer(1, 15))

    elements.append(Paragraph("<b>Detailed Telemetry & Anomaly Log</b>", styles['Heading3']))
    elements.append(Spacer(1, 6))

    table_data = [["Timestamp", "Payload", "Type", "Parsed Readings", "Signal", "ML Anomaly Reason"]]
    
    for r in records[-50:]:
        reason_p = Paragraph(r['anomaly_reason'], body_style)
        payload_p = Paragraph(f"<code>{r['raw_payload']}</code>", body_style)
        parsed_p = Paragraph(json.dumps(r['parsed']), body_style)
        
        table_data.append([
            r['timestamp'],
            payload_p,
            r['data_type'],
            parsed_p,
            f"{r['rssi']}dBm\n{r['snr']}dB",
            reason_p
        ])

    t_detail = Table(table_data, colWidths=[90, 85, 75, 110, 50, 140])
    
    ts_style = [
        ('BACKGROUND', (0,0), (-1,0), colors.HexColor('#0f172a')),
        ('TEXTCOLOR', (0,0), (-1,0), colors.white),
        ('FONTNAME', (0,0), (-1,0), 'Helvetica-Bold'),
        ('FONTSIZE', (0,0), (-1,-1), 8),
        ('VALIGN', (0,0), (-1,-1), 'MIDDLE'),
        ('GRID', (0,0), (-1,-1), 0.5, colors.HexColor('#94a3b8')),
    ]
    
    for i, r in enumerate(records[-50:], start=1):
        if r['is_anomaly'] == 1:
            ts_style.append(('BACKGROUND', (0, i), (-1, i), colors.HexColor('#fee2e2')))
            ts_style.append(('TEXTCOLOR', (0, i), (-1, i), colors.HexColor('#991b1b')))

    t_detail.setStyle(TableStyle(ts_style))
    elements.append(t_detail)
    
    doc.build(elements)
    buffer.seek(0)
    
    return send_file(
        buffer,
        as_attachment=True,
        download_name=f"LoRa_Gateway_Report_{datetime.now().strftime('%Y%m%d_%H%M')}.pdf",
        mimetype='application/pdf'
    )

# -------------------------------------------------------------------
# Data APIs
# -------------------------------------------------------------------
def parse_csv_dataframe(limit=None):
    init_csv()
    try:
        df = pd.read_csv(CSV_PATH)
        if df.empty or 'timestamp' not in df.columns:
            return []
        
        if limit:
            df = df.tail(limit)

        records = []
        for _, r in df.iterrows():
            try:
                parsed = json.loads(r['parsed_json'])
            except Exception:
                parsed = {}
            records.append({
                "timestamp": str(r['timestamp']),
                "raw_payload": str(r['raw_payload']),
                "data_type": str(r['data_type']),
                "parsed": parsed,
                "rssi": float(r['rssi']),
                "snr": float(r['snr']),
                "is_anomaly": int(r['is_anomaly']),
                "anomaly_reason": str(r['anomaly_reason'])
            })
        return records
    except Exception as e:
        print(f"[CSV Parse Error] {e}")
        return []

@app.route('/api/data')
@login_required
def get_data():
    return jsonify(list(reversed(parse_csv_dataframe(limit=100))))

@app.route('/api/history_all')
@login_required
def get_all_history():
    return jsonify(parse_csv_dataframe())

if __name__ == '__main__':
    app.run(host='0.0.0.0', port=5050, debug=True, use_reloader=False)