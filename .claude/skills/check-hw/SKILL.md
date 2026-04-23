---
name: check-hw
description: Check hardware status — RP2040 connection, INA226 sensors, FET states, and server health
disable-model-invocation: true
allowed-tools: Bash(python *) Bash(ssh *)
argument-hint: [pi-hostname]
---

Check the full hardware stack status. Connects to the Pi server and queries every subsystem.

Target host: $ARGUMENTS (default: read from `~/.switching-circuit-host` or `127.0.0.1`)

## Steps

1. **Resolve host**:
   ```python
   import pathlib
   try: host = pathlib.Path.home().joinpath('.switching-circuit-host').read_text().strip()
   except: host = '127.0.0.1'
   ```
   Override with argument if provided.

2. **Server health** — connect and get full status:
   ```python
   import json, socket
   s = socket.socket()
   s.settimeout(5)
   s.connect((host, 5555))
   s.sendall(b'{"cmd":"get_status"}\n')
   status = json.loads(s.recv(8192).decode().split('\n')[0])
   ```

3. **Report server state**:
   - Mode: idle/charge/discharge/auto
   - Frequency and sequence
   - FET states (P1/P2/N1/N2 on/off)

4. **Report sensor readings** (all 4 INA226 channels):
   - P1 (+A high-side): voltage, current
   - P2 (+B high-side): voltage, current
   - N1 (-A low-side): voltage, current
   - N2 (-B low-side): voltage, current
   - Flag if all zeros (mock mode / RP2040 not connected)
   - Flag if any sensor shows error

5. **Check RP2040 connection** via SSH to Pi:
   ```
   ssh pi@<host> "ls -la /dev/ttyACM* 2>/dev/null || echo 'No RP2040 serial device'"
   ```

6. **Check INA226 I2C bus** via SSH (if accessible):
   ```
   ssh pi@<host> "i2cdetect -y 1 2>/dev/null | grep -E '40|41|43|45' || echo 'No I2C scan available'"
   ```
   Expected addresses: 0x40 (P1), 0x41 (P2), 0x43 (N1), 0x45 (N2)

7. **Check if auto mode is running**:
   ```python
   s.sendall(b'{"cmd":"auto_status"}\n')
   auto = json.loads(s.recv(8192).decode().split('\n')[0])
   ```
   Report: schedule name, current step, cycle, detected state, match status

8. **Check Pi system health** via SSH:
   ```
   ssh pi@<host> "uptime; free -h | head -2; df -h / | tail -1; vcgencmd measure_temp 2>/dev/null"
   ```

## Summary Table
Report a table:
| Component | Status | Details |
|-----------|--------|---------|
| Pi network | OK/FAIL | IP, latency |
| Server | OK/FAIL | mode, port 5555 |
| RP2040 | OK/MOCK | serial port |
| INA226 P1 (0x40) | OK/MISSING | voltage, current |
| INA226 P2 (0x41) | OK/MISSING | voltage, current |
| INA226 N1 (0x43) | OK/MISSING | voltage, current |
| INA226 N2 (0x45) | OK/MISSING | voltage, current |
| Auto mode | RUNNING/STOPPED | schedule, step |
