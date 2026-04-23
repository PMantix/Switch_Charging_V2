---
name: scan
description: Scan the network for the Raspberry Pi and check if the switching circuit server is reachable
disable-model-invocation: true
allowed-tools: Bash(python *) Bash(ping *) Bash(nc *) Bash(cat *) Bash(arp *)
---

Scan for the Raspberry Pi and switching circuit server. Report what's found.

## Steps

1. **Check cached host** from last successful connection:
   ```
   cat ~/.switching-circuit-host 2>/dev/null
   ```

2. **Try mDNS resolution**:
   ```
   python3 -c "import socket; print(socket.gethostbyname('raspberrypi.local'))" 2>/dev/null
   ```

3. **Ping the Pi** (cached IP or mDNS result):
   ```
   ping -c 1 -W 2 <ip>
   ```

4. **Check if the server is listening on port 5555**:
   ```
   python3 -c "
   import socket, json
   s = socket.socket()
   s.settimeout(3)
   try:
       s.connect(('<ip>', 5555))
       s.sendall(b'{\"cmd\":\"get_status\"}\n')
       resp = json.loads(s.recv(4096).decode().split(chr(10))[0])
       print(f'Server responding: mode={resp[\"mode\"]}, sensors={\"sensors\" in resp}')
       sensors = resp.get('sensors', {})
       for name in ['P1','P2','N1','N2']:
           d = sensors.get(name, {})
           v = d.get('voltage', 0)
           i = d.get('current', 0)
           print(f'  {name}: {v:.4f}V  {i*1000:.3f}mA')
   except Exception as e:
       print(f'Server not responding: {e}')
   finally:
       s.close()
   "
   ```

5. **Check for USB serial devices** (RP2040):
   ```
   ls -la /dev/ttyACM* /dev/cu.usbmodem* 2>/dev/null || echo "No USB serial devices found"
   ```
   On Mac (if running locally):
   ```
   ls -la /dev/cu.usbmodem* /dev/tty.usbmodem* 2>/dev/null || echo "No USB serial devices found"
   ```

6. **Check ARP table** for Pi's MAC address (Raspberry Pi MACs start with b8:27:eb, dc:a6:32, or e4:5f:01):
   ```
   arp -a | grep -iE "b8:27:eb|dc:a6:32|e4:5f:01|raspberry"
   ```

7. **If nothing found**, scan the local subnet for port 5555:
   ```
   python3 -c "
   import socket, concurrent.futures
   def probe(ip):
       try:
           s = socket.create_connection((ip, 5555), timeout=0.5)
           s.close()
           return ip
       except: return None
   # Get local IP prefix
   import subprocess
   result = subprocess.run(['ipconfig', 'getifaddr', 'en0'], capture_output=True, text=True)
   if not result.stdout.strip():
       result = subprocess.run(['ipconfig', 'getifaddr', 'en1'], capture_output=True, text=True)
   local_ip = result.stdout.strip()
   if local_ip:
       prefix = '.'.join(local_ip.split('.')[:3])
       print(f'Scanning {prefix}.0/24 for port 5555...')
       with concurrent.futures.ThreadPoolExecutor(max_workers=50) as ex:
           futures = {ex.submit(probe, f'{prefix}.{i}'): i for i in range(1,255)}
           for f in concurrent.futures.as_completed(futures):
               r = f.result()
               if r: print(f'  Found server at {r}:5555')
   else:
       print('Could not determine local IP')
   "
   ```

## Report
Summarize: Pi reachable (Y/N), server responding (Y/N), RP2040 connected (Y/N), sensor readings if available.
