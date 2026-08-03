# Industrial IoT Motor Monitoring and Control System

## Overview
This project implements an Industrial Internet of Things (IIoT) motor monitoring and control system using Python, MQTT, Docker, and ThingsBoard. It demonstrates real-time telemetry, edge processing, automatic cooling control, fault detection, and interactive dashboard visualization.

## Features
- Real-time motor process simulation
- MQTT-based communication
- Edge-side analytics and decision making
- Automatic fan control
- Manual fan RPM control through ThingsBoard RPC
- Five-second telemetry aggregation
- One-second emergency reporting
- Fan failure and overload simulation
- Thermal hard-trip protection
- Interactive ThingsBoard dashboard

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
├── config.py
├── requirements.txt
├── .env
├── industrial_iot_motor_control_center_v8.json
├── industrial_iot_motor_control_center_v9_fixed.json
├── manual_fan_rpm_control_widget.json
├── README.md
├── README_MANUAL_RPM_FIX.md
├── README_RUN_WINDOWS.txt
└── test_logic.py
```

## Requirements
- Python 3.10+
- Docker and Docker Compose
- ThingsBoard Community Edition
- PostgreSQL
- MQTT Broker (Mosquitto or ThingsBoard built-in broker)

## Installation

1. Clone the repository.
2. Install the Python dependencies:

```bash
pip install -r requirements.txt
```

3. Start ThingsBoard using Docker.

```bash
docker-compose up -d
```

4. Run the simulator.

```bash
python motor_simulator.py
```

5. Run the edge gateway.

```bash
python edge_gateway.py
```

6. Open ThingsBoard at:

```
http://localhost:8080
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
