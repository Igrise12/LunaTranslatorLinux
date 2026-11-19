
include(FetchContent)

option(WIN10ABOVE "WIN10ABOVE" OFF)

if(NOT WIN32)
    # Linux：官方预编译包（CPU），直接链接（DT_NEEDED），运行时靠 $ORIGIN rpath找到
    set(ort_version 1.24.4)
    if(CMAKE_SYSTEM_PROCESSOR MATCHES "(x86_|X86_|AMD64|amd64)i?64")
        set(ort_arch x64)
    elseif(CMAKE_SYSTEM_PROCESSOR MATCHES "^(aarch64|arm64)$")
        set(ort_arch aarch64)
    else()
        message(FATAL_ERROR "unsupported onnxruntime architecture: ${CMAKE_SYSTEM_PROCESSOR}")
    endif()
    # Microsoft.ML.OnnxRuntime NuGet package: same libonnxruntime.so as the
    # GitHub release tarball (onnxruntime-linux-<arch>-<ver>.tgz), which is
    # also accepted via FETCHCONTENT_SOURCE_DIR_ONNXRUNTIME.
    # one package for all Linux architectures
    FetchContent_Declare(onnxruntime
        URL https://api.nuget.org/v3-flatcontainer/microsoft.ml.onnxruntime/${ort_version}/microsoft.ml.onnxruntime.${ort_version}.nupkg
        URL_HASH SHA256=4b978d5065b85e7004b6c6f60ca494bd978fbe6836cbf0a0b52d82b61ab99638
        DOWNLOAD_NAME onnxruntime-${ort_version}.zip
        DOWNLOAD_EXTRACT_TIMESTAMP true
    )
    FetchContent_MakeAvailable(onnxruntime)
    if(EXISTS ${onnxruntime_SOURCE_DIR}/build/native/include)
        # NuGet layout
        string(REPLACE "aarch64" "arm64" ort_rid "linux-${ort_arch}")
        set(ort_include ${onnxruntime_SOURCE_DIR}/build/native/include)
        set(ort_lib ${onnxruntime_SOURCE_DIR}/runtimes/${ort_rid}/native/libonnxruntime.so)
    else()
        # release tarball layout
        set(ort_include ${onnxruntime_SOURCE_DIR}/include)
        set(ort_lib ${onnxruntime_SOURCE_DIR}/lib/libonnxruntime.so.${ort_version})
    endif()
    add_library(onnxruntime INTERFACE)
    target_include_directories(onnxruntime INTERFACE ${ort_include})
    target_link_libraries(onnxruntime INTERFACE ${ort_lib})

    # 链接记录的DT_NEEDED是SONAME(libonnxruntime.so.1)，只拷真文件+建SONAME软链
    add_custom_target(copy_onnxruntime_dll
        COMMAND ${CMAKE_COMMAND} -E copy_if_different
            ${ort_lib}
            ${CMAKE_FINAL_OUTPUT_DIRECTORY}/libonnxruntime.so.${ort_version}
        COMMAND ln -sf libonnxruntime.so.${ort_version} ${CMAKE_FINAL_OUTPUT_DIRECTORY}/libonnxruntime.so.1
    )
    add_dependencies(onnxruntime copy_onnxruntime_dll)
    return()
endif()

#onnxruntime
#版本：
#1.13.1 最后一个原生支持win7的版本
#1.9.0 最早的可运行paddleocr的版本
#1.10.0 体积比1.9.0还要小一点
if(WIN10ABOVE)
    # https://github.com/microsoft/onnxruntime/issues/15255
    # DML和ort的版本必须对应，因此必须附带而非加载系统的，否则无法使用gpu
    
    if(${CMAKE_SIZEOF_VOID_P} EQUAL 8)
        set(ort_version 1.24.4) 
    else()
        set(ort_version 1.22.1) #之后不再有32位release
    endif()
    FetchContent_Declare(onnxruntime 
        URL https://www.nuget.org/api/v2/package/Microsoft.ML.OnnxRuntime.DirectML/${ort_version}
        DOWNLOAD_EXTRACT_TIMESTAMP true
    )
    FetchContent_MakeAvailable(onnxruntime)
    add_library(onnxruntime INTERFACE)
    target_include_directories(onnxruntime INTERFACE ${onnxruntime_SOURCE_DIR}/build/native/include)
    target_link_libraries(onnxruntime INTERFACE ${onnxruntime_SOURCE_DIR}/runtimes/win-${platform}/native/onnxruntime.lib)
    set(onnxruntime_DLL ${onnxruntime_SOURCE_DIR}/runtimes/win-${platform}/native/onnxruntime.dll)
    message(${onnxruntime_DLL})
    function(extract_last_version_simple file_path output_var)
        file(READ "${file_path}" file_content)
        # 使用贪婪匹配直接找到最后一个版本号
        string(REGEX MATCH ".*([0-9]+\\.[0-9]+\\.[0-9]+)" dummy "${file_content}")
        if(CMAKE_MATCH_1)
            set(${output_var} "${CMAKE_MATCH_1}" PARENT_SCOPE)
        else()
            set(${output_var} "" PARENT_SCOPE)
        endif()
    endfunction()
    extract_last_version_simple(${onnxruntime_SOURCE_DIR}/Microsoft.ML.OnnxRuntime.DirectML.nuspec directml_version)
    message(${directml_version})
    FetchContent_Declare(directml 
        URL https://www.nuget.org/api/v2/package/Microsoft.AI.DirectML/${directml_version}
        DOWNLOAD_EXTRACT_TIMESTAMP true
    )
    FetchContent_MakeAvailable(directml)
    set(directml_DLL ${directml_SOURCE_DIR}/bin/${platform}-win/DirectML.dll)
    message(${directml_DLL})
        
    add_custom_target(copy_directml_dll
        COMMAND ${CMAKE_COMMAND} -E copy_if_different
            ${directml_DLL}
            ${CMAKE_FINAL_OUTPUT_DIRECTORY}/DirectML.dll
    )
    add_dependencies(onnxruntime copy_directml_dll)
else()
    set(ort_version 1.10.0)

    FetchContent_Declare(onnxruntime 
        URL https://github.com/microsoft/onnxruntime/releases/download/v${ort_version}/onnxruntime-win-${platform}-${ort_version}.zip
        DOWNLOAD_EXTRACT_TIMESTAMP true
    )
    FetchContent_MakeAvailable(onnxruntime)
    add_library(onnxruntime INTERFACE)
    target_include_directories(onnxruntime INTERFACE ${onnxruntime_SOURCE_DIR}/include)
    target_link_libraries(onnxruntime INTERFACE ${onnxruntime_SOURCE_DIR}/lib/onnxruntime.lib)
    set(onnxruntime_DLL ${onnxruntime_SOURCE_DIR}/lib/onnxruntime.dll)
    message(${onnxruntime_DLL})
    
endif()

add_custom_target(copy_onnxruntime_dll
    COMMAND ${CMAKE_COMMAND} -E copy_if_different
        ${onnxruntime_DLL}
        ${CMAKE_FINAL_OUTPUT_DIRECTORY}/onnxruntime.dll
)
add_dependencies(onnxruntime copy_onnxruntime_dll)

target_link_options(onnxruntime INTERFACE "/DELAYLOAD:onnxruntime.dll")