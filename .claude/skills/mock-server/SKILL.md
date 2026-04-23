---
name: mock-server
description: Start the mock server locally for TUI testing without hardware
disable-model-invocation: true
allowed-tools: Bash(python *)
---

Start the switching circuit server in mock mode for local TUI development/testing.

## Start Server
```
cd /Users/phillipaquino/Code/SwitchingCircuitV2
python3 -m server
```
The server runs in mock mode when no RP2040 is connected (auto-detects).
It listens on port 5555 with zero sensor readings.

## Connect TUI
In a separate terminal:
```
python3 -m tui --host 127.0.0.1
```

## Mock Mode Behavior
- Sensor data is all zeros (REST state detected)
- All commands work (mode changes, sequence selection, frequency)
- Auto mode works but all non-REST steps will timeout (no real cycler current)
- Use `on_timeout: "advance"` in schedules for mock testing
- Press `n` to skip steps manually

## Quick Verification
```python
import json, socket
s = socket.socket(); s.connect(('127.0.0.1', 5555))
s.sendall(b'{"cmd":"get_status"}\n')
print(s.recv(4096).decode())
```
