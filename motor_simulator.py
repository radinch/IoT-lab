import json
import math
import random
import threading
import time
from dataclasses import dataclass

import paho.mqtt.client as mqtt

from config import (
    LOCAL_MQTT_HOST,
    LOCAL_MQTT_PORT,
    RAW_TOPIC,
    COMMAND_TOPIC,
    DEVICE_ID,
    SIMULATION_STEP_SEC,
    AMBIENT_TEMP_C,
    BASE_FAN_RPM,
    MAX_FAN_RPM,
    MIN_FAN_RPM,
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


@dataclass
class MotorPlant:
    device_id: str = DEVICE_ID

    temperature_c: float = 34.0
    ambient_c: float = AMBIENT_TEMP_C

    load_setpoint_percent: float = 55.0
    load_percent: float = 50.0

    fan_rpm_setpoint: float = BASE_FAN_RPM
    fan_rpm_actual: float = BASE_FAN_RPM

    running: bool = True
    emergency_stop: bool = False

    # fault_mode: none, overload, fan_failure
    fault_mode: str = "none"
    overload_level: int = 0

    current_a: float = 0.0
    voltage_v: float = 400.0
    power_kw: float = 0.0
    energy_kwh: float = 0.0
    vibration_mm_s: float = 1.5

    total_runtime_s: float = 0.0
    sequence: int = 0
    health_score: float = 100.0

    def update(self, dt: float) -> None:
        self.sequence += 1

        if self.running and not self.emergency_stop:
            self.total_runtime_s += dt

        # -------------------------------------------------------------
        # Load model
        # -------------------------------------------------------------
        if not self.running or self.emergency_stop:
            target_load = 0.0

        elif self.fault_mode == "overload":
            # Pressing "o" multiple times increases overload level.
            # Level 1: heavy load
            # Level 2: dangerous overload
            # Level 3: extreme industrial overload
            severity = max(1, min(self.overload_level, 3))

            if severity == 1:
                target_load = random.uniform(98.0, 108.0)
            elif severity == 2:
                target_load = random.uniform(110.0, 122.0)
            else:
                target_load = random.uniform(122.0, 135.0)

        else:
            target_load = self.load_setpoint_percent + random.uniform(-4.0, 4.0)

        max_load_change_per_sec = 14.0
        delta_load = clamp(
            target_load - self.load_percent,
            -max_load_change_per_sec * dt,
            max_load_change_per_sec * dt,
        )

        # Allow simulated overload above 100%.
        self.load_percent = clamp(self.load_percent + delta_load, 0.0, 135.0)

        # -------------------------------------------------------------
        # Fan dynamics
        # -------------------------------------------------------------
        if self.fault_mode == "fan_failure":
            target_fan_rpm = 0.0
        else:
            target_fan_rpm = self.fan_rpm_setpoint

        fan_ramp_rate = 750.0
        fan_delta = clamp(
            target_fan_rpm - self.fan_rpm_actual,
            -fan_ramp_rate * dt,
            fan_ramp_rate * dt,
        )
        self.fan_rpm_actual = clamp(
            self.fan_rpm_actual + fan_delta,
            MIN_FAN_RPM,
            MAX_FAN_RPM,
        )

        # -------------------------------------------------------------
        # Thermal model
        # Tnew = Told + delta_T_load - delta_T_cooling
        # -------------------------------------------------------------
        if self.running and not self.emergency_stop:
            load_factor = self.load_percent / 100.0

            # Base motor heating.
            delta_t_load = (0.06 + 0.55 * (load_factor ** 2)) * dt

            # Extra heat caused by real overload.
            # This prevents the motor from stabilizing around 75 C.
            if self.fault_mode == "overload":
                severity = max(1, min(self.overload_level, 3))
                overload_extra_heat = {
                    1: 0.22,
                    2: 0.45,
                    3: 0.75,
                }[severity] * dt
                delta_t_load += overload_extra_heat
        else:
            delta_t_load = 0.0

        # Cooling model.
        # Fan cooling is strong, but not enough to survive a real overload forever.
        fan_cooling = (self.fan_rpm_actual / MAX_FAN_RPM) * 0.38
        passive_cooling = max(self.temperature_c - self.ambient_c, 0.0) * 0.004
        delta_t_cooling = (fan_cooling + passive_cooling) * dt

        sensor_noise = random.uniform(-0.05, 0.05)

        self.temperature_c = self.temperature_c + delta_t_load - delta_t_cooling + sensor_noise
        self.temperature_c = clamp(self.temperature_c, self.ambient_c, 130.0)

        # -------------------------------------------------------------
        # Electrical model
        # -------------------------------------------------------------
        if self.running and not self.emergency_stop:
            temp_penalty = max(self.temperature_c - 65.0, 0.0) * 0.08
            self.current_a = (
                3.5
                + 0.19 * self.load_percent
                + temp_penalty
                + random.uniform(-0.25, 0.25)
            )
        else:
            self.current_a = random.uniform(0.2, 0.6)

        power_factor = 0.82
        self.power_kw = math.sqrt(3) * self.voltage_v * self.current_a * power_factor / 1000.0
        self.energy_kwh += self.power_kw * dt / 3600.0

        # -------------------------------------------------------------
        # Vibration model
        # -------------------------------------------------------------
        self.vibration_mm_s = (
            1.2
            + 2.0 * (self.load_percent / 100.0)
            + max(self.temperature_c - 70.0, 0.0) * 0.08
            + random.uniform(-0.12, 0.12)
        )

        if self.fault_mode == "overload":
            severity = max(1, min(self.overload_level, 3))
            self.vibration_mm_s += random.uniform(1.0, 2.0) * severity

        if self.fault_mode == "fan_failure":
            self.vibration_mm_s += random.uniform(0.4, 0.9)

        self.vibration_mm_s = clamp(self.vibration_mm_s, 0.0, 20.0)

        # -------------------------------------------------------------
        # Health score
        # -------------------------------------------------------------
        temp_damage = max(self.temperature_c - 65.0, 0.0) * 0.9
        vibration_damage = max(self.vibration_mm_s - 4.5, 0.0) * 6.0

        overload_damage = 0.0
        if self.fault_mode == "overload":
            severity = max(1, min(self.overload_level, 3))
            overload_damage = 10.0 * severity

        fan_damage = 15.0 if self.fault_mode == "fan_failure" else 0.0

        self.health_score = 100.0 - temp_damage - vibration_damage - overload_damage - fan_damage
        self.health_score = clamp(self.health_score, 0.0, 100.0)

    def telemetry(self) -> dict:
        return {
            "ts": now_ms(),
            "device_id": self.device_id,
            "sequence": self.sequence,

            "temperature_c": round(self.temperature_c, 2),
            "ambient_c": round(self.ambient_c, 2),

            "load_percent": round(self.load_percent, 2),
            "load_setpoint_percent": round(self.load_setpoint_percent, 2),

            "fan_rpm": round(self.fan_rpm_actual, 0),
            "fan_rpm_setpoint": round(self.fan_rpm_setpoint, 0),

            "current_a": round(self.current_a, 2),
            "voltage_v": round(self.voltage_v, 1),
            "power_kw": round(self.power_kw, 3),
            "energy_kwh": round(self.energy_kwh, 5),

            "vibration_mm_s": round(self.vibration_mm_s, 3),
            "health_score": round(self.health_score, 1),

            "running": self.running,
            "emergency_stop": self.emergency_stop,
            "fault_mode": self.fault_mode,
            "overload_level": self.overload_level,
            "source": "motor_simulator",
        }

    def apply_command(self, command: dict) -> str:
        cmd = command.get("cmd")

        if cmd == "control":
            if "fan_rpm" in command:
                rpm = float(command["fan_rpm"])
                self.fan_rpm_setpoint = clamp(rpm, MIN_FAN_RPM, MAX_FAN_RPM)

            if "running" in command:
                self.running = bool(command["running"])

            return "control command applied"

        if cmd == "set_fan_rpm":
            rpm = float(command.get("rpm", BASE_FAN_RPM))
            self.fan_rpm_setpoint = clamp(rpm, MIN_FAN_RPM, MAX_FAN_RPM)
            return f"fan setpoint changed to {self.fan_rpm_setpoint:.0f} RPM"

        if cmd == "set_load":
            load = float(command.get("load_percent", 55.0))
            self.load_setpoint_percent = clamp(load, 0.0, 100.0)
            return f"load setpoint changed to {self.load_setpoint_percent:.1f}%"

        if cmd == "set_running":
            self.running = bool(command.get("running", True))
            return f"running changed to {self.running}"

        if cmd == "emergency_stop":
            enabled = bool(command.get("enabled", True))
            self.emergency_stop = enabled
            if enabled:
                self.running = False
                self.fan_rpm_setpoint = MAX_FAN_RPM
            return f"emergency_stop changed to {self.emergency_stop}"

        if cmd == "reset_emergency":
            self.emergency_stop = False
            self.running = True
            self.fault_mode = "none"
            self.overload_level = 0
            self.load_setpoint_percent = 55.0
            self.fan_rpm_setpoint = BASE_FAN_RPM
            return "emergency reset complete"

        if cmd == "inject_fault":
            fault = str(command.get("fault", "none")).lower()

            if fault not in ["none", "overload", "fan_failure"]:
                return f"unknown fault: {fault}"

            self.fault_mode = fault

            if fault == "overload":
                level = int(command.get("level", 1))
                self.overload_level = max(1, min(level, 3))
            else:
                self.overload_level = 0

            return f"fault mode changed to {self.fault_mode}, overload_level={self.overload_level}"

        if cmd == "clear_fault":
            self.fault_mode = "none"
            self.overload_level = 0
            self.emergency_stop = False
            self.running = True
            self.load_setpoint_percent = 55.0
            return "fault cleared and motor requested to run"

        return f"unknown command: {cmd}"


class SimulatorApp:
    def __init__(self) -> None:
        self.plant = MotorPlant()
        self.lock = threading.Lock()
        self.running = True

        self.client = make_mqtt_client(f"simulator-{DEVICE_ID}-{random.randint(1000, 9999)}")
        self.client.on_connect = self.on_connect
        self.client.on_message = self.on_message

    def on_connect(self, client, userdata, flags, rc, *extra):
        print(f"[SIM] Connected to local MQTT broker with result code: {rc}")
        client.subscribe(COMMAND_TOPIC, qos=1)
        print(f"[SIM] Subscribed to command topic: {COMMAND_TOPIC}")

    def on_message(self, client, userdata, msg):
        try:
            payload = json.loads(msg.payload.decode("utf-8"))
        except json.JSONDecodeError:
            print("[SIM] Invalid JSON command received")
            return

        with self.lock:
            result = self.plant.apply_command(payload)

        # Only print non-repetitive or operator commands. The edge sends frequent control commands.
        if payload.get("cmd") != "control":
            print(f"[SIM] Command received: {payload} -> {result}")

    def keyboard_loop(self) -> None:
        print("\n[SIM] Keyboard controls:")
        print("  o + Enter : inject/increase motor overload level 1 -> 2 -> 3")
        print("  f + Enter : inject fan failure")
        print("  c + Enter : clear fault")
        print("  s + Enter : stop motor")
        print("  r + Enter : restart motor and reset faults")
        print("  q + Enter : quit\n")

        while self.running:
            try:
                key = input().strip().lower()
            except EOFError:
                break

            with self.lock:
                if key == "o":
                    self.plant.fault_mode = "overload"
                    self.plant.overload_level = min(self.plant.overload_level + 1, 3)
                    print(f"[SIM] Injecting MOTOR OVERLOAD fault | level={self.plant.overload_level}")

                elif key == "f":
                    print("[SIM] Injecting COOLING FAN FAILURE fault")
                    self.plant.fault_mode = "fan_failure"
                    self.plant.overload_level = 0

                elif key == "c":
                    print("[SIM] Clearing fault and requesting recovery")
                    self.plant.fault_mode = "none"
                    self.plant.overload_level = 0
                    self.plant.emergency_stop = False
                    self.plant.running = True
                    self.plant.load_setpoint_percent = 55.0

                elif key == "s":
                    print("[SIM] Stopping motor")
                    self.plant.running = False

                elif key == "r":
                    print("[SIM] Restarting motor and resetting faults")
                    self.plant.running = True
                    self.plant.emergency_stop = False
                    self.plant.fault_mode = "none"
                    self.plant.overload_level = 0
                    self.plant.load_setpoint_percent = 55.0
                    self.plant.fan_rpm_setpoint = BASE_FAN_RPM

                elif key == "q":
                    print("[SIM] Quitting simulator")
                    self.running = False

    def run(self) -> None:
        self.client.connect(LOCAL_MQTT_HOST, LOCAL_MQTT_PORT, keepalive=60)
        self.client.loop_start()

        keyboard_thread = threading.Thread(target=self.keyboard_loop, daemon=True)
        keyboard_thread.start()

        print(f"[SIM] Publishing raw telemetry to: {RAW_TOPIC}")

        while self.running:
            started = time.time()

            with self.lock:
                self.plant.update(SIMULATION_STEP_SEC)
                telemetry = self.plant.telemetry()

            self.client.publish(RAW_TOPIC, json.dumps(telemetry), qos=1)

            print(
                f"[SIM] T={telemetry['temperature_c']:6.2f} C | "
                f"Load={telemetry['load_percent']:6.1f}% | "
                f"Fan={telemetry['fan_rpm']:5.0f} RPM | "
                f"Fault={telemetry['fault_mode']} | "
                f"OL={telemetry['overload_level']}"
            )

            elapsed = time.time() - started
            time.sleep(max(0.0, SIMULATION_STEP_SEC - elapsed))

        self.client.loop_stop()
        self.client.disconnect()


if __name__ == "__main__":
    app = SimulatorApp()
    app.run()
