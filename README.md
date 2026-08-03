# Industrial IoT Motor Monitoring and Control System

## Overview
This project implements an Industrial Internet of Things (IIoT) motor monitoring and control system using a simulated electric motor, an edge gateway, MQTT communication, and the ThingsBoard IoT platform.

## Features
- Real-time motor process simulation
- MQTT communication
- Edge-side processing and automatic cooling
- Five-second aggregation during normal operation
- One-second reporting during critical events
- Fan failure and overload simulation
- Thermal hard-trip protection and recovery
- Manual fan RPM control via RPC
- Emergency stop and restart
- ThingsBoard dashboard

## System Architecture
```text
Motor Simulator
      |
 MQTT Broker
      |
 Edge Gateway
      |
 ThingsBoard
      |
 Web Dashboard
```

## Project Structure
```text
industrial-iot/
├── edge_gateway.py
├── motor_simulator.py
├── mqtt_topics.py
├── requirements.txt
├── docker-compose.yml
├── thingsboard/
├── dashboards/
└── tests/
```

## Requirements
- Python 3.10+
- Docker & Docker Compose
- ThingsBoard CE
- PostgreSQL
- Eclipse Mosquitto

## Installation
```bash
git clone <repository-url>
cd <repository>
python -m venv .venv
source .venv/bin/activate      # Linux/WSL
# or
.venv\Scripts\activate       # Windows

pip install -r requirements.txt
docker-compose up -d
python motor_simulator.py
python edge_gateway.py
```

Open **http://localhost:8080**

Default credentials:
- Username: `tenant@thingsboard.org`
- Password: `tenant`

## Technologies
- Python
- MQTT (Paho MQTT)
- ThingsBoard CE
- Docker
- PostgreSQL
- Eclipse Mosquitto

## License
Developed for educational purposes as part of an Industrial Internet of Things (IIoT) course project.
