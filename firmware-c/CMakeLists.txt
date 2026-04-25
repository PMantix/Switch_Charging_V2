cmake_minimum_required(VERSION 3.13)

# Pull in the Pico SDK (via pico_sdk_import.cmake). The SDK itself is NOT
# vendored — set PICO_SDK_PATH in your environment before configuring.
include(pico_sdk_import.cmake)

project(switching_circuit_v2_fw C CXX ASM)

set(CMAKE_C_STANDARD 11)
set(CMAKE_CXX_STANDARD 17)

# Default board: pico (RP2040). Override with -DPICO_BOARD=pico_w etc.
if(NOT DEFINED PICO_BOARD)
    set(PICO_BOARD pico)
endif()

pico_sdk_init()

# ---------------------------------------------------------------------------
# Firmware target
# ---------------------------------------------------------------------------
add_executable(switching_circuit_v2_fw
    src/main.c
    src/ina226.c
    src/streaming.c
    src/cmd_parser.c
    src/switching.c
    src/neopixel.c
    src/usb_cdc.c
)

target_include_directories(switching_circuit_v2_fw PRIVATE src)

# Generate the WS2812 PIO header from src/ws2812.pio at build time.
pico_generate_pio_header(switching_circuit_v2_fw
    ${CMAKE_CURRENT_LIST_DIR}/src/ws2812.pio
)

# Pico SDK libraries we depend on:
#   pico_stdlib       — basic init, GPIO, time
#   hardware_i2c      — INA226 transport
#   hardware_pio      — WS2812 driver
#   hardware_timer    — switching ISR
#   hardware_irq      — alarm interrupt setup
#   pico_unique_id    — board ID for J reply
target_link_libraries(switching_circuit_v2_fw
    pico_stdlib
    pico_unique_id
    hardware_i2c
    hardware_pio
    hardware_timer
    hardware_irq
    hardware_dma
)

# Use USB CDC for stdio (matches the MicroPython firmware's transport) and
# disable UART stdio so prints don't leak out of pin 0/1, which we use
# for FET drives in some board variants.
pico_enable_stdio_usb(switching_circuit_v2_fw 1)
pico_enable_stdio_uart(switching_circuit_v2_fw 0)

# Generate UF2 + map + bin + hex outputs.
pico_add_extra_outputs(switching_circuit_v2_fw)
