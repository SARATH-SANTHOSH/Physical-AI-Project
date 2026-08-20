# AI Gateway Telemetry Dashboard

A Flask dashboard for monitoring LoRa telemetry, detecting sensor anomalies, viewing historical readings, and exporting PDF reports.

## Features

- Temperature and humidity, water-level, and counter/heartbeat payload parsing
- Trend-based anomaly detection and Isolation Forest outlier detection
- CSV persistence for telemetry history
- Dashboard, history, and analytics views
- Login-protected web pages and JSON data APIs
- PDF reports with charts and anomaly details

## Architecture

The Flask application listens on `0.0.0.0:5050`. A background TCP client connects to a LoRa bridge at `127.0.0.1:7500` and expects newline-delimited JSON messages such as:

```json
{"payload":"H:50% T:22C","rssi":-60,"snr":8.5}
```

Each accepted frame is parsed, checked for anomalies, and appended to `app/data/sensor_history.csv`. The CSV directory is mounted as a Docker volume so data survives container recreation.

## Requirements

- Python 3.10 or newer for local development
- Docker Desktop for the containerized setup
- A TCP LoRa bridge listening on port `7500` when live telemetry is required

## Project Structure

```text
.
├── app/
│   ├── app.py
│   ├── data/sensor_history.csv
│   └── templates/
├── Dockerfile
├── docker-compose.yml
└── requirements.txt
```

## Run Locally

```bash
python3 -m venv .venv
source .venv/bin/activate
python -m pip install --upgrade pip
python -m pip install -r requirements.txt
python app/app.py
```

Open [http://localhost:5050](http://localhost:5050). The default login is:

| Username | Password |
| --- | --- |
| `admin` | `admin123` |

These credentials and the Flask secret key are development defaults. Change them before using the application in a shared or production environment.

## Run with Docker Compose

Build and start the service:

```bash
docker compose up --build
```

Run it in the background:

```bash
docker compose up --build -d
```

Open [http://localhost:5050](http://localhost:5050). View logs or stop the service with:

```bash
docker compose logs -f lora-gateway
docker compose down
```

The host directory `app/data` is mounted at `/app/app/data` in the container. Your telemetry CSV remains available after `docker compose down` and later restarts.

## Build and Run the Image Directly

```bash
docker build -t lora-unoq-gateway:latest .
docker run --name unoq_gateway \
  -p 5050:5050 \
  -v "$(pwd)/app/data:/app/app/data" \
  lora-unoq-gateway:latest
```

Stop and remove the container:

```bash
docker stop unoq_gateway
docker rm unoq_gateway
```

## Routes

| Path | Purpose |
| --- | --- |
| `/login` | Sign in |
| `/` | Live dashboard |
| `/history` | Historical readings |
| `/analytics` | Charts and analysis |
| `/download_report` | Download a PDF report |
| `/api/data` | Latest readings as JSON |
| `/api/history_all` | Full history as JSON |

## Telemetry Payloads

Supported payload patterns include:

- `H:50% T:22C`
- `T:22C H:50%`
- `Level:011`
- `Count:18`

The TCP bridge should send one JSON object per line with `payload`, `rssi`, and `snr` fields.

## Troubleshooting

- Check application output with `docker compose logs -f lora-gateway`.
- Confirm that port `5050` is available on the host.
- Confirm that the LoRa bridge is listening on `127.0.0.1:7500` from the same network namespace as the Flask process.
- If no bridge is connected, the dashboard still starts and can display existing CSV data.

## License

No license is currently specified. Add a license before distributing this project.
