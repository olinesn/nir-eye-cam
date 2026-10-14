cmake_minimum_required(VERSION 3.13)

# Board profile: "pico" (Raspberry Pi Pico) or "xiao" (Seeed XIAO RP2040).
set(NEC_BOARD "pico" CACHE STRING "Carrier board: pico or xiao")
if(NEC_BOARD STREQUAL "xiao")
    set(PICO_BOARD seeed_xiao_rp2040)
elseif(NEC_BOARD STREQUAL "pico")
    set(PICO_BOARD pico)
else()
    message(FATAL_ERROR "NEC_BOARD must be pico or xiao, got '${NEC_BOARD}'")
endif()

include(pico_sdk_import.cmake)

project(nir_eye_cam C CXX ASM)
set(CMAKE_C_STANDARD 11)

pico_sdk_init()

add_executable(nir_eye_cam
    src/main.c
    src/camera.c
    src/syncio.c
    src/usb_descriptors.c
)

target_include_directories(nir_eye_cam PRIVATE src)
target_compile_options(nir_eye_cam PRIVATE -Wall -Wextra -Wno-unused-parameter)

if(NEC_BOARD STREQUAL "xiao")
    target_compile_definitions(nir_eye_cam PRIVATE NEC_BOARD_XIAO=1)
endif()

target_link_libraries(nir_eye_cam PRIVATE
    pico_stdlib
    pico_unique_id
    hardware_dma
    hardware_i2c
    hardware_irq
    hardware_pio
    hardware_pwm
    tinyusb_device
)

# The CDC port carries binary packets; never let stdio write into it.
pico_enable_stdio_usb(nir_eye_cam 0)
pico_enable_stdio_uart(nir_eye_cam 0)

set_target_properties(nir_eye_cam PROPERTIES OUTPUT_NAME "nir_eye_cam_${NEC_BOARD}")
pico_add_extra_outputs(nir_eye_cam)
