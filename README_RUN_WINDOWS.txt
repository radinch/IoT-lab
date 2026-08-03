Industrial IoT Motor Control Simulation - V4 Exact Threshold Logic
=================================================================

This version follows the requested lab behavior:

1) The edge does NOT know that motor overload was injected as soon as you press o.
   The simulator can show Fault=overload for your debugging, but the edge does not
   use this flag to raise the overload alarm.

2) From startup until 70 C:
   edge_state = NORMAL
   fan command = 800 RPM
   alarm = none

3) At 70 C:
   edge_state = EDGE_COOLING
   alarm = none
   the edge locally increases fan RPM without waiting for ThingsBoard/cloud.

4) At 85 C:
   edge_state = MOTOR_OVERLOAD if load is high, otherwise CRITICAL_TEMPERATURE.
   active_alarm = motor_overload or critical_temperature.
   telemetry report interval changes from 5 seconds to 1 second.

5) At 95 C:
   edge_state = EDGE_HARD_TRIP
   active_alarm = thermal_hard_trip
   motor is stopped locally by the edge and fan stays at max RPM.

6) After hard trip:
   the edge keeps cooling until temperature is below TEMP_RECOVERY_C = 60 C.
   then it automatically sends reset_emergency to clear the simulator fault,
   restart the motor, and return fan setpoint to 800 RPM.

Run order on Windows
--------------------

1. Start ThingsBoard Docker:
   cd C:\thingsboard
   docker compose up -d

2. Start local Mosquitto broker, if not already running:
   docker rm -f mosquitto
   docker run -d --name mosquitto -p 1883:1883 eclipse-mosquitto:2 mosquitto -c /mosquitto-no-auth.conf

3. Install dependency:
   python -m pip install -r requirements.txt

4. Terminal 1:
   python edge_gateway.py

5. Terminal 2:
   python motor_simulator.py

Keyboard commands in simulator
------------------------------
o + Enter : inject/increase overload level 1 -> 2 -> 3
f + Enter : fan failure
c + Enter : clear fault
s + Enter : stop motor
r + Enter : restart and reset faults
q + Enter : quit

Recommended overload test
-------------------------
Press o three times.
Expected edge sequence:
NORMAL -> EDGE_COOLING -> MOTOR_OVERLOAD -> EDGE_HARD_TRIP -> AUTO_RECOVERED -> NORMAL

Recommended cooling failure test
--------------------------------
Press f.
Expected behavior:
Fan RPM goes to 0. If temperature is rising while fan RPM is 0,
edge_state becomes COOLING_SYSTEM_FAILED and active_alarm becomes cooling_system_failed.
