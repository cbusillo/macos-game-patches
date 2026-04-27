#include <windows.h>

#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>

#ifdef _WIN64
#define WINE_DXGI_SHARED_MAP_NAME L"Local\\WineDxgiSharedHandleMapV3_64"
#define WINE_DXGI_SHARED_MUTEX_NAME L"Local\\WineDxgiSharedHandleMutexV3_64"
#else
#define WINE_DXGI_SHARED_MAP_NAME L"Local\\WineDxgiSharedHandleMapV3_32"
#define WINE_DXGI_SHARED_MUTEX_NAME L"Local\\WineDxgiSharedHandleMutexV3_32"
#endif

#define WINE_DXGI_SHARED_MAP_MAGIC 0x5744534bu
#define WINE_DXGI_SHARED_MAP_CAPACITY 4096

struct wine_dxgi_shared_buffer_desc
{
    uint32_t byte_width;
    uint32_t usage;
    uint32_t bind_flags;
    uint32_t cpu_access_flags;
    uint32_t misc_flags;
    uint32_t structure_byte_stride;
};

struct wine_dxgi_shared_texture2d_desc
{
    uint32_t width;
    uint32_t height;
    uint32_t mip_levels;
    uint32_t array_size;
    uint32_t format;
    uint32_t sample_count;
    uint32_t sample_quality;
    uint32_t usage;
    uint32_t bind_flags;
    uint32_t cpu_access_flags;
    uint32_t misc_flags;
};

struct wine_dxgi_shared_entry
{
    uint64_t token;
    uint64_t object_ptr;
    DWORD owner_pid;
    uint64_t owner_process_start_time;
    uint32_t resource_type;
    uint32_t shared_misc_flags;
    uint32_t shared_features;
    uint32_t backing_type;
    uint32_t reserved0;
    uint64_t backing_id;
    uint64_t reserved1;
    struct wine_dxgi_shared_buffer_desc buffer;
    struct wine_dxgi_shared_texture2d_desc texture2d;
};

struct wine_dxgi_shared_state
{
    DWORD magic;
    uint64_t counter;
    struct wine_dxgi_shared_entry entries[WINE_DXGI_SHARED_MAP_CAPACITY];
};

static int parse_token(const char *text, uint64_t *value)
{
    char *end = NULL;
    unsigned long long parsed;

    if (!text || !value)
        return 0;

    parsed = strtoull(text, &end, 0);
    if (end == text || (end && *end != '\0'))
        return 0;

    *value = (uint64_t)parsed;
    return 1;
}

int main(int argc, char **argv)
{
    HANDLE map = NULL;
    HANDLE mutex = NULL;
    struct wine_dxgi_shared_state *state = NULL;
    uint64_t token_filter = 0;
    int use_filter = 0;
    int locked_mutex = 0;
    unsigned int count = 0;
    unsigned int i;

    if (argc == 2)
    {
        if (!parse_token(argv[1], &token_filter))
        {
            printf("usage: %s [token_hex_or_decimal]\n", argv[0]);
            return 2;
        }
        use_filter = 1;
    }
    else if (argc > 2)
    {
        printf("usage: %s [token_hex_or_decimal]\n", argv[0]);
        return 2;
    }

    map = OpenFileMappingW(FILE_MAP_READ, FALSE, WINE_DXGI_SHARED_MAP_NAME);
    if (!map)
    {
        printf("open_map_failed error=%lu\n", GetLastError());
        return 1;
    }

    state = (struct wine_dxgi_shared_state *)MapViewOfFile(map, FILE_MAP_READ, 0, 0, sizeof(*state));
    if (!state)
    {
        printf("map_view_failed error=%lu\n", GetLastError());
        CloseHandle(map);
        return 1;
    }

    mutex = OpenMutexW(SYNCHRONIZE, FALSE, WINE_DXGI_SHARED_MUTEX_NAME);
    if (mutex)
    {
        DWORD wait_result = WaitForSingleObject(mutex, 2000);
        if (wait_result == WAIT_OBJECT_0 || wait_result == WAIT_ABANDONED)
            locked_mutex = 1;
        else
            printf("wait_mutex_failed result=%lu error=%lu\n", wait_result, GetLastError());
    }
    else
    {
        printf("open_mutex_failed error=%lu\n", GetLastError());
    }

    printf("map_magic=0x%08lx expected_magic=0x%08x counter=0x%llx\n",
        (unsigned long)state->magic,
        (unsigned int)WINE_DXGI_SHARED_MAP_MAGIC,
        (unsigned long long)state->counter);

    for (i = 0; i < WINE_DXGI_SHARED_MAP_CAPACITY; ++i)
    {
        const struct wine_dxgi_shared_entry *entry = &state->entries[i];
        if (!entry->token)
            continue;
        if (use_filter && entry->token != token_filter)
            continue;

        ++count;
        printf(
            "entry[%u] token=0x%llx owner_pid=%lu owner_start=0x%llx type=%u "
            "backing=%u shared_misc=0x%x features=0x%x object_ptr=0x%llx backing_id=0x%llx "
            "tex=%ux%u fmt=%u bind=0x%x misc=0x%x\n",
            i,
            (unsigned long long)entry->token,
            (unsigned long)entry->owner_pid,
            (unsigned long long)entry->owner_process_start_time,
            (unsigned int)entry->resource_type,
            (unsigned int)entry->backing_type,
            (unsigned int)entry->shared_misc_flags,
            (unsigned int)entry->shared_features,
            (unsigned long long)entry->object_ptr,
            (unsigned long long)entry->backing_id,
            (unsigned int)entry->texture2d.width,
            (unsigned int)entry->texture2d.height,
            (unsigned int)entry->texture2d.format,
            (unsigned int)entry->texture2d.bind_flags,
            (unsigned int)entry->texture2d.misc_flags);
    }

    printf("entry_count=%u\n", count);

    if (locked_mutex && mutex)
        ReleaseMutex(mutex);
    if (mutex)
        CloseHandle(mutex);
    UnmapViewOfFile(state);
    CloseHandle(map);

    return 0;
}
