import os

try:
    from dotenv import load_dotenv
except Exception:  # pragma: no cover - optional helper during bootstrap
    load_dotenv = None

if load_dotenv is not None:
    load_dotenv()


def env_int(name: str, default: int) -> int:
    try:
        return int(os.getenv(name, default))
    except (TypeError, ValueError):
        return default


def env_float(name: str, default: float) -> float:
    try:
        return float(os.getenv(name, default))
    except (TypeError, ValueError):
        return default


# ---------------------------------------------------------------------
# Local plant MQTT broker
# Simulator <--> Edge Gateway
# ---------------------------------------------------------------------
LOCAL_MQTT_HOST = os.getenv("LOCAL_MQTT_HOST", "localhost")
LOCAL_MQTT_PORT = env_int("LOCAL_MQTT_PORT", 1883)

# ---------------------------------------------------------------------
# ThingsBoard MQTT
# Host port 1884 is mapped to ThingsBoard container port 1883.
# Web UI remains http://localhost:8080
# ---------------------------------------------------------------------
TB_HOST = os.getenv("TB_HOST", "localhost")
TB_PORT = env_int("TB_PORT", 1884)
TB_ACCESS_TOKEN = os.getenv("TB_ACCESS_TOKEN", "PUT_YOUR_DEVICE_ACCESS_TOKEN_HERE")

# ---------------------------------------------------------------------
# Device identity
# ---------------------------------------------------------------------
DEVICE_ID = os.getenv("DEVICE_ID", "motor-01")

# ---------------------------------------------------------------------
# MQTT topics between simulator and edge gateway
# ---------------------------------------------------------------------
RAW_TOPIC = f"factory/{DEVICE_ID}/raw"
COMMAND_TOPIC = f"factory/{DEVICE_ID}/command"

# ---------------------------------------------------------------------
# ThingsBoard topics
# ---------------------------------------------------------------------
TB_TELEMETRY_TOPIC = "v1/devices/me/telemetry"
TB_ATTRIBUTES_TOPIC = "v1/devices/me/attributes"
TB_RPC_REQUEST_TOPIC = "v1/devices/me/rpc/request/+"
TB_RPC_RESPONSE_TOPIC_PREFIX = "v1/devices/me/rpc/response"

# ---------------------------------------------------------------------
# Physical and control constants
# ---------------------------------------------------------------------
SIMULATION_STEP_SEC = env_float("SIMULATION_STEP_SEC", 1.0)

TEMP_WARNING_C = env_float("TEMP_WARNING_C", 70.0)
TEMP_CRITICAL_C = env_float("TEMP_CRITICAL_C", 85.0)
TEMP_HARD_TRIP_C = env_float("TEMP_HARD_TRIP_C", 95.0)
TEMP_RECOVERY_C = env_float("TEMP_RECOVERY_C", 60.0)

AMBIENT_TEMP_C = env_float("AMBIENT_TEMP_C", 25.0)

MIN_FAN_RPM = 0
BASE_FAN_RPM = 800
MAX_FAN_RPM = 3200

NORMAL_REPORT_INTERVAL_SEC = 5.0
CRITICAL_REPORT_INTERVAL_SEC = 1.0

EDGE_STALE_DATA_TIMEOUT_SEC = 4.0
