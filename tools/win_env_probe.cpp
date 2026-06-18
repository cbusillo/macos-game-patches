// Tiny CrossOver environment/path probe.

#define WIN32_LEAN_AND_MEAN
#include <windows.h>

#include <cstdio>
#include <string>
#include <vector>

void print_env(const char* name) {
    char value[4096] = {};
    DWORD len = GetEnvironmentVariableA(name, value, sizeof(value));
    if (len == 0 || len >= sizeof(value)) {
        std::printf("%s=<unset>\n", name);
        return;
    }
    std::printf("%s=%s\n", name, value);
}

void check_path(const std::string& path) {
    DWORD attrs = GetFileAttributesA(path.c_str());
    std::printf(
        "exists[%s]=%s attrs=0x%08lx\n",
        path.c_str(),
        attrs == INVALID_FILE_ATTRIBUTES ? "no" : "yes",
        (unsigned long)attrs
    );
}

void maybe_add_host_steamvr_path(std::vector<std::string>* paths) {
    char drive_c[4096] = {};
    DWORD len = GetEnvironmentVariableA("ALVR_CROSSOVER_STEAM_DRIVE_C", drive_c, sizeof(drive_c));
    if (len == 0 || len >= sizeof(drive_c)) {
        std::printf("ALVR_CROSSOVER_STEAM_DRIVE_C=<unset; skipping host mirror SteamVR path>\n");
        return;
    }

    std::string base = drive_c;
    while (!base.empty() && (base.back() == '\\' || base.back() == '/')) {
        base.pop_back();
    }
    paths->push_back(base + "\\Program Files (x86)\\Steam\\steamapps\\common\\SteamVR\\bin\\vrclient_x64.dll");
}

int main() {
    print_env("USERPROFILE");
    print_env("APPDATA");
    print_env("LOCALAPPDATA");
    print_env("VR_OVERRIDE");
    print_env("VR_PATHREG_OVERRIDE");
    print_env("ALVR_SHM_MAX_AGE_SECONDS");
    print_env("ALVR_CROSSOVER_STEAM_DRIVE_C");

    std::vector<std::string> paths = {
        "C:\\Program Files (x86)\\Steam\\steamapps\\common\\SteamVR\\bin\\vrclient_x64.dll",
        "C:\\users\\crossover\\AppData\\Local\\openvr\\openvrpaths.vrpath",
    };
    maybe_add_host_steamvr_path(&paths);

    for (const auto& path : paths) {
        check_path(path);
    }
    return 0;
}
