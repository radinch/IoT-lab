# Stable Manual Fan RPM Control — WSL / ThingsBoard 4.3

## What was wrong

The old dashboard used ThingsBoard's built-in generic slider. In this installation it rendered the physical track as 0–100 even though the displayed RPC value could be above 100. It also initialized itself from `fan_rpm_latest`, which is the measured fan speed. Because the simulator models fan inertia, measured RPM changes for several seconds after a command. The old control therefore moved its own handle while the fan was ramping and could send those intermediate values back as new commands.

## What changed

- Added a custom RPC widget: `manual_fan_rpm_control_widget.json`.
- The control range is explicitly 0–3200 RPM.
- The step is 100 RPM.
- An exact numeric input and Apply button are included.
- The widget sends an RPC only when the slider is released or Apply is pressed.
- It reads the commanded setpoint through `getManualFanRpm`, not measured RPM telemetry.
- `edge_gateway.py` now implements `getManualFanRpm`.
- Actual fan RPM may still ramp for a few seconds; the control setpoint itself stays fixed.

---

# Installation steps

## 1. Stop the two Python programs

In the simulator terminal, enter:

```text
q
```

In the edge-gateway terminal, press:

```text
Ctrl+C
```

You do not need to stop ThingsBoard or Mosquitto while replacing the Python file and dashboard widget.

## 2. Replace the project folder

Back up the current project:

```bash
mv ~/industrial-iot/industrial_iot_motor_project \
   ~/industrial-iot/industrial_iot_motor_project_before_slider_fix
```

Copy the new fixed folder from Windows Downloads. Example:

```bash
cp -r "/mnt/c/Users/YOUR_WINDOWS_USERNAME/Downloads/industrial_iot_motor_project_fixed" \
      ~/industrial-iot/industrial_iot_motor_project
```

Or extract the supplied ZIP directly:

```bash
cd ~/industrial-iot
unzip -o industrial_iot_motor_project_v10_slider_fixed.zip
mv industrial_iot_motor_project_fixed industrial_iot_motor_project
```

## 3. Restore your real ThingsBoard token

Open:

```bash
cd ~/industrial-iot/industrial_iot_motor_project
nano .env
```

Set:

```env
LOCAL_MQTT_HOST=localhost
LOCAL_MQTT_PORT=1883
TB_HOST=localhost
TB_PORT=1884
TB_ACCESS_TOKEN=YOUR_REAL_MOTOR_01_TOKEN
DEVICE_ID=motor-01
```

Save with Ctrl+O, Enter, Ctrl+X.

## 4. Update Python dependencies

```bash
cd ~/industrial-iot/industrial_iot_motor_project
python3 -m venv .venv
source .venv/bin/activate
python -m pip install --upgrade pip
python -m pip install -r requirements.txt
```

## 5. Run the logic tests

```bash
python test_logic.py
```

The last line should be:

```text
All tests passed.
```

---

# Import the custom widget into ThingsBoard

## 6. Copy the widget JSON to Windows Downloads

From WSL:

```bash
cp ~/industrial-iot/industrial_iot_motor_project/manual_fan_rpm_control_widget.json \
   "/mnt/c/Users/YOUR_WINDOWS_USERNAME/Downloads/"
```

## 7. Create a widget bundle

In ThingsBoard:

1. Open **Widget Library**.
2. Click **+ Add widget bundle**.
3. Create a bundle named:

```text
Industrial Motor Controls
```

4. Open the new bundle.
5. Click **+ Add widget type**.
6. Choose **Import widget type**.
7. Select:

```text
manual_fan_rpm_control_widget.json
```

8. Save.

The widget should appear as:

```text
Manual Fan RPM Control
```

---

# Replace the old dashboard slider

## 8. Remove the old slider

1. Open the motor dashboard.
2. Click the pencil icon to enter edit mode.
3. Select the old widget titled **MANUAL FAN RPM SLIDER**.
4. Delete it.

## 9. Add the new control

1. While still in dashboard edit mode, click **Add widget**.
2. Open the bundle **Industrial Motor Controls**.
3. Select **Manual Fan RPM Control**.
4. For the target device/entity alias, select:

```text
Motor
```

5. Open its Advanced/Settings section and confirm:

```text
Minimum RPM: 0
Maximum RPM: 3200
RPM step: 100
Initial RPM: 800
Get setpoint RPC method: getManualFanRpm
Set RPM RPC method: setFanRpm
RPC timeout: 5000
```

6. Add the widget.
7. Resize it as desired.
8. Save the dashboard.

---

# Restart and test

## 10. Make sure Docker services are running

```bash
sudo docker info
sudo docker start motor-mosquitto
cd ~/thingsboard
sudo docker-compose up -d
```

## 11. Start the edge gateway

Terminal 1:

```bash
cd ~/industrial-iot/industrial_iot_motor_project
source .venv/bin/activate
python edge_gateway.py
```

## 12. Start the simulator

Terminal 2:

```bash
cd ~/industrial-iot/industrial_iot_motor_project
source .venv/bin/activate
python motor_simulator.py
```

## 13. Test manual mode

1. Enable **Manual Mode** on the dashboard.
2. Move the new RPM slider to a value such as 1600 RPM and release it.
3. Or type `1600` into **Exact setpoint** and click **Apply**.
4. The control must remain at 1600 RPM.
5. The fan-speed gauge may move gradually toward 1600 RPM for a few seconds. That gauge shows measured RPM; the new control shows commanded RPM.
6. After reaching 1600 RPM, measured RPM should remain stable unless:
   - manual mode is turned off;
   - a safety condition overrides manual control;
   - fan failure is injected;
   - emergency stop or hard trip is active.

## 14. Verify the RPC in the edge terminal

When you apply 1600 RPM, you should see something similar to:

```text
[EDGE] RPC received: method=setFanRpm, params=1600
```

The simulator receives a `set_fan_rpm` command and then normal edge control keeps sending the same manual setpoint once per sample.

---

# Expected behavior

- Slider scale: 0 to 3200 RPM.
- Slider step: 100 RPM.
- Exact input: accepts values from 0 to 3200 and rounds to the nearest 100.
- The slider does not chase `fan_rpm_latest`.
- It does not send an RPC continuously while dragging.
- The commanded value stays fixed.
- Actual RPM ramps toward the command because the simulator includes fan dynamics.

# Safety overrides

Manual mode does not defeat safety logic. The edge gateway may override the selected setpoint and command maximum cooling during:

- cooling-system failure;
- motor overload above the critical threshold;
- critical temperature;
- thermal hard trip;
- operator emergency stop.
