---
name: deploy
description: Deploy server code and firmware to the Raspberry Pi over rsync/SSH
disable-model-invocation: true
allowed-tools: Bash(rsync *) Bash(ssh *) Bash(python *)
argument-hint: [pi-hostname]
---

Deploy the switching circuit server and firmware to the Raspberry Pi.

Target host: $ARGUMENTS (default: discover via `~/.switching-circuit-host`)

## Steps

1. **Stop the running service** (important — must stop before firmware upload):
   ```
   ssh pi@<host> "sudo systemctl stop switching-circuit 2>/dev/null; pkill -f 'python.*-m server' 2>/dev/null; true"
   ```

2. **Upload server code**:
   ```
   rsync -av --exclude='__pycache__' --exclude='.DS_Store' server/ pi@<host>:/home/pi/SwitchingCircuitV2/server/
   ```

3. **Upload firmware** (if firmware/ changed):
   ```
   rsync -av --exclude='__pycache__' firmware/ pi@<host>:/home/pi/SwitchingCircuitV2/firmware/
   ```

4. **Upload schedules**:
   ```
   rsync -av schedules/ pi@<host>:/home/pi/SwitchingCircuitV2/schedules/
   ```

5. **Restart the server**:
   ```
   ssh pi@<host> "cd /home/pi/SwitchingCircuitV2 && nohup python3 -m server > /tmp/switching-circuit.log 2>&1 &"
   ```

6. **Verify** — wait 3 seconds then check the log:
   ```
   ssh pi@<host> "tail -5 /tmp/switching-circuit.log"
   ```
   Expected: "Server ready — listening on port 5555"

## Notes
- The RP2040 serial port auto-scans /dev/ttyACM0-3
- If firmware upload fails, the RP2040 may need a manual reset
- Check `~/.switching-circuit-host` for the cached Pi IP address
