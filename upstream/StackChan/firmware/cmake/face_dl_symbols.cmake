# ESP-DL 3.3.0 and ESP-SR 2.3.1 export these same assembly names.
# They are not interchangeable (the convolution bias layout differs).
# Keep ESP-SR untouched and namespace the new ESP-DL implementation.
# Apply to C/C++ and preprocessed assembly, including all internal references.
idf_component_get_property(face_dl_lib espressif__esp-dl COMPONENT_LIB)
set(face_dl_shared_symbols
    dl_tie728_bzero
    dl_tie728_bzero_128b
    dl_tie728_memcpy
    dl_tie728_memset_16b
    dl_tie728_memset_32b
    dl_tie728_memset_8b
    dl_tie728_s16_conv2d_11cn
    dl_tie728_s16_conv2d_11cn_bias
    dl_tie728_s16_conv2d_11cn_bias_prelu
    dl_tie728_s16_conv2d_11cn_bias_relu
    dl_tie728_s16_conv2d_11cn_prelu
    dl_tie728_s16_conv2d_11cn_relu
    dl_tie728_s16_conv2d_33cn
    dl_tie728_s16_conv2d_33cn_bias
    dl_tie728_s16_conv2d_33cn_bias_prelu
    dl_tie728_s16_conv2d_33cn_bias_relu
    dl_tie728_s16_conv2d_33cn_prelu
    dl_tie728_s16_conv2d_33cn_relu
    dl_tie728_s16_conv2d_hwcn
    dl_tie728_s16_conv2d_hwcn_bias
    dl_tie728_s16_conv2d_hwcn_bias_prelu
    dl_tie728_s16_conv2d_hwcn_bias_relu
    dl_tie728_s16_conv2d_hwcn_prelu
    dl_tie728_s16_conv2d_hwcn_relu
    dl_tie728_s8_conv2d_11cn
    dl_tie728_s8_conv2d_11cn_prelu
    dl_tie728_s8_conv2d_11cn_relu
    dl_tie728_s8_conv2d_33cn
    dl_tie728_s8_conv2d_33cn_prelu
    dl_tie728_s8_conv2d_33cn_relu
    dl_tie728_s8_conv2d_hwcn
    dl_tie728_s8_conv2d_hwcn_prelu
    dl_tie728_s8_conv2d_hwcn_relu
    dl_tie728_s8_unaligned_conv2d_11cn
    dl_tie728_s8_unaligned_conv2d_33cn
    dl_tie728_s8_unaligned_conv2d_hwcn
    dl_xtensa_s16_conv2d_11cn
    dl_xtensa_s16_conv2d_11cn_bias
    dl_xtensa_s16_conv2d_11cn_bias_relu
    dl_xtensa_s16_conv2d_11cn_relu
    dl_xtensa_s16_conv2d_33cn
    dl_xtensa_s16_conv2d_33cn_bias
    dl_xtensa_s16_conv2d_33cn_bias_relu
    dl_xtensa_s16_conv2d_33cn_relu
    dl_xtensa_s16_conv2d_hwcn
    dl_xtensa_s16_conv2d_hwcn_bias
    dl_xtensa_s16_conv2d_hwcn_bias_relu
    dl_xtensa_s16_conv2d_hwcn_relu
)
foreach(symbol IN LISTS face_dl_shared_symbols)
    target_compile_definitions(${face_dl_lib} PRIVATE "${symbol}=collector_face_${symbol}")
endforeach()
idf_component_get_property(face_sr_dir espressif__esp-sr COMPONENT_DIR)
add_custom_target(check_face_kernel_isolation ALL
    COMMAND "${PYTHON}" "${PROJECT_DIR}/../../../scripts/check-face-link.py"
        --nm "${CMAKE_NM}" --dl "$<TARGET_FILE:${face_dl_lib}>"
        --sr "${face_sr_dir}/lib/esp32s3/libdl_lib.a"
    DEPENDS ${face_dl_lib}
    VERBATIM)
