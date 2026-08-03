import json
import random
import statistics
import time
from collections import deque
from typing import Any, Dict, List, Optional

import paho.mqtt.client as mqtt

from config import (
    LOCAL_MQTT_HOST,
    LOCAL_MQTT_PORT,
    TB_HOST,
    TB_PORT,
    TB_ACCESS_TOKEN,
    DEVICE_ID,
    RAW_TOPIC,
    COMMAND_TOPIC,
    TB_TELEMETRY_TOPIC,
    TB_ATTRIBUTES_TOPIC,
    TB_RPC_REQUEST_TOPIC,
    TB_RPC_RESPONSE_TOPIC_PREFIX,
    TEMP_WARNING_C,
    TEMP_CRITICAL_C,
    TEMP_HARD_TRIP_C,
    TEMP_RECOVERY_C,
    BASE_FAN_RPM,
    MAX_FAN_RPM,
    MIN_FAN_RPM,
    NORMAL_REPORT_INTERVAL_SEC,
    CRITICAL_REPORT_INTERVAL_SEC,
    EDGE_STALE_DATA_TIMEOUT_SEC,
)


def clamp(value: float, low: float, high: float) -> float:
    return max(low, min(high, value))


def now_ms() -> int:
    return int(time.time() * 1000)


def make_mqtt_client(client_id: str) -> mqtt.Client:
    """Compatible with both paho-mqtt 1.x and 2.x."""
    try:
        return mqtt.Client(mqtt.CallbackAPIVersion.VERSION2, client_id=client_id)
    except Exception:
        return mqtt.Client(client_id=client_id)


