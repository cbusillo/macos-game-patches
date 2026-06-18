// Tiny CrossOver environment/path probe.

#define WIN32_LEAN_AND_MEAN
#include <windows.h>

#include <cstdio>

void print_env(const char* name) {
    char value[4096] = {};
    DWORD len = GetEnvironmentVariableA(name, value, sizeof(value));
    if (len == 0 || len >= sizeof(value)) {
        std::printf("%s=<unset>\n", name);
        return;
    }
    std::printf("%s=%s\n", name, value);
}

int main() {
    print_env("USERPROFILE");
    print_env("APPDATA");
    print_env("LOCALAPPDATA");
    print_env("VR_OVERRIDE");
    print_env("VR_PATHREG_OVERRIDE");
    print_env("ALVR_SHM_MAX_AGE_SECONDS");

    const char* paths[] = {
        "C:\\Program Files (x86)\\Steam\\steamapps\\common\\SteamVR\\bin\\vrclient_x64.dll",
        "C:\\users\\crossover\\AppData\\Local\\openvr\\openvrpaths.vrpath",
        "Z:\\Users\\<mac-user>\\Library\\Application Support\\CrossOver\\Bottles\\Steam\\drive_c\\Program Files (x86)\\Steam\\steamapps\\common\\SteamVR\\bin\\vrclient_x64.dll",
    };
    for (const char* path : paths) {
        DWORD attrs = GetFileAttributesA(path);
        std::printf("exists[%s]=%s attrs=0x%08lx\n", path, attrs == INVALID_FILE_ATTRIBUTES ? "no" : "yes", (unsigned long)attrs);
    }
    return 0;
}
