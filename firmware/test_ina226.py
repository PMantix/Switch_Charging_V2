"""Quick INA226 raw register dump for all 4 addresses."""
from machine import I2C, Pin
import time

i2c = I2C(1, sda=Pin(6), scl=Pin(7), freq=400000)

addrs = [("P1", 0x40), ("P2", 0x41), ("N1", 0x50), ("N2", 0x45)]
found = i2c.scan()
print("I2C scan:", [hex(a) for a in found])

for name, addr in addrs:
    if addr not in found:
        print(f"{name} (0x{addr:02X}): NOT FOUND")
        continue
    try:
        # Read config register
        i2c.writeto(addr, bytes([0x00]))
        cfg = i2c.readfrom(addr, 2)
        cfg_val = (cfg[0] << 8) | cfg[1]

        # Read shunt voltage register
        i2c.writeto(addr, bytes([0x01]))
        sh = i2c.readfrom(addr, 2)
        sh_val = (sh[0] << 8) | sh[1]
        if sh_val > 32767:
            sh_val -= 65536

        # Read bus voltage register
        i2c.writeto(addr, bytes([0x02]))
        bv = i2c.readfrom(addr, 2)
        bv_val = (bv[0] << 8) | bv[1]

        # Read die ID
        i2c.writeto(addr, bytes([0xFF]))
        die = i2c.readfrom(addr, 2)
        die_val = (die[0] << 8) | die[1]

        bus_v = bv_val * 1.25e-3
        shunt_uv = sh_val * 2.5

        print(f"{name} (0x{addr:02X}): cfg=0x{cfg_val:04X} die=0x{die_val:04X} bus_raw={bv_val} ({bus_v:.4f}V) shunt_raw={sh_val} ({shunt_uv:.1f}uV)")
    except OSError as e:
        print(f"{name} (0x{addr:02X}): ERROR {e}")