class EdgeController:
    def __init__(self) -> None:
        # Keep enough history for calculations and dashboard diagnostics.
        self.samples = deque(maxlen=180)
        self.temp_rate_history = deque(maxlen=10)

        self.latest_sample: Optional[dict] = None
        self.previous_sample: Optional[dict] = None

        self.last_raw_arrival_time = 0.0
        self.last_cloud_send_time = 0.0
        self.last_stale_alarm_time = 0.0

        self.raw_samples_received = 0
        self.cloud_messages_sent = 0
        self.local_commands_sent = 0

        self.manual_mode = False
        self.manual_fan_rpm = BASE_FAN_RPM

        self.operator_emergency_stop = False
        self.edge_hard_trip = False
        self.hard_trip_reason = "none"
        self.last_auto_recovery_time = 0.0

        self.edge_state = "BOOTING"
        self.active_alarm = "none"

        self.temperature_rate_c_per_s = 0.0
        self.cooling_failure_latched = False
        self.cooling_failure_confirm_count = 0
        self.cooling_failure_clear_count = 0

    def process_raw_sample(self, sample: dict) -> dict:
        self.previous_sample = self.latest_sample
        self.latest_sample = sample
        self.samples.append(sample)

        self.raw_samples_received += 1
        self.last_raw_arrival_time = time.time()

        self.temperature_rate_c_per_s = self._calculate_temperature_rate()
        self.temp_rate_history.append(self.temperature_rate_c_per_s)

        decision = self._control_decision(sample)
        return decision

    def _calculate_temperature_rate(self) -> float:
        if not self.latest_sample or not self.previous_sample:
            return 0.0

        t1 = float(self.previous_sample.get("temperature_c", 0.0))
        t2 = float(self.latest_sample.get("temperature_c", 0.0))

        ts1 = float(self.previous_sample.get("ts", now_ms())) / 1000.0
        ts2 = float(self.latest_sample.get("ts", now_ms())) / 1000.0

        dt = max(ts2 - ts1, 0.001)
        return (t2 - t1) / dt

    def _calculate_smoothed_temperature_rate(self) -> float:
        if not self.temp_rate_history:
            return 0.0
        recent = list(self.temp_rate_history)[-5:]
        return statistics.mean(recent)

    def _update_cooling_failure_latch(self, fan_rpm: float) -> bool:
        """
        Stabilized cooling-failure detection.

        Trigger condition:
        - fan RPM near zero, and
        - temperature trend rising for several consecutive samples.

        Clear condition:
        - fan has recovered above a healthy threshold, and
        - temperature trend has stabilized for several consecutive samples.
        """
        smoothed_rate = self._calculate_smoothed_temperature_rate()
        suspicion = fan_rpm < 100.0 and smoothed_rate > 0.03
        recovery = fan_rpm > 300.0 and smoothed_rate <= 0.03

        if suspicion:
            self.cooling_failure_confirm_count += 1
            self.cooling_failure_clear_count = 0
        else:
            self.cooling_failure_confirm_count = 0
            if self.cooling_failure_latched and recovery:
                self.cooling_failure_clear_count += 1
            else:
                self.cooling_failure_clear_count = 0

        if self.cooling_failure_confirm_count >= 3:
            self.cooling_failure_latched = True

        if self.cooling_failure_clear_count >= 3:
            self.cooling_failure_latched = False
            self.cooling_failure_confirm_count = 0
            self.cooling_failure_clear_count = 0

        return self.cooling_failure_latched

    def _control_decision(self, sample: dict) -> dict:
        temp = float(sample.get("temperature_c", 0.0))
        load = float(sample.get("load_percent", 0.0))
        fan_rpm = float(sample.get("fan_rpm", 0.0))
        running = bool(sample.get("running", False))

        alarm_motor_overload = False
        alarm_critical_temperature = False
        alarm_hard_trip = False
        just_auto_recovered = False

        if temp >= TEMP_CRITICAL_C:
            alarm_critical_temperature = True
            if load >= 85.0:
                alarm_motor_overload = True

        alarm_cooling_failed = self._update_cooling_failure_latch(fan_rpm)

        if temp >= TEMP_HARD_TRIP_C:
            alarm_hard_trip = True
            self.edge_hard_trip = True
            self.hard_trip_reason = "thermal_hard_trip"

        cooled_down = temp <= TEMP_RECOVERY_C
        if self.edge_hard_trip and cooled_down and not self.operator_emergency_stop:
            self.edge_hard_trip = False
            self.hard_trip_reason = "none"
            self.last_auto_recovery_time = time.time()
            just_auto_recovered = True

        motor_should_run = running

        if self.operator_emergency_stop:
            fan_command = MAX_FAN_RPM
            motor_should_run = False
            self.edge_state = "OPERATOR_EMERGENCY_STOP"
            self.active_alarm = "operator_emergency_stop"

        elif self.edge_hard_trip or alarm_hard_trip:
            fan_command = MAX_FAN_RPM
            motor_should_run = False
            self.edge_state = "EDGE_HARD_TRIP"
            self.active_alarm = "thermal_hard_trip"

        elif just_auto_recovered:
            fan_command = BASE_FAN_RPM
            motor_should_run = True
            self.edge_state = "AUTO_RECOVERED"
            self.active_alarm = "none"
            return {
                "cmd": "reset_emergency",
                "reason": "auto_recovery_after_cooldown",
            }

        elif alarm_cooling_failed:
            fan_command = MAX_FAN_RPM
            self.edge_state = "COOLING_SYSTEM_FAILED"
            self.active_alarm = "cooling_system_failed"

        elif alarm_motor_overload:
            fan_command = MAX_FAN_RPM
            self.edge_state = "MOTOR_OVERLOAD"
            self.active_alarm = "motor_overload"

        elif alarm_critical_temperature:
            fan_command = MAX_FAN_RPM
            self.edge_state = "CRITICAL_TEMPERATURE"
            self.active_alarm = "critical_temperature"

        elif self.manual_mode:
            fan_command = self.manual_fan_rpm
            self.edge_state = "MANUAL_CONTROL"
            self.active_alarm = "none"

        else:
            if temp >= TEMP_WARNING_C:
                extra = (temp - TEMP_WARNING_C) * 170.0
                slope_boost = max(self._calculate_smoothed_temperature_rate(), 0.0) * 250.0
                fan_command = 1800.0 + extra + slope_boost
                self.edge_state = "EDGE_COOLING"
                self.active_alarm = "none"
            else:
                fan_command = BASE_FAN_RPM
                self.edge_state = "NORMAL"
                self.active_alarm = "none"

        fan_command = clamp(fan_command, MIN_FAN_RPM, MAX_FAN_RPM)
        return {
            "cmd": "control",
            "fan_rpm": round(fan_command, 0),
            "running": motor_should_run,
        }

    def is_emergency(self) -> bool:
        return self.edge_state in [
            "MOTOR_OVERLOAD",
            "COOLING_SYSTEM_FAILED",
            "CRITICAL_TEMPERATURE",
            "EDGE_HARD_TRIP",
            "OPERATOR_EMERGENCY_STOP",
        ]

    def should_report_to_cloud(self) -> bool:
        now = time.time()
        interval = CRITICAL_REPORT_INTERVAL_SEC if self.is_emergency() else NORMAL_REPORT_INTERVAL_SEC
        return (now - self.last_cloud_send_time) >= interval

    def mark_cloud_sent(self) -> None:
        self.last_cloud_send_time = time.time()
        self.cloud_messages_sent += 1

    def _get_reporting_samples(self) -> List[dict]:
        if not self.latest_sample:
            return []

        if self.is_emergency():
            return [self.latest_sample]

        latest_ts = float(self.latest_sample.get("ts", now_ms()))
        cutoff_ts = latest_ts - (NORMAL_REPORT_INTERVAL_SEC * 1000.0)
        report_samples = [s for s in self.samples if float(s.get("ts", 0)) >= cutoff_ts]
        return report_samples or [self.latest_sample]

    def build_telemetry_for_cloud(self) -> dict:
        if not self.latest_sample:
            return {
                "edge_state": "NO_DATA",
                "active_alarm": "no_data",
                "data_stale": True,
            }

        latest = self.latest_sample
        report_samples = self._get_reporting_samples()
        temp_values = [float(s.get("temperature_c", 0.0)) for s in report_samples]
        load_values = [float(s.get("load_percent", 0.0)) for s in report_samples]
        fan_values = [float(s.get("fan_rpm", 0.0)) for s in report_samples]
        current_values = [float(s.get("current_a", 0.0)) for s in report_samples]
        vibration_values = [float(s.get("vibration_mm_s", 0.0)) for s in report_samples]

        temp_latest = float(latest.get("temperature_c", 0.0))
        fan_latest = float(latest.get("fan_rpm", 0.0))
        load_latest = float(latest.get("load_percent", 0.0))

        traffic_saving = 0.0
        if self.raw_samples_received > 0:
            traffic_saving = 100.0 * (
                1.0 - self.cloud_messages_sent / max(self.raw_samples_received, 1)
            )
            traffic_saving = clamp(traffic_saving, 0.0, 100.0)

        thermal_risk_index = clamp(
            ((temp_latest - 50.0) / (TEMP_CRITICAL_C - 50.0)) * 100.0,
            0.0,
            100.0,
        )

        alarm_motor_overload = self.edge_state == "MOTOR_OVERLOAD"
        alarm_cooling_failed = self.edge_state == "COOLING_SYSTEM_FAILED"
        alarm_critical_temp = self.edge_state in [
            "CRITICAL_TEMPERATURE",
            "MOTOR_OVERLOAD",
            "EDGE_HARD_TRIP",
        ]
        alarm_hard_trip = self.edge_state == "EDGE_HARD_TRIP"
        estimated_remaining_life_percent = float(latest.get("health_score", 100.0))

        report_mode = "realtime_1s" if self.is_emergency() else "average_5s"
        report_window_seconds = CRITICAL_REPORT_INTERVAL_SEC if self.is_emergency() else NORMAL_REPORT_INTERVAL_SEC

        return {
            "temperature": round(statistics.mean(temp_values), 2),
            "temperature_latest": round(temp_latest, 2),
            "temperature_min": round(min(temp_values), 2),
            "temperature_max": round(max(temp_values), 2),
            "temperature_rate_c_per_s": round(self.temperature_rate_c_per_s, 3),
            "temperature_rate_smoothed_c_per_s": round(self._calculate_smoothed_temperature_rate(), 3),

            "load_percent": round(statistics.mean(load_values), 2),
            "load_latest": round(load_latest, 2),

            "fan_rpm": round(statistics.mean(fan_values), 0),
            "fan_rpm_latest": round(fan_latest, 0),
            "fan_rpm_manual_setpoint": round(self.manual_fan_rpm, 0),

            "current_a": round(statistics.mean(current_values), 2),
            "vibration_mm_s": round(statistics.mean(vibration_values), 3),

            "power_kw": latest.get("power_kw", 0.0),
            "energy_kwh": latest.get("energy_kwh", 0.0),

            "edge_state": self.edge_state,
            "active_alarm": self.active_alarm,
            "manual_mode": self.manual_mode,
            "operator_emergency_stop": self.operator_emergency_stop,
            "edge_hard_trip": self.edge_hard_trip,
            "hard_trip_reason": self.hard_trip_reason,
            "recovery_temperature_c": TEMP_RECOVERY_C,
            "last_auto_recovery_time": round(self.last_auto_recovery_time, 1),

            "alarm_motor_overload": alarm_motor_overload,
            "alarm_cooling_failed": alarm_cooling_failed,
            "alarm_critical_temperature": alarm_critical_temp,
            "alarm_hard_trip": alarm_hard_trip,
            "data_stale": False,

            "thermal_risk_index": round(thermal_risk_index, 1),
            "health_score": round(estimated_remaining_life_percent, 1),
            "traffic_saving_percent": round(traffic_saving, 1),
            "report_mode": report_mode,
            "aggregation_window_sec": report_window_seconds,
            "aggregation_window_samples": len(report_samples),

            "raw_samples_received": self.raw_samples_received,
            "cloud_messages_sent": self.cloud_messages_sent,
            "local_commands_sent": self.local_commands_sent,

            "sim_fault_mode": latest.get("fault_mode", "none"),
            "overload_level": latest.get("overload_level", 0),
            "motor_running": latest.get("running", False),
            "sim_emergency_stop": latest.get("emergency_stop", False),
        }

    def build_stale_data_alarm(self) -> dict:
        return {
            "edge_state": "DATA_STALE",
            "active_alarm": "sensor_data_timeout",
            "data_stale": True,
            "alarm_sensor_timeout": True,
            "seconds_since_last_sample": round(time.time() - self.last_raw_arrival_time, 1),
        }

    def set_manual_mode(self, enabled: bool) -> None:
        self.manual_mode = enabled

    def set_manual_fan_rpm(self, rpm: float) -> None:
        self.manual_fan_rpm = clamp(rpm, MIN_FAN_RPM, MAX_FAN_RPM)

    def emergency_stop(self, enabled: bool) -> None:
        self.operator_emergency_stop = enabled
        if enabled:
            self.edge_state = "OPERATOR_EMERGENCY_STOP"
            self.active_alarm = "operator_emergency_stop"

    def reset_emergency(self) -> None:
        self.operator_emergency_stop = False
        self.edge_hard_trip = False
        self.hard_trip_reason = "none"
        self.active_alarm = "none"
        self.edge_state = "RESET_BY_OPERATOR"
        self.cooling_failure_latched = False
        self.cooling_failure_confirm_count = 0
        self.cooling_failure_clear_count = 0


