---
name: new-pi
description: Provision a freshly-imaged Raspberry Pi 5 as a new fleet member (pi-SW2, pi-SW3, ...) — hostname, AP profile, services, logind
disable-model-invocation: true
allowed-tools: Bash(ssh *) Bash(scp *) Bash(rsync *)
argument-hint: [new-hostname] [current-ip]
---

Provision a new Raspberry Pi 5 as a fleet member. One-shot per Pi — for ongoing code updates use the `deploy` skill.

Arguments: `$ARGUMENTS` — expect `<new-hostname> <current-ip>`, e.g. `pi-SW3 192.168.1.42`.

- `<new-hostname>` must match pattern `pi-SWn`. `fleet.my_ap_ssid()` derives the SSID by substituting `-`→`_`, so `pi-SW3` → `pi_SW3`.
- `<current-ip>` is the Pi's current reachable IP (on your local WiFi or Ethernet) before it flips to AP mode.

If arguments are missing, prompt the operator.

## Prerequisites

- Pi is a Raspberry Pi **5** imaged with Raspberry Pi OS Bookworm (NetworkManager-based), SSH enabled, default user `pi`.
- Pi is currently reachable at `<current-ip>`.
- Operator has the fleet AP password. To grab it from pi-SW1:
  ```
  ssh pi@10.42.0.1 "sudo nmcli -s -g 802-11-wireless-security.psk connection show pi_SW1"
  ```
- Repo `https://github.com/PMantix/Switch_Charging_V2.git` is accessible (currently HTTPS-cloneable; if private, have a PAT / SSH key ready).

## Steps

Compute `<new-ssid>` = `<new-hostname>` with `-` replaced by `_` (e.g. `pi-SW3` → `pi_SW3`).

1. **Sanity-check the Pi** — model, OS, and user:
   ```
   ssh pi@<current-ip> "cat /proc/device-tree/model 2>/dev/null; echo; hostnamectl | head -5; id"
   ```
   Abort if the model is not "Raspberry Pi 5" or the OS is not Bookworm — the power-button evdev path and NetworkManager AP-mode behavior are Pi-5-and-Bookworm-specific.

2. **Set the hostname**:
   ```
   ssh pi@<current-ip> "sudo hostnamectl set-hostname <new-hostname>"
   ```
   Takes effect for the fleet code after reboot (step 10). Does not drop the SSH session.

3. **Create the NetworkManager AP profile** (SSID and profile name both = `<new-ssid>`):
   ```
   ssh pi@<current-ip> "sudo nmcli connection add \
     type wifi ifname wlan0 con-name <new-ssid> ssid <new-ssid> \
     mode ap ipv4.method shared \
     wifi-sec.key-mgmt wpa-psk wifi-sec.psk '<AP_PASSWORD>' \
     connection.autoconnect no"
   ```
   `autoconnect=no` is deliberate — `server/ap_fallback.py` explicitly activates the profile at boot, and `server/power_button.py` toggles it on double-press. NM auto-activation would race with that logic.

4. **Add `pi` to `netdev`** so `nmcli connection up/down` works without sudo (polkit rule on Debian):
   ```
   ssh pi@<current-ip> "sudo gpasswd -a pi netdev"
   ```

5. **Install system dependencies**:
   ```
   ssh pi@<current-ip> "sudo apt update && sudo apt install -y \
     python3-gpiozero python3-evdev python3-serial git i2c-tools"
   ssh pi@<current-ip> "pip3 install --break-system-packages tm1637"
   ```
   `tm1637` is not in apt; `--break-system-packages` is required under PEP 668 on Bookworm.

6. **Clone the repo** to the path the systemd unit expects (`/home/pi/Code/Switch_Charging_V2`):
   ```
   ssh pi@<current-ip> "mkdir -p /home/pi/Code && \
     git clone https://github.com/PMantix/Switch_Charging_V2.git /home/pi/Code/Switch_Charging_V2"
   ```

7. **Install systemd units and the logind drop-in** from the repo's `server/` directory:
   ```
   ssh pi@<current-ip> "sudo cp /home/pi/Code/Switch_Charging_V2/server/ap-fallback.service /etc/systemd/system/ && \
                        sudo cp /home/pi/Code/Switch_Charging_V2/server/power-button.service /etc/systemd/system/ && \
                        sudo cp /home/pi/Code/Switch_Charging_V2/server/switching-circuit.service /etc/systemd/system/ && \
                        sudo mkdir -p /etc/systemd/logind.conf.d/ && \
                        sudo cp /home/pi/Code/Switch_Charging_V2/server/50-switch-charging-no-power-key.conf /etc/systemd/logind.conf.d/"
   ```

8. **Reload systemd and enable the services**:
   ```
   ssh pi@<current-ip> "sudo systemctl daemon-reload && \
                        sudo systemctl enable ap-fallback.service power-button.service switching-circuit.service && \
                        sudo systemctl restart systemd-logind"
   ```
   Don't `start` here — wait for the reboot so hostname, logind, and AP boot-activation all come up in the right order.

9. **Confirm with the operator before rebooting.** The reboot drops the current SSH session; the Pi comes back up as an AP (SSID `<new-ssid>`) at `10.42.0.1`, and the operator's laptop will need to join that SSID to reconnect.

10. **Reboot**:
    ```
    ssh pi@<current-ip> "sudo reboot"
    ```

11. **Reconnect and verify** — operator joins SSID `<new-ssid>` on laptop, then:
    ```
    echo 10.42.0.1 > ~/.switching-circuit-host
    ssh-keygen -R 10.42.0.1   # clear stale host key from prior Pi on the same IP
    ssh pi@10.42.0.1 "hostnamectl | head -3; \
      systemctl is-active ap-fallback.service power-button.service switching-circuit.service; \
      nmcli -t -f NAME,DEVICE connection show --active | grep wlan0"
    ```
    Expected:
    - hostname = `<new-hostname>`
    - `ap-fallback.service` = `inactive` (oneshot, already ran), the other two = `active`
    - active wlan0 profile = `<new-ssid>`

## Notes

- **Every new Pi sits at the same IP (10.42.0.1) when in AP mode.** The `ssh-keygen -R` in step 11 is essential or you'll hit a host-key mismatch after provisioning Pi N+1.
- **The operator's laptop can only be on one Pi's AP at a time.** After joining `<new-ssid>`, the previous Pi is unreachable until you switch SSIDs back.
- **Boot behavior once provisioned:** Pi boots → NetworkManager comes up → `ap-fallback.service` immediately activates the AP profile (no 30s wait) → `switching-circuit.service` starts the server on port 5555 → `power-button.service` listens for double-press to toggle AP ↔ client.
- **For ongoing code updates** use the `deploy` skill, not this one.
