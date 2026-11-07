cmake_minimum_required(VERSION 3.16)
project(collector_display LANGUAGES C CXX)
set(CMAKE_CXX_STANDARD 17)
get_filename_component(ROOT "${CMAKE_CURRENT_LIST_DIR}/../.." ABSOLUTE)
set(FIRMWARE "${ROOT}/upstream/StackChan/firmware")
set(LV_BUILD_CONF_PATH "${CMAKE_CURRENT_LIST_DIR}/lv_conf.h" CACHE PATH "" FORCE)
set(CONFIG_LV_BUILD_DEMOS OFF CACHE BOOL "" FORCE)
set(CONFIG_LV_BUILD_EXAMPLES OFF CACHE BOOL "" FORCE)
set(CONFIG_LV_USE_THORVG_INTERNAL OFF CACHE BOOL "" FORCE)
add_subdirectory("${FIRMWARE}/managed_components/lvgl__lvgl" lvgl)
add_executable(display_preview display_preview.cpp
    "${FIRMWARE}/main/collector/screen.cpp"
    "${FIRMWARE}/main/collector/laughing_man.c"
    "${FIRMWARE}/main/collector/collector_mono_15.c")
target_include_directories(display_preview PRIVATE "${FIRMWARE}/main")
target_link_libraries(display_preview PRIVATE lvgl)
add_executable(reaction_benchmark reaction_benchmark.cpp
    "${FIRMWARE}/main/collector/screen.cpp"
    "${FIRMWARE}/main/collector/laughing_man.c"
    "${FIRMWARE}/main/collector/collector_mono_15.c")
target_include_directories(reaction_benchmark PRIVATE "${FIRMWARE}/main")
target_link_libraries(reaction_benchmark PRIVATE lvgl)