class EdgeGatewayApp:
    def __init__(self) -> None:
        self.controller = EdgeController()

        self.local_client = make_mqtt_client(f"edge-local-{DEVICE_ID}-{random.randint(1000, 9999)}")
        self.local_client.on_connect = self.on_local_connect
        self.local_client.on_message = self.on_local_message

        self.tb_client = make_mqtt_client(f"edge-tb-{DEVICE_ID}-{random.randint(1000, 9999)}")
        self.tb_client.username_pw_set(TB_ACCESS_TOKEN)
        self.tb_client.on_connect = self.on_tb_connect
        self.tb_client.on_message = self.on_tb_message

    def on_local_connect(self, client, userdata, flags, rc, *extra):
        print(f"[EDGE] Connected to local MQTT broker with result code: {rc}")
        client.subscribe(RAW_TOPIC, qos=1)
        print(f"[EDGE] Subscribed to raw telemetry topic: {RAW_TOPIC}")

    def on_local_message(self, client, userdata, msg):
        try:
            sample = json.loads(msg.payload.decode("utf-8"))
        except json.JSONDecodeError:
            print("[EDGE] Invalid raw JSON received")
            return

        decision = self.controller.process_raw_sample(sample)
        self.publish_local_command(decision)

        if self.controller.should_report_to_cloud():
            telemetry = self.controller.build_telemetry_for_cloud()
            self.publish_tb_telemetry(telemetry)
            self.controller.mark_cloud_sent()

    def publish_local_command(self, command: dict) -> None:
        self.local_client.publish(COMMAND_TOPIC, json.dumps(command), qos=1)
        self.controller.local_commands_sent += 1

    def on_tb_connect(self, client, userdata, flags, rc, *extra):
        print(f"[EDGE] Connected to ThingsBoard with result code: {rc}")
        client.subscribe(TB_RPC_REQUEST_TOPIC, qos=1)
        print(f"[EDGE] Subscribed to ThingsBoard RPC topic: {TB_RPC_REQUEST_TOPIC}")

        attributes = {
            "device_id": DEVICE_ID,
            "gateway_type": "Python Edge Gateway",
            "firmware_version": "1.6.0-stable-manual-rpm",
            "project": "Industrial IoT Motor Edge Control",
            "warning_temperature_c": TEMP_WARNING_C,
            "critical_temperature_c": TEMP_CRITICAL_C,
            "hard_trip_temperature_c": TEMP_HARD_TRIP_C,
            "recovery_temperature_c": TEMP_RECOVERY_C,
            "max_fan_rpm": MAX_FAN_RPM,
            "normal_report_interval_sec": NORMAL_REPORT_INTERVAL_SEC,
            "critical_report_interval_sec": CRITICAL_REPORT_INTERVAL_SEC,
            "local_mqtt": f"{LOCAL_MQTT_HOST}:{LOCAL_MQTT_PORT}",
            "thingsboard_mqtt": f"{TB_HOST}:{TB_PORT}",
        }
        client.publish(TB_ATTRIBUTES_TOPIC, json.dumps(attributes), qos=1)

    def on_tb_message(self, client, userdata, msg):
        topic = msg.topic
        try:
            payload = json.loads(msg.payload.decode("utf-8"))
        except json.JSONDecodeError:
            print("[EDGE] Invalid ThingsBoard message")
            return

        if topic.startswith("v1/devices/me/rpc/request/"):
            request_id = topic.split("/")[-1]
            response = self.handle_rpc(payload)
            response_topic = f"{TB_RPC_RESPONSE_TOPIC_PREFIX}/{request_id}"
            self.tb_client.publish(response_topic, json.dumps(response), qos=1)

    def handle_rpc(self, payload: dict) -> dict:
        method = payload.get("method")
        params = payload.get("params")
        print(f"[EDGE] RPC received: method={method}, params={params}")

        try:
            if method == "getState":
                return {
                    "ok": True,
                    "state": self.controller.build_telemetry_for_cloud(),
                }

            if method == "setManualMode":
                enabled = bool(params)
                self.controller.set_manual_mode(enabled)
                return {
                    "ok": True,
                    "manual_mode": self.controller.manual_mode,
                }

            # if method in ("setFanRpm", "setValue"):
            #     rpm = self.extract_number(params, default=BASE_FAN_RPM)
            #     self.controller.set_manual_fan_rpm(rpm)
            #     self.controller.set_manual_mode(True)
            #     self.publish_local_command({
            #         "cmd": "set_fan_rpm",
            #         "rpm": self.controller.manual_fan_rpm,
            #     })
            #     return {
            #         "ok": True,
            #         "manual_mode": True,
            #         "fan_rpm": self.controller.manual_fan_rpm,
            #     }

            if method in ("setFanRpm", "setValue"):
                # Manual mode must be enabled explicitly by the dashboard switch.
                if not self.controller.manual_mode:
                    return {
                        "ok": False,
                        "error": "manual_mode_required",
                        "message": "Enable manual mode before changing fan RPM.",
                        "manual_mode": False,
                        "fan_rpm": self.controller.manual_fan_rpm,
                    }

                rpm = self.extract_number(
                    params,
                    default=self.controller.manual_fan_rpm,
                )

                self.controller.set_manual_fan_rpm(rpm)

                self.publish_local_command({
                    "cmd": "set_fan_rpm",
                    "rpm": self.controller.manual_fan_rpm,
                })

                return {
                    "ok": True,
                    "manual_mode": True,
                    "fan_rpm": self.controller.manual_fan_rpm,
                }

            if method == "getValue":
                # Backward-compatible numeric response for generic RPC controls.
                return round(self.controller.manual_fan_rpm, 0)

            if method == "getManualFanRpm":
                # Return the commanded manual setpoint, not the measured fan RPM.
                # The measured RPM may ramp for a few seconds because the simulator
                # models fan inertia. Returning the setpoint keeps the dashboard
                # control stable instead of making its handle chase telemetry.
                return {
                    "ok": True,
                    "fan_rpm": round(self.controller.manual_fan_rpm, 0),
                    "manual_mode": self.controller.manual_mode,
                }

            if method == "setLoadPercent":
                load_percent = self.extract_number(params, default=55.0)
                load_percent = clamp(load_percent, 0.0, 100.0)
                self.publish_local_command({
                    "cmd": "set_load",
                    "load_percent": load_percent,
                })
                return {
                    "ok": True,
                    "load_percent": load_percent,
                }

            if method == "emergencyStop":
                enabled = True if params is None else bool(params)
                self.controller.emergency_stop(enabled)
                self.publish_local_command({
                    "cmd": "emergency_stop",
                    "enabled": enabled,
                })
                return {
                    "ok": True,
                    "operator_emergency_stop": enabled,
                }

            if method == "stopMotor":
                self.publish_local_command({
                    "cmd": "set_running",
                    "running": False,
                })
                return {
                    "ok": True,
                    "motor_running": False,
                }

            if method in ("startMotor", "resetEmergency"):
                self.controller.reset_emergency()
                self.publish_local_command({"cmd": "reset_emergency"})
                return {
                    "ok": True,
                    "motor_running": True,
                    "emergency_reset": True,
                }

            if method == "injectFault":
                fault = self.extract_fault(params)
                level = 1
                if isinstance(params, dict):
                    level = int(params.get("level", 1))
                level = max(1, min(level, 3))
                command = {
                    "cmd": "inject_fault",
                    "fault": fault,
                }
                if fault == "overload":
                    command["level"] = level
                self.publish_local_command(command)
                return {
                    "ok": True,
                    "fault": fault,
                    "level": level if fault == "overload" else 0,
                }

            if method == "clearFault":
                self.controller.reset_emergency()
                self.publish_local_command({"cmd": "clear_fault"})
                return {
                    "ok": True,
                    "fault": "none",
                }

            return {
                "ok": False,
                "error": f"unknown RPC method: {method}",
            }

        except Exception as exc:
            return {
                "ok": False,
                "error": str(exc),
            }

    @staticmethod
    def extract_number(params: Any, default: float) -> float:
        if params is None:
            return default
        if isinstance(params, (int, float)):
            return float(params)
        if isinstance(params, str):
            try:
                return float(params)
            except ValueError:
                return default
        if isinstance(params, dict):
            for key in ["value", "rpm", "fan_rpm", "load", "load_percent"]:
                if key in params:
                    return float(params[key])
        return default

    @staticmethod
    def extract_fault(params: Any) -> str:
        if isinstance(params, str):
            fault = params.lower()
        elif isinstance(params, dict):
            fault = str(params.get("fault", "none")).lower()
        else:
            fault = "none"

        if fault not in ["none", "overload", "fan_failure"]:
            raise ValueError("fault must be one of: none, overload, fan_failure")
        return fault

    def publish_tb_telemetry(self, values: Dict[str, Any]) -> None:
        payload = {
            "ts": now_ms(),
            "values": values,
        }
        self.tb_client.publish(TB_TELEMETRY_TOPIC, json.dumps(payload), qos=1)
        print(
            f"[EDGE->TB] mode={values.get('report_mode', 'n/a')} | "
            f"state={values.get('edge_state')} | "
            f"T={values.get('temperature_latest', values.get('temperature'))} | "
            f"Load={values.get('load_latest')} | "
            f"OL={values.get('overload_level')} | "
            f"alarm={values.get('active_alarm')} | "
            f"window={values.get('aggregation_window_samples', 0)} samples"
        )

    def run(self) -> None:
        if not TB_ACCESS_TOKEN or TB_ACCESS_TOKEN == "PUT_YOUR_DEVICE_ACCESS_TOKEN_HERE":
            print("\n[EDGE] ERROR: Please set TB_ACCESS_TOKEN in .env or environment.")
            print("[EDGE] Create a device in ThingsBoard and copy its access token.\n")
            return

        self.local_client.connect(LOCAL_MQTT_HOST, LOCAL_MQTT_PORT, keepalive=60)
        self.tb_client.connect(TB_HOST, TB_PORT, keepalive=60)

        self.local_client.loop_start()
        self.tb_client.loop_start()

        print("[EDGE] Edge gateway started.")
        print("[EDGE] Waiting for simulator data...")

        try:
            while True:
                time.sleep(1.0)
                if self.controller.last_raw_arrival_time == 0:
                    continue
                seconds_since_last = time.time() - self.controller.last_raw_arrival_time
                if seconds_since_last > EDGE_STALE_DATA_TIMEOUT_SEC:
                    if time.time() - self.controller.last_stale_alarm_time >= CRITICAL_REPORT_INTERVAL_SEC:
                        alarm = self.controller.build_stale_data_alarm()
                        self.publish_tb_telemetry(alarm)
                        self.controller.mark_cloud_sent()
                        self.controller.last_stale_alarm_time = time.time()
        except KeyboardInterrupt:
            print("\n[EDGE] Shutting down...")
        finally:
            self.local_client.loop_stop()
            self.tb_client.loop_stop()
            self.local_client.disconnect()
            self.tb_client.disconnect()


if __name__ == "__main__":
    app = EdgeGatewayApp()
    app.run()
