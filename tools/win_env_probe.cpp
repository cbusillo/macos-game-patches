// Tiny CrossOver environment/path probe.

#define WIN32_LEAN_AND_MEAN
#include <windows.h>

#include <cstring>
#include <cstdio>
#include <string>
#include <vector>

constexpr const char* kHostUsersGlob = "Z:\\Users\\*";
constexpr const char* kCrossOverSteamBottleSuffix =
    "\\Library\\Application Support\\CrossOver\\Bottles\\Steam\\drive_c";
constexpr const char* kSteamVrDllSuffix =
    "\\Program Files (x86)\\Steam\\steamapps\\common\\SteamVR\\bin\\vrclient_x64.dll";

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

std::string trim_trailing_slashes(std::string path) {
    while (!path.empty() && (path.back() == '\\' || path.back() == '/')) {
        path.pop_back();
    }
    return path;
}

void maybe_add_env_steamvr_path(std::vector<std::string>* paths) {
    char drive_c[4096] = {};
    DWORD len = GetEnvironmentVariableA("ALVR_CROSSOVER_STEAM_DRIVE_C", drive_c, sizeof(drive_c));
    if (len == 0 || len >= sizeof(drive_c)) {
        return;
    }

    paths->push_back(trim_trailing_slashes(drive_c) + kSteamVrDllSuffix);
}

void add_host_steamvr_paths(std::vector<std::string>* paths) {
    WIN32_FIND_DATAA find_data = {};
    HANDLE find = FindFirstFileA(kHostUsersGlob, &find_data);
    if (find == INVALID_HANDLE_VALUE) {
        std::printf("host_users_glob[%s]=unavailable error=%lu\n", kHostUsersGlob, GetLastError());
        return;
    }

    do {
        if ((find_data.dwFileAttributes & FILE_ATTRIBUTE_DIRECTORY) == 0) {
            continue;
        }
        if (std::strcmp(find_data.cFileName, ".") == 0 || std::strcmp(find_data.cFileName, "..") == 0) {
            continue;
        }

        std::string drive_c = "Z:\\Users\\";
        drive_c += find_data.cFileName;
        drive_c += kCrossOverSteamBottleSuffix;
        paths->push_back(drive_c + kSteamVrDllSuffix);
    } while (FindNextFileA(find, &find_data));

    FindClose(find);
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
    maybe_add_env_steamvr_path(&paths);
    add_host_steamvr_paths(&paths);

    for (const auto& path : paths) {
        check_path(path);
    }
    return 0;
}
