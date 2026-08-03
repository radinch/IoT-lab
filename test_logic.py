import random
import sys
import types

# Test without a real MQTT dependency.
paho = types.ModuleType('paho')
mqtt_pkg = types.ModuleType('paho.mqtt')
mqtt_client = types.ModuleType('paho.mqtt.client')


class DummyClient:
    def __init__(self, *args, **kwargs):
        pass


class DummyCallbackAPIVersion:
    VERSION2 = object()


mqtt_client.Client = DummyClient
mqtt_client.CallbackAPIVersion = DummyCallbackAPIVersion
sys.modules['paho'] = paho
sys.modules['paho.mqtt'] = mqtt_pkg
sys.modules['paho.mqtt.client'] = mqtt_client

from motor_simulator import MotorPlant
from edge_gateway import EdgeController
from config import TEMP_WARNING_C, TEMP_CRITICAL_C, TEMP_HARD_TRIP_C, TEMP_RECOVERY_C


def run_overload_threshold_test():
    random.seed(42)
    plant = MotorPlant()
    edge = EdgeController()
    plant.fault_mode = 'overload'
    plant.overload_level = 3

    states = []
    violations = []
    last = None
    hard_trip_reached = False

    for i in range(400):
        plant.update(1.0)
        sample = plant.telemetry()
        decision = edge.process_raw_sample(sample)
        plant.apply_command(decision)
        st = edge.edge_state
        if st != last:
            states.append((i + 1, sample['temperature_c'], sample['load_percent'], sample['fan_rpm'], st, edge.active_alarm))
            last = st

        if sample['temperature_c'] < TEMP_WARNING_C and st != 'NORMAL':
            violations.append((i + 1, 'below70_not_normal', sample['temperature_c'], st))
            break
        if TEMP_WARNING_C <= sample['temperature_c'] < TEMP_CRITICAL_C and st != 'EDGE_COOLING':
            violations.append((i + 1, '70to85_not_edge_cooling', sample['temperature_c'], st))
            break
        if TEMP_CRITICAL_C <= sample['temperature_c'] < TEMP_HARD_TRIP_C and st not in ('MOTOR_OVERLOAD', 'CRITICAL_TEMPERATURE'):
            violations.append((i + 1, '85to95_not_alert', sample['temperature_c'], st))
            break
        if sample['temperature_c'] >= TEMP_HARD_TRIP_C and st != 'EDGE_HARD_TRIP':
            violations.append((i + 1, 'above95_not_hardtrip', sample['temperature_c'], st))
            break
        if st == 'EDGE_HARD_TRIP':
            hard_trip_reached = True
            break

    recovered = False
    for j in range(500):
        plant.update(1.0)
        sample = plant.telemetry()
        decision = edge.process_raw_sample(sample)
        plant.apply_command(decision)
        st = edge.edge_state
        if st != last:
            states.append((i + j + 2, sample['temperature_c'], sample['load_percent'], sample['fan_rpm'], st, edge.active_alarm))
            last = st
        if st in ('AUTO_RECOVERED', 'NORMAL') and sample['temperature_c'] <= TEMP_RECOVERY_C and plant.fault_mode == 'none' and plant.overload_level == 0:
            recovered = True
            break

    print('=== overload threshold test ===')
    print('state transitions:')
    for row in states:
        print(row)
    print('violations:', violations)
    print('hard_trip_reached:', hard_trip_reached)
    print('recovered:', recovered)

    assert not violations
    assert hard_trip_reached
    assert recovered


def run_aggregation_window_test():
    edge = EdgeController()
    temps = [10, 20, 30, 40, 50, 60, 70]
    for idx, temp in enumerate(temps):
        sample = {
            'ts': idx * 1000,
            'temperature_c': temp,
            'load_percent': 50,
            'fan_rpm': 800,
            'current_a': 10,
            'vibration_mm_s': 1.0,
            'power_kw': 5.0,
            'energy_kwh': 1.0,
            'health_score': 100.0,
            'fault_mode': 'none',
            'overload_level': 0,
            'running': True,
            'emergency_stop': False,
        }
        edge.process_raw_sample(sample)

    telemetry = edge.build_telemetry_for_cloud()
    # Latest sample at t=6s -> 5 second normal aggregation window contains t=1..6.
    expected_avg = sum([20, 30, 40, 50, 60, 70]) / 6.0
    print('=== aggregation window test ===')
    print('temperature average:', telemetry['temperature'])
    print('aggregation samples:', telemetry['aggregation_window_samples'])
    assert abs(telemetry['temperature'] - round(expected_avg, 2)) < 1e-6
    assert telemetry['aggregation_window_samples'] == 6
    assert telemetry['report_mode'] == 'average_5s'


def run_cooling_failure_stability_test():
    edge = EdgeController()
    print('=== cooling failure stability test ===')

    # Build enough normal history first.
    for i in range(5):
        sample = {
            'ts': i * 1000,
            'temperature_c': 50 + i * 0.01,
            'load_percent': 55,
            'fan_rpm': 800,
            'current_a': 10,
            'vibration_mm_s': 1.0,
            'power_kw': 5.0,
            'energy_kwh': 1.0,
            'health_score': 100.0,
            'fault_mode': 'none',
            'overload_level': 0,
            'running': True,
            'emergency_stop': False,
        }
        edge.process_raw_sample(sample)

    # One noisy sample should not latch failure.
    sample = {
        'ts': 5000,
        'temperature_c': 50.2,
        'load_percent': 55,
        'fan_rpm': 0,
        'current_a': 10,
        'vibration_mm_s': 1.0,
        'power_kw': 5.0,
        'energy_kwh': 1.0,
        'health_score': 100.0,
        'fault_mode': 'none',
        'overload_level': 0,
        'running': True,
        'emergency_stop': False,
    }
    edge.process_raw_sample(sample)
    print('latched after single bad sample:', edge.cooling_failure_latched)
    assert edge.cooling_failure_latched is False

    # Three consecutive bad samples should latch.
    for j in range(3):
        sample = {
            'ts': 6000 + j * 1000,
            'temperature_c': 50.4 + j * 0.25,
            'load_percent': 55,
            'fan_rpm': 0,
            'current_a': 10,
            'vibration_mm_s': 1.0,
            'power_kw': 5.0,
            'energy_kwh': 1.0,
            'health_score': 100.0,
            'fault_mode': 'none',
            'overload_level': 0,
            'running': True,
            'emergency_stop': False,
        }
        edge.process_raw_sample(sample)
    print('latched after repeated bad samples:', edge.cooling_failure_latched)
    assert edge.cooling_failure_latched is True

    # Recovery samples should clear the latch.
    for j in range(5):
        sample = {
            'ts': 9000 + j * 1000,
            'temperature_c': 50.8 - j * 0.2,
            'load_percent': 55,
            'fan_rpm': 800,
            'current_a': 10,
            'vibration_mm_s': 1.0,
            'power_kw': 5.0,
            'energy_kwh': 1.0,
            'health_score': 100.0,
            'fault_mode': 'none',
            'overload_level': 0,
            'running': True,
            'emergency_stop': False,
        }
        edge.process_raw_sample(sample)
    print('latched after recovery samples:', edge.cooling_failure_latched)
    assert edge.cooling_failure_latched is False


if __name__ == '__main__':
    run_overload_threshold_test()
    run_aggregation_window_test()
    run_cooling_failure_stability_test()
    print('All tests passed.')
