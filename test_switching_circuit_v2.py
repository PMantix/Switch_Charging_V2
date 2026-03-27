from gpiozero import OutputDevice
from time import sleep

q1 = OutputDevice(17, active_high=True, initial_value=False)
q3 = OutputDevice(27, active_high=True, initial_value=False)
q2 = OutputDevice(22, active_high=True, initial_value=False)
q4 = OutputDevice(23, active_high=True, initial_value=False)

print('State 0: Q1+Q3 - LED 1 (+A/-A)')
q1.on(); q3.on()
sleep(3)
q1.off(); q3.off()
sleep(1)

print('State 1: Q1+Q4 - LED 2 (+A/-B)')
q1.on(); q4.on()
sleep(3)
q1.off(); q4.off()
sleep(1)

print('State 2: Q2+Q3 - LED 3 (+B/-A)')
q2.on(); q3.on()
sleep(3)
q2.off(); q3.off()
sleep(1)

print('State 3: Q2+Q4 - LED 4 (+B/-B)')
q2.on(); q4.on()
sleep(3)
q2.off(); q4.off()
sleep(1)

print('State 4: All ON - all 4 LEDs')
q1.on(); q3.on(); q2.on(); q4.on()
sleep(3)
q1.off(); q3.off(); q2.off(); q4.off()

print('All OFF')
