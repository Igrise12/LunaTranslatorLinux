# LunaBridge{32,64}.exe: runs inside a Wine/Proton prefix and exposes the
# LunaHost DLL API to the native Linux app over TCP (see main.cpp).
add_executable(LunaBridge WIN32 main.cpp)
target_precompile_headers(LunaBridge REUSE_FROM pch)
set_target_properties(LunaBridge PROPERTIES OUTPUT_NAME "LunaBridge${bitappendix}")
target_link_libraries(LunaBridge pch ws2_32)

# test program for scripts/linux/bridge_smoke.py
add_executable(LunaHookTestTarget WIN32 hooktarget.cpp)
set_target_properties(LunaHookTestTarget PROPERTIES OUTPUT_NAME "hooktarget${bitappendix}")
target_link_libraries(LunaHookTestTarget gdi32 user32)
