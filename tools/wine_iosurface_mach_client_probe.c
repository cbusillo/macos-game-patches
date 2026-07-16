#include <windows.h>

#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

struct alvr_iosurface_import_result
{
    uint32_t verification_status;
    uint32_t surface_id;
    uint32_t width;
    uint32_t height;
    uint32_t bytes_per_row;
    uint32_t pixel_format;
    uint32_t producer_pid;
    uint32_t client_pid;
    uint8_t expected_bgra[4];
    uint8_t actual_bgra[4];
};

_Static_assert(sizeof(struct alvr_iosurface_import_result) == 40,
               "import result ABI changed");

typedef LONG(WINAPI *import_probe_fn)(
    const char *service_name,
    uint64_t session_nonce,
    struct alvr_iosurface_import_result *result);

static int parse_nonce(const char *text, uint64_t *nonce)
{
    char *end = NULL;
    unsigned long long value;

    if (!text || !*text) return 0;
    value = _strtoui64(text, &end, 10);
    if (!end || *end != '\0' || value == 0) return 0;
    *nonce = value;
    return 1;
}

int main(int argc, char **argv)
{
    HMODULE bridge;
    FARPROC procedure;
    import_probe_fn import_probe;
    struct alvr_iosurface_import_result result = {0};
    uint64_t nonce;
    LONG status;

    if (argc != 3 || !parse_nonce(argv[2], &nonce))
    {
        fprintf(stderr, "usage: %s SERVICE NONCE\n", argv[0]);
        return 64;
    }

    bridge = LoadLibraryA("alvr_iosurface_bridge.dll");
    if (!bridge)
    {
        fprintf(stderr,
                "PROBE error=LoadLibraryA win32=%lu\n",
                GetLastError());
        return 1;
    }
    procedure = GetProcAddress(bridge, "alvr_iosurface_import_probe");
    if (!procedure)
    {
        fprintf(stderr,
                "PROBE error=GetProcAddress win32=%lu\n",
                GetLastError());
        FreeLibrary(bridge);
        return 1;
    }
    _Static_assert(sizeof(import_probe) == sizeof(procedure),
                   "function pointer size mismatch");
    memcpy(&import_probe, &procedure, sizeof(import_probe));

    status = import_probe(argv[1], nonce, &result);
    printf("PROBE wine_client ntstatus=0x%08lx verification_status=%u "
           "surface_id=%u size=%ux%u bytes_per_row=%u "
           "pixel_format=0x%08x producer_pid=%u client_pid=%u "
           "actual_bgra=%u,%u,%u,%u expected_bgra=%u,%u,%u,%u result=%s\n",
           (unsigned long)(uint32_t)status,
           result.verification_status,
           result.surface_id,
           result.width,
           result.height,
           result.bytes_per_row,
           result.pixel_format,
           result.producer_pid,
           result.client_pid,
           result.actual_bgra[0],
           result.actual_bgra[1],
           result.actual_bgra[2],
           result.actual_bgra[3],
           result.expected_bgra[0],
           result.expected_bgra[1],
           result.expected_bgra[2],
           result.expected_bgra[3],
           status == 0 && result.verification_status == 0 ? "pass" : "fail");
    FreeLibrary(bridge);
    return status == 0 && result.verification_status == 0 ? 0 : 1;
}
