idf_component_register(
    SRCS "main.c" "vd55g1.c" "capture.c" "syncio.c" "usb_link.c"
    INCLUDE_DIRS "." "../../common"
    REQUIRES esp_driver_cam esp_driver_isp esp_driver_i2c esp_driver_gpio
             esp_driver_ledc esp_hw_support esp_timer esp_psram
)
