/*
 * greenboost_vulkan_layer.c , VK_LAYER_GREENBOOST_memory
 *
 * Implicit Vulkan layer, activated by GREENBOOST_VULKAN=1 in the environment.
 * Install manifest: /etc/vulkan/implicit_layer.d/VkLayer_greenboost.json
 *
 * Hook 1: vkGetPhysicalDeviceMemoryProperties[2[KHR]]
 *   Inflates the device-local heap to match the virtual VRAM the CUDA shim
 *   reports to CUDA applications (auto-detected from kernel module params).
 *   Games read this value to choose quality presets and texture budgets.
 *
 * Hook 2: vkAllocateMemory
 *   On VK_ERROR_OUT_OF_DEVICE_MEMORY, attempts a tiered fallback:
 *     T2: GreenBoost DDR via DMA-BUF import (VK_KHR_external_memory_fd).
 *     T3: NVMe-spillable 4K pages via DMA-BUF import (for large allocs).
 *   Pressure-aware: skips doomed T2 attempts when the pool is critical.
 *   All overflow allocations are tracked in a hash table for lifecycle management.
 *
 * Hook 3: vkFreeMemory
 *   On freeing a tracked T2/T3 allocation, marks the kernel buffer COLD via
 *   GB_IOCTL_MADVISE so it is evicted first under pressure.
 *
 * Memory orchestration:
 *   - Burst detector: loading-screen alloc bursts are marked HOT (working set)
 *   - Pressure cache: GB_IOCTL_GET_INFO polled every 16 allocs
 *   - Session cleanup: GB_IOCTL_RELEASE_PID in destructor
 *
 * Dispatch model: minimal static arrays (<=4 instances / <=4 devices).
 * Thread-safety rule: the mutex guards ONLY the dispatch-table arrays.
 *   All external calls (next_gipa, next_gdpa, Vulkan functions) are made
 *   OUTSIDE the mutex to prevent deadlock under concurrent threads.
 */

#define _GNU_SOURCE
#include <stdio.h>
#include <string.h>
#include <strings.h>   /* strncasecmp() , see gbvk_detect_is_game() */
#include <stdlib.h>
#include <stdint.h>
#include <stdatomic.h>
#include <unistd.h>
#include <fcntl.h>
#include <sys/ioctl.h>
#include <pthread.h>
#include <signal.h>
#include <syslog.h>
#include <time.h>
#include <stdarg.h>
#include <sys/stat.h>
#include <errno.h>

#include <vulkan/vulkan.h>
#include <vulkan/vk_layer.h>

#include "greenboost_ioctl.h"

/* ── Configuration ────────────────────────────────────────────────────── */

/* Total virtual VRAM presented to games (T1 + T2).
 * 0 = init failed → inflate_heaps() is a no-op. */
static uint64_t g_gbvk_virtual_vram_bytes = 0;

/* Headroom held back from the budget we report to games. See
 * gbvk_reserved_budget() for the reasoning; tunable via
 * GREENBOOST_VK_BUDGET_RESERVE_DIV / _MAX_MB. */
static uint64_t g_gbvk_budget_reserve_div    = 8;      /* 1/8 = 12.5%      */
static uint64_t g_gbvk_budget_reserve_max    = 1024ULL * 1024ULL * 1024ULL; /* 1 GiB cap */

/* Minimum alloc size to attempt T2 DMA-BUF fallback (default: from env or 32 MB).
 * Lower than AI shim's 64 MB because games make many 32-64 MB texture allocs. */
static uint64_t g_gbvk_overflow_min_bytes = 32ULL * 1024ULL * 1024ULL;

/* Minimum alloc size to attempt T3 NVMe fallback (default 128 MB).
 * Only large streaming textures can tolerate NVMe latency. */
static uint64_t g_gbvk_t3_min_bytes = 128ULL * 1024ULL * 1024ULL;

/* Debug flag , set GREENBOOST_VK_DEBUG=1 for verbose syslog. */
static int g_gbvk_debug = 0;

/* PSO compile thread hint , mirrors DXVK_NUM_COMPILER_THREADS / GREENBOOST_SHADER_THREADS.
 * Read once at gbvk_init(); defaults to (nproc - 2) clamped to [1, 32].
 * Used by gbvk_CreateGraphicsPipelines to split large batches across threads. */
static unsigned g_gbvk_shader_threads = 0;

#define GBVK_MAX_INSTANCES  4
#define GBVK_MAX_DEVICES    4

/* ── PR-GGGG: forward declarations.  These atomics and helpers are referenced
 * from `gbvk_init` / `gbvk_dump_stats` (defined ~line 400+) but their full
 * definitions live near the pipeline-cache block far below.  Tentative
 * definitions: every counter zero-initialises by C rules. */
static _Atomic uint64_t g_gbvk_pipe_count_g;
static _Atomic uint64_t g_gbvk_pipe_count_c;
static _Atomic uint64_t g_gbvk_pipe_total_ns;
static _Atomic uint64_t g_gbvk_pipe_slow_ns;
static _Atomic uint64_t g_gbvk_present_count;
static _Atomic uint64_t g_gbvk_present_last_ns;
static _Atomic uint64_t g_gbvk_present_total_ns;
static _Atomic uint64_t g_gbvk_present_worst_ns;
static _Atomic uint64_t g_gbvk_present_hitches;
/* PR-P1: Frame-time ring buffer , lock-free single-writer/single-reader.
 * 512 slots ≈ 8 s at 60 fps , enough for stable 1% low computation.
 * SIZE must be a power of 2 so we can mask instead of mod. */
#define GBVK_FTBUF_SIZE 512
static uint64_t         g_gbvk_ft_buf[GBVK_FTBUF_SIZE];
static _Atomic uint64_t g_gbvk_ft_head;    /* monotonically increasing write index */
static _Atomic uint64_t g_gbvk_ft_filled;  /* total frames written (may exceed SIZE) */
static void gbvk_install_signal_handlers_once(void);
static void gbvk_pipe_snapshot_thread_start_once(void);
static void gbvk_pipe_drop_device(VkDevice device);
/* NIS dispatch forward declarations.  Use struct tags so we don't need
 * the full struct definitions visible yet , the typedef + definition
 * land together near the dispatch implementation block far below. */
struct GbNisSwapState_;
struct GbDevData_;
static struct GbNisSwapState_ *nis_state_alloc_slot(VkSwapchainKHR sc);
static struct GbNisSwapState_ *nis_state_find(VkSwapchainKHR sc);
static void gbvk_nis_swap_alloc(struct GbDevData_ *d, VkDevice device,
                                VkSwapchainKHR sc,
                                const VkSwapchainCreateInfoKHR *ci,
                                struct GbNisSwapState_ *s,
                                VkFormat storage_fmt);
static void gbvk_nis_swap_free(struct GbDevData_ *d, struct GbNisSwapState_ *s);
/* NIS format helpers , definitions live near nis_fill_sharpen_defaults below. */
static VkFormat nis_srgb_to_storage_format(VkFormat fmt);
static float    nis_read_sharpness(void);
static float    nis_read_scale(void);

/* ── Logging ──────────────────────────────────────────────────────────── */

/* write(2) is declared __wur, and a bare (void) cast on the call does NOT
 * satisfy that , GCC deliberately ignores it, which is where the
 * -Wunused-result noise on every build came from. Binding the result and
 * discarding the variable is the form GCC accepts. Ignoring the result is
 * correct here on purpose: a short write or a failed write on a log or
 * sysfs path must never perturb the game we're loaded into. */
#define GB_IGNORE_WRITE(expr) do { ssize_t gb__n = (expr); (void)gb__n; } while (0)

static int             g_gbvk_log_fd    = -1;
static pthread_mutex_t g_gbvk_log_mutex = PTHREAD_MUTEX_INITIALIZER;

static void gbvk_mkdirs(const char *path)
{
    char buf[4096];
    snprintf(buf, sizeof(buf), "%s", path);
    for (char *p = buf + 1; *p; p++) {
        if (*p == '/') { *p = '\0'; mkdir(buf, 0755); *p = '/'; }
    }
    mkdir(buf, 0755);
}

__attribute__((constructor))
static void gbvk_init_logging(void)
{
    const char *xdg  = getenv("XDG_DATA_HOME");
    const char *home = getenv("HOME");
    char dir[4096];
    if (xdg && xdg[0])
        snprintf(dir, sizeof(dir), "%s/greenboost/proton-logs", xdg);
    else
        snprintf(dir, sizeof(dir), "%s/.local/share/greenboost/proton-logs",
                 (home && home[0]) ? home : "/tmp");
    gbvk_mkdirs(dir);

    char logpath[4200];
    snprintf(logpath, sizeof(logpath), "%s/vulkan-layer.log", dir);
    g_gbvk_log_fd = open(logpath, O_WRONLY | O_CREAT | O_APPEND | O_CLOEXEC, 0644);

    openlog("VK_LAYER_GREENBOOST", LOG_PID | LOG_NDELAY, LOG_USER);

    const char *dbg = getenv("GREENBOOST_VK_DEBUG");
    if (dbg && dbg[0] == '1') g_gbvk_debug = 1;
}

__attribute__((destructor))
static void gbvk_fini_logging(void)
{
    closelog();
    if (g_gbvk_log_fd >= 0) { close(g_gbvk_log_fd); g_gbvk_log_fd = -1; }
}

static void gbvk_emit(int level, const char *fmt, ...)
    __attribute__((format(printf, 2, 3)));
static void gbvk_emit(int level, const char *fmt, ...)
{
    struct timespec ts;
    clock_gettime(CLOCK_REALTIME, &ts);
    struct tm tm;
    gmtime_r(&ts.tv_sec, &tm);

    char msg[2048];
    va_list ap;
    va_start(ap, fmt);
    vsnprintf(msg, sizeof(msg), fmt, ap);
    va_end(ap);

    char buf[2304];
    int len = snprintf(buf, sizeof(buf),
        "%04d-%02d-%02dT%02d:%02d:%02d.%03ldZ [VK_LAYER_GREENBOOST] %s\n",
        tm.tm_year + 1900, tm.tm_mon + 1, tm.tm_mday,
        tm.tm_hour, tm.tm_min, tm.tm_sec, ts.tv_nsec / 1000000L, msg);
    if (len < 0 || len >= (int)sizeof(buf)) len = (int)sizeof(buf) - 1;

    if (g_gbvk_log_fd >= 0) {
        pthread_mutex_lock(&g_gbvk_log_mutex);
        GB_IGNORE_WRITE(write(g_gbvk_log_fd, buf, (size_t)len));
        pthread_mutex_unlock(&g_gbvk_log_mutex);
    }
    syslog(level, "[VK_LAYER_GREENBOOST] %s", msg);
    fwrite(buf, 1, (size_t)len, stderr);
}

#define gbvk_log(fmt, ...) gbvk_emit(LOG_INFO,  fmt, ##__VA_ARGS__)
#define gbvk_dbg(fmt, ...) do { if (g_gbvk_debug) gbvk_emit(LOG_DEBUG, fmt, ##__VA_ARGS__); } while(0)

/* ── Persistent /dev/greenboost fd ───────────────────────────────────── */

static int              g_gbvk_dev_fd = -1;
static pthread_once_t   g_gbvk_dev_once = PTHREAD_ONCE_INIT;
static pthread_mutex_t  g_gbvk_dev_mutex = PTHREAD_MUTEX_INITIALIZER;

static void gbvk_open_dev(void)
{
    g_gbvk_dev_fd = open("/dev/greenboost", O_RDWR | O_CLOEXEC);
    if (g_gbvk_dev_fd < 0)
        gbvk_log("open /dev/greenboost failed , T2/T3 fallback disabled");
    else
        gbvk_log("opened /dev/greenboost fd=%d", g_gbvk_dev_fd);
}

static int gbvk_dev_fd(void)
{
    pthread_once(&g_gbvk_dev_once, gbvk_open_dev);
    return g_gbvk_dev_fd;
}

/* ── Process-identity gate: is this process the actual game? ───────────
 *
 * VK_ADD_IMPLICIT_LAYER_PATH is prefix-wide, so this library loads into
 * EVERY Vulkan-capable process Proton starts in the Wine prefix , not just
 * the game. That was silently true from day one and harmless for most of
 * what this file does (queue-priority boosts, logging), but not for VRAM
 * inflation: inflate_heaps()/inflate_budget() report a virtual pool far
 * larger than physical VRAM to any caller, and one of those callers is
 * Proton's in-prefix `steam.exe` , Steamworks' own DRM / hardware-survey
 * client, not the game.
 *
 * Real incident, 2026-08-27: Final Fantasy VII Rebirth hung indefinitely
 * inside that steam.exe stage, before the game exe ever launched. A/B'd on
 * the real path: every Proton/Wine sync-and-scheduling knob the wrapper
 * sets was ruled out one at a time (still hung with all of them off); only
 * GREENBOOST_VULKAN_DISABLE=1 (which stops this whole library from doing
 * anything, in every process) fixed it. vulkan-layer.log showed exactly one
 * CreateInstance from an app identifying as 'unknown' at the hang point,
 * then nothing , consistent with Steamworks' own survey code choking on a
 * card that suddenly claims 4x its physical VRAM, not with a lock or a
 * crash in this library itself.
 *
 * The fix: only the process that IS the game gets the inflated numbers.
 * gb_proton_main.py now exports GREENBOOST_GAME_EXE with the basename of
 * the exe it is about to launch (it already knows this , same argv it
 * hands to Proton). Every other process in the prefix , steam.exe,
 * explorer.exe, svchost.exe, winedevice.exe, plugplay.exe , sees real,
 * unmodified GPU properties, exactly as if this layer were not loaded at
 * all for them.
 *
 * Match against /proc/self/comm rather than argv[0]: Wine sets the Linux
 * process comm to the Windows exe's basename (confirmed live , `steam.exe`,
 * `explorer.exe`, `svchost.exe` all show up verbatim in `ps` and in each
 * process's own /proc/self/comm), and comm is what is actually stable
 * across however this process was spawned. comm is TASK_COMM_LEN=16 bytes
 * (15 visible + NUL), so long exe names get silently truncated by the
 * kernel , compare only the overlap, case-insensitively (Windows filesystem
 * semantics), not full equality.
 *
 * If GREENBOOST_GAME_EXE is unset (a caller that isn't this wrapper, or a
 * game launched some other way), default to the OLD prefix-wide behaviour
 * , this gate narrows an already-existing capability, it must never turn
 * inflation off entirely for setups that predate this env var. */
static int g_gbvk_is_game = 1;   /* fail open: unset env var → old behaviour */
static pthread_once_t g_gbvk_is_game_once = PTHREAD_ONCE_INIT;

static void gbvk_detect_is_game(void)
{
    const char *want = getenv("GREENBOOST_GAME_EXE");
    if (!want || !want[0])
        return;                 /* g_gbvk_is_game stays 1 , see comment above */

    char comm[64] = {0};
    int fd = open("/proc/self/comm", O_RDONLY | O_CLOEXEC);
    if (fd < 0) {
        g_gbvk_is_game = 1;      /* can't tell , fail open, not closed */
        return;
    }
    ssize_t n = read(fd, comm, sizeof(comm) - 1);
    close(fd);
    if (n <= 0) {
        g_gbvk_is_game = 1;
        return;
    }
    while (n > 0 && (comm[n - 1] == '\n' || comm[n - 1] == '\r')) n--;
    comm[n] = '\0';

    size_t clen = strlen(comm);
    size_t wlen = strlen(want);
    size_t cmp  = clen < wlen ? clen : wlen;    /* comm may be truncated */
    g_gbvk_is_game = (cmp > 0 && strncasecmp(comm, want, cmp) == 0);

    if (!g_gbvk_is_game)
        gbvk_dbg("process '%s' is not the game ('%s') , VRAM inflation "
                 "inactive for this process", comm, want);
}

static int gbvk_is_game_process(void)
{
    pthread_once(&g_gbvk_is_game_once, gbvk_detect_is_game);
    return g_gbvk_is_game;
}

/* ── Allocation tracking hash table ──────────────────────────────────── */
/*
 * Open-addressed hash table mapping VkDeviceMemory (uint64_t) to overflow
 * allocation metadata. Simplified from the CUDA shim's 131K-slot design;
 * gaming workloads have far fewer large allocations (typically 10-200).
 *
 * Slot 0 = empty, Slot 1 = tombstone (deleted; never a valid VkDeviceMemory).
 * Fibonacci hash + linear probe. 8 striped locks.
 */
#define GBVK_HT_BITS    12
#define GBVK_HT_SIZE    (1u << GBVK_HT_BITS)   /* 4096 slots */
#define GBVK_HT_MASK    (GBVK_HT_SIZE - 1u)
#define GBVK_HT_LOCKS   8
#define GBVK_HT_TOMBSTONE ((uint64_t)1)

typedef struct {
    uint64_t  key;      /* VkDeviceMemory handle (0=empty, 1=tombstone)    */
    uint64_t  size;     /* allocation size in bytes                        */
    int       dma_fd;   /* dup'd DMA-BUF fd for madvise/evict calls        */
    int32_t   buf_id;   /* kernel IDR id returned in gb_alloc_req.fd       */
    uint8_t   tier;     /* 2 = T2 DDR, 3 = T3 NVMe                        */
    uint8_t   flags;    /* GB_ALLOC_* flags used at allocation time        */
    uint8_t   hot;      /* 1 = marked HOT (loading-screen working set)     */
    uint8_t   _pad;
} __attribute__((aligned(64))) GbVkHtEntry;

static GbVkHtEntry    g_gbvk_ht[GBVK_HT_SIZE];
static pthread_mutex_t g_gbvk_ht_locks[GBVK_HT_LOCKS] = {
    PTHREAD_MUTEX_INITIALIZER, PTHREAD_MUTEX_INITIALIZER,
    PTHREAD_MUTEX_INITIALIZER, PTHREAD_MUTEX_INITIALIZER,
    PTHREAD_MUTEX_INITIALIZER, PTHREAD_MUTEX_INITIALIZER,
    PTHREAD_MUTEX_INITIALIZER, PTHREAD_MUTEX_INITIALIZER,
};

static inline uint32_t gbvk_ht_hash(uint64_t key)
{
    return (uint32_t)((key * UINT64_C(0x9E3779B97F4A7C15)) >> (64 - GBVK_HT_BITS));
}

static void gbvk_ht_insert(uint64_t key, uint64_t size, int dma_fd,
                            int32_t buf_id, uint8_t tier, uint8_t flags)
{
    uint32_t h = gbvk_ht_hash(key);
    pthread_mutex_t *lk = &g_gbvk_ht_locks[h & (GBVK_HT_LOCKS - 1)];
    pthread_mutex_lock(lk);
    for (uint32_t i = 0; i < GBVK_HT_SIZE; i++) {
        uint32_t idx = (h + i) & GBVK_HT_MASK;
        if (!g_gbvk_ht[idx].key || g_gbvk_ht[idx].key == GBVK_HT_TOMBSTONE) {
            g_gbvk_ht[idx].key    = key;
            g_gbvk_ht[idx].size   = size;
            g_gbvk_ht[idx].dma_fd = dma_fd;
            g_gbvk_ht[idx].buf_id = buf_id;
            g_gbvk_ht[idx].tier   = tier;
            g_gbvk_ht[idx].flags  = flags;
            g_gbvk_ht[idx].hot    = 0;
            break;
        }
    }
    pthread_mutex_unlock(lk);
}

/* Returns a copy of the entry and tombstones the slot. Returns 0 if not found. */
static int gbvk_ht_remove(uint64_t key, GbVkHtEntry *out)
{
    uint32_t h = gbvk_ht_hash(key);
    pthread_mutex_t *lk = &g_gbvk_ht_locks[h & (GBVK_HT_LOCKS - 1)];
    pthread_mutex_lock(lk);
    int found = 0;
    for (uint32_t i = 0; i < GBVK_HT_SIZE; i++) {
        uint32_t idx = (h + i) & GBVK_HT_MASK;
        if (!g_gbvk_ht[idx].key) break;  /* empty slot , key absent */
        if (g_gbvk_ht[idx].key == key) {
            *out = g_gbvk_ht[idx];
            g_gbvk_ht[idx].key = GBVK_HT_TOMBSTONE;
            found = 1;
            break;
        }
    }
    pthread_mutex_unlock(lk);
    return found;
}

/* ── Session statistics ───────────────────────────────────────────────── */

static _Atomic uint32_t g_gbvk_t2_count  = 0;
static _Atomic uint32_t g_gbvk_t3_count  = 0;
static _Atomic uint64_t g_gbvk_t2_bytes  = 0;
static _Atomic uint64_t g_gbvk_t3_bytes  = 0;
static _Atomic uint32_t g_gbvk_oom_count = 0; /* allocs that failed all tiers */

/* ── Pool info cache (refreshed every 16 alloc attempts) ──────────────── */

#define GBVK_INFO_REFRESH_INTERVAL 16
static struct gb_info   g_gbvk_pool_info;
static _Atomic uint32_t g_gbvk_alloc_counter = 0;
static pthread_mutex_t  g_gbvk_info_mutex = PTHREAD_MUTEX_INITIALIZER;

static void gbvk_refresh_pool_info(void)
{
    int fd = gbvk_dev_fd();
    if (fd < 0) return;
    struct gb_info info;
    if (ioctl(fd, GB_IOCTL_GET_INFO, &info) == 0) {
        pthread_mutex_lock(&g_gbvk_info_mutex);
        g_gbvk_pool_info = info;
        pthread_mutex_unlock(&g_gbvk_info_mutex);

        /* Dynamically update heap inflation target when the pool cap changes
         * (e.g. user called GB_IOCTL_SET_POOL_CAP mid-session). This keeps
         * the size reported to games (T1 GPU VRAM + T2 DDR pool) accurate. */
        if (info.vram_physical_mb > 0 && info.max_pool_mb > 0) {
            uint64_t total = (info.vram_physical_mb + info.max_pool_mb)
                             * 1024ULL * 1024ULL;
            if (total != g_gbvk_virtual_vram_bytes) {
                g_gbvk_virtual_vram_bytes = total;
                gbvk_dbg("pool refresh: heap target updated → %llu GB "
                         "(T1=%llu MB + T2 cap=%llu MB)",
                         (unsigned long long)(total >> 30),
                         (unsigned long long)info.vram_physical_mb,
                         (unsigned long long)info.max_pool_mb);
            }
        }

        gbvk_dbg("pool refresh: T2 %llu/%llu MB (%s), T3 %llu MB",
                 (unsigned long long)info.allocated_mb,
                 (unsigned long long)info.max_pool_mb,
                 info.t2_pressure == GB_T2_PRESSURE_CRITICAL ? "CRITICAL" :
                 info.t2_pressure == GB_T2_PRESSURE_WARN     ? "WARN"     : "ok",
                 (unsigned long long)info.nvme_t3_allocated_mb);
    }
}

static uint32_t gbvk_t2_pressure(void)
{
    pthread_mutex_lock(&g_gbvk_info_mutex);
    uint32_t p = g_gbvk_pool_info.t2_pressure;
    pthread_mutex_unlock(&g_gbvk_info_mutex);
    return p;
}

/* ── Burst detector: mark loading-screen allocs HOT ─────────────────── */
/*
 * During a game's loading screen, the GPU gets a rapid burst of large texture
 * allocs. These become the game's working set and should stay in T2 until freed.
 * We detect the end of a burst (quiet for 2 seconds) and mark all burst allocs HOT
 * via GB_IOCTL_MADVISE so the kernel evicts them last under pressure.
 */
static _Atomic uint64_t g_gbvk_last_alloc_ms  = 0;
static _Atomic uint32_t g_gbvk_burst_active   = 0;
#define GBVK_BURST_QUIET_MS  2000

static uint64_t gbvk_now_ms(void)
{
    struct timespec ts;
    clock_gettime(CLOCK_MONOTONIC, &ts);
    return (uint64_t)ts.tv_sec * 1000 + ts.tv_nsec / 1000000;
}

/* Called after each successful T2/T3 alloc. Updates burst state.
 * On the first alloc of a new burst, promote this session to SESSION_ACTIVE
 * so the kernel kernel evicts its buffers last under AI-inference pressure. */
static void gbvk_burst_record(void)
{
    uint64_t now = gbvk_now_ms();
    uint64_t prev = atomic_load(&g_gbvk_last_alloc_ms);
    atomic_store(&g_gbvk_last_alloc_ms, now);

    int was_active = atomic_load(&g_gbvk_burst_active);
    if (!was_active || (now - prev >= GBVK_BURST_QUIET_MS)) {
        /* New burst starting , promote session to LRU head */
        atomic_store(&g_gbvk_burst_active, 1);
        int fd = gbvk_dev_fd();
        if (fd >= 0) {
            struct gb_session_req sr = { .pid = 0, .reserved = 0 };
            if (ioctl(fd, GB_IOCTL_SESSION_ACTIVE, &sr) == 0)
                gbvk_dbg("burst start: SESSION_ACTIVE sent");
            else
                gbvk_dbg("burst start: SESSION_ACTIVE ioctl failed (%d)", errno);
        }
    }
}

/*
 * Called every 16 allocs. If burst was active and now quiet for >=2s,
 * iterate hash table and mark all unfreed burst allocs HOT.
 */
static void gbvk_burst_check(void)
{
    if (!atomic_load(&g_gbvk_burst_active)) return;
    uint64_t now = gbvk_now_ms();
    uint64_t last = atomic_load(&g_gbvk_last_alloc_ms);
    if (now - last < GBVK_BURST_QUIET_MS) return;

    /* Burst ended , mark all tracked allocs HOT (working set). */
    int fd = gbvk_dev_fd();
    uint32_t marked = 0;
    for (uint32_t i = 0; i < GBVK_HT_SIZE; i++) {
        /* gbvk_ht_insert/remove pick their lock from hash(key) & mask, NOT
         * from the physical slot index , a key can land at any probed slot
         * (h, h+1, h+2, ...). Reading g_gbvk_ht[i].key here unlocked to pick
         * a candidate lock is therefore a TOCTOU: a concurrent remove(K1)
         * followed by insert(K2) landing at the same physical slot i can
         * swap in a key whose lock differs from the one just computed,
         * before this thread finishes acquiring it , leaving this loop
         * touching e->hot/e->buf_id under a mutex that doesn't actually
         * guard slot i's current occupant. Re-check under the lock and
         * retry with the fresh key's lock until they agree; once they do,
         * any insert/remove that could touch this slot's current key must
         * contend for this exact mutex, so it's now genuinely safe. */
        uint32_t lock_idx;
        pthread_mutex_t *lk;
        for (;;) {
            lock_idx = gbvk_ht_hash(g_gbvk_ht[i].key) & (GBVK_HT_LOCKS - 1);
            lk = &g_gbvk_ht_locks[lock_idx];
            pthread_mutex_lock(lk);
            if ((gbvk_ht_hash(g_gbvk_ht[i].key) & (GBVK_HT_LOCKS - 1)) == lock_idx)
                break;
            pthread_mutex_unlock(lk);
        }
        GbVkHtEntry *e = &g_gbvk_ht[i];
        if (e->key && e->key != GBVK_HT_TOMBSTONE && !e->hot) {
            if (fd >= 0) {
                struct gb_madvise_req m = { .buf_id = e->buf_id,
                                            .advise = GB_MADVISE_HOT };
                if (ioctl(fd, GB_IOCTL_MADVISE, &m) == 0) {
                    e->hot = 1;
                    marked++;
                } else {
                    /* Don't mark e->hot here , the kernel doesn't know about
                     * this buffer's temperature yet, so leave it eligible
                     * for retry on the next burst-end instead of silently
                     * stranding it as "hot" forever with the kernel never
                     * having been told. */
                    gbvk_dbg("burst end: MADVISE HOT failed for buf_id=%d (%d)",
                             e->buf_id, errno);
                }
            } else {
                /* No device fd , nothing for the kernel to track anyway;
                 * mark locally so we don't spin retrying every burst. */
                e->hot = 1;
                marked++;
            }
        }
        pthread_mutex_unlock(lk);
    }
    atomic_store(&g_gbvk_burst_active, 0);
    if (marked)
        gbvk_log("burst ended: marked %u allocs HOT (game working set)", marked);

    /* Keep session active during gameplay , the working set is in T2 and
     * must not be evicted by a concurrent AI inference session. */
    if (fd >= 0 && marked > 0) {
        struct gb_session_req sr = { .pid = 0, .reserved = 0 };
        if (ioctl(fd, GB_IOCTL_SESSION_ACTIVE, &sr) == 0)
            gbvk_dbg("burst ended: SESSION_ACTIVE maintained for gameplay working set");
        else
            gbvk_dbg("burst ended: SESSION_ACTIVE ioctl failed (%d)", errno);
    }
}

/* ── Runtime init ─────────────────────────────────────────────────────── */

__attribute__((constructor))
static void gbvk_init(void)
{
    /* Only activate when explicitly enabled. */
    const char *env = getenv("GREENBOOST_VULKAN");
    if (!env || env[0] != '1')
        return;

    /* Debug logging. */
    const char *dbg = getenv("GREENBOOST_VK_DEBUG");
    if (dbg && dbg[0] == '1') g_gbvk_debug = 1;

    /* PR-GGGG: SIGUSR1/2 dump handlers , best-effort, no error on failure. */
    gbvk_install_signal_handlers_once();

    /* PR-GGGG: start the periodic monitor thread at layer init so SIGUSR1
     * always has somewhere to deliver to.  Idle when no caches tracked. */
    gbvk_pipe_snapshot_thread_start_once();

    /* Configurable overflow threshold (apply before early returns). */
    const char *min_env = getenv("GREENBOOST_VK_OVERFLOW_MIN_MB");
    if (min_env) {
        long long mb = atoll(min_env);
        if (mb > 0)
            g_gbvk_overflow_min_bytes = (uint64_t)mb * 1024ULL * 1024ULL;
    }

    /* Configurable T3 threshold. */
    const char *t3_env = getenv("GREENBOOST_VK_T3_MIN_MB");
    if (t3_env) {
        long long mb = atoll(t3_env);
        if (mb > 0)
            g_gbvk_t3_min_bytes = (uint64_t)mb * 1024ULL * 1024ULL;
    }

    /* Headroom held back from the budget reported to games. Both knobs
     * accept 0: DIV=0 or MAX_MB=0 disables the reserve and restores the
     * pre-2026-08-20 gross-capacity behaviour, which is the A/B escape
     * hatch. Negative or unparseable values are ignored, not clamped ,
     * a typo should leave the default in place, not silently pick 0. */
    const char *rdiv_env = getenv("GREENBOOST_VK_BUDGET_RESERVE_DIV");
    if (rdiv_env) {
        long long d = atoll(rdiv_env);
        if (d >= 0)
            g_gbvk_budget_reserve_div = (uint64_t)d;
    }
    const char *rmax_env = getenv("GREENBOOST_VK_BUDGET_RESERVE_MAX_MB");
    if (rmax_env) {
        long long mb = atoll(rmax_env);
        if (mb >= 0)
            g_gbvk_budget_reserve_max = (uint64_t)mb * 1024ULL * 1024ULL;
    }

    /* PSO compile thread count , read GREENBOOST_SHADER_THREADS; default nproc-2. */
    {
        const char *st_env = getenv("GREENBOOST_SHADER_THREADS");
        if (st_env && st_env[0]) {
            long n = atol(st_env);
            if (n >= 1 && n <= 32)
                g_gbvk_shader_threads = (unsigned)n;
        }
        if (g_gbvk_shader_threads == 0) {
            long ncpu = sysconf(_SC_NPROCESSORS_ONLN);
            g_gbvk_shader_threads = (unsigned)(ncpu > 3 ? ncpu - 2 : 1);
            if (g_gbvk_shader_threads > 32) g_gbvk_shader_threads = 32;
        }
        gbvk_log("init: shader_threads=%u (GREENBOOST_SHADER_THREADS=%s)",
                 g_gbvk_shader_threads, st_env ? st_env : "<auto>");
    }

    /* Read virtual VRAM from kernel module sysfs. */
    int physical_gb = -1, virtual_gb = -1;
    char buf[32];
    FILE *f;

    f = fopen("/sys/module/greenboost/parameters/physical_vram_gb", "r");
    if (f) { if (fgets(buf, sizeof(buf), f)) physical_gb = atoi(buf); fclose(f); }

    f = fopen("/sys/module/greenboost/parameters/virtual_vram_gb", "r");
    if (f) { if (fgets(buf, sizeof(buf), f)) virtual_gb = atoi(buf); fclose(f); }

    if (physical_gb > 0 && virtual_gb > 0) {
        g_gbvk_virtual_vram_bytes =
            ((uint64_t)physical_gb + (uint64_t)virtual_gb) * 1024ULL * 1024ULL * 1024ULL;
        gbvk_log("init: sysfs physical=%d GB + virtual=%d GB = %llu GB | "
                 "T2 overflow>=%llu MB | T3 overflow>=%llu MB",
                 physical_gb, virtual_gb,
                 (unsigned long long)(g_gbvk_virtual_vram_bytes >> 30),
                 (unsigned long long)(g_gbvk_overflow_min_bytes >> 20),
                 (unsigned long long)(g_gbvk_t3_min_bytes >> 20));
        return;
    }

    /* Fallback 2: query GB_IOCTL_GET_INFO directly.
     * Works even when the kernel module sysfs params are unavailable (e.g.
     * Path B/C without greenboost.ko, or if sysfs read failed).
     * Exposes the real CUDA memory pool: T1 GPU VRAM + T2 DDR pool cap. */
    {
        int ifd = gbvk_dev_fd();
        if (ifd >= 0) {
            struct gb_info info;
            if (ioctl(ifd, GB_IOCTL_GET_INFO, &info) == 0 &&
                info.vram_physical_mb > 0 && info.max_pool_mb > 0) {
                g_gbvk_virtual_vram_bytes =
                    (info.vram_physical_mb + info.max_pool_mb) * 1024ULL * 1024ULL;
                gbvk_log("init: ioctl T1=%llu MB + T2 cap=%llu MB = %llu GB "
                         "| T2 overflow>=%llu MB | T3 overflow>=%llu MB",
                         (unsigned long long)info.vram_physical_mb,
                         (unsigned long long)info.max_pool_mb,
                         (unsigned long long)(g_gbvk_virtual_vram_bytes >> 30),
                         (unsigned long long)(g_gbvk_overflow_min_bytes >> 20),
                         (unsigned long long)(g_gbvk_t3_min_bytes >> 20));
                return;
            }
        }
    }

    /* Fallback 3: env var override (manual / testing). */
    const char *vram_env = getenv("GREENBOOST_VIRTUAL_VRAM_MB");
    if (vram_env) {
        long long mb = atoll(vram_env);
        if (mb > 0) {
            g_gbvk_virtual_vram_bytes = (uint64_t)mb * 1024ULL * 1024ULL;
            gbvk_log("init: GREENBOOST_VIRTUAL_VRAM_MB=%lld MB", mb);
            return;
        }
    }

    gbvk_log("init: kernel params unavailable , heap inflation disabled, "
             "T2/T3 overflow still available on OOM");
}

/* ── PR-GGGG: SIGUSR1 stats dump ────────────────────────────────────────
 *
 * A user can run `kill -USR1 <pid>` against the game (or any process
 * loading this layer) to make us emit a one-line snapshot of every
 * running counter , shader compiles, frame pacing, memory tiers.  The
 * signal handler itself is intentionally minimal: it sets a flag the
 * snapshot worker picks up on its next 1-second tick, which keeps us
 * fully async-signal-safe.
 *
 * SIGUSR2: same dump, plus an immediate VkPipelineCache snapshot to disk.
 */

static _Atomic int g_gbvk_dump_requested = 0;   /* 1 = USR1, 2 = USR2 */

static void gbvk_dump_stats(void)
{
    uint32_t t2 = atomic_load(&g_gbvk_t2_count);
    uint32_t t3 = atomic_load(&g_gbvk_t3_count);
    uint64_t t2b = atomic_load(&g_gbvk_t2_bytes);
    uint64_t t3b = atomic_load(&g_gbvk_t3_bytes);
    uint32_t oom = atomic_load(&g_gbvk_oom_count);

    uint64_t gpc  = atomic_load_explicit(&g_gbvk_pipe_count_g,  memory_order_relaxed);
    uint64_t cpc  = atomic_load_explicit(&g_gbvk_pipe_count_c,  memory_order_relaxed);
    uint64_t tot  = atomic_load_explicit(&g_gbvk_pipe_total_ns, memory_order_relaxed);
    uint64_t slow = atomic_load_explicit(&g_gbvk_pipe_slow_ns,  memory_order_relaxed);

    uint64_t pcount   = atomic_load_explicit(&g_gbvk_present_count,    memory_order_relaxed);
    uint64_t ptotal   = atomic_load_explicit(&g_gbvk_present_total_ns, memory_order_relaxed);
    uint64_t pworst   = atomic_load_explicit(&g_gbvk_present_worst_ns, memory_order_relaxed);
    uint64_t phitches = atomic_load_explicit(&g_gbvk_present_hitches,  memory_order_relaxed);

    double mean_ms = (pcount > 1) ? (double)ptotal / (pcount - 1) / 1.0e6 : 0.0;
    double fps     = (mean_ms > 0.0) ? (1000.0 / mean_ms) : 0.0;

    /* PR-P1: snapshot ring buffer, sort a copy, compute P99 (1% low FPS). */
    double p1_fps  = 0.0;
    {
        uint64_t filled = atomic_load_explicit(&g_gbvk_ft_filled, memory_order_relaxed);
        uint32_t count  = (uint32_t)(filled < GBVK_FTBUF_SIZE ? filled : GBVK_FTBUF_SIZE);
        if (count >= 20) {
            /* Stack-allocate a copy; insertion sort is fine for 512 items. */
            uint64_t tmp[GBVK_FTBUF_SIZE];
            uint64_t head = atomic_load_explicit(&g_gbvk_ft_head, memory_order_relaxed);
            for (uint32_t i = 0; i < count; i++) {
                uint32_t idx = (uint32_t)((head - count + i) & (GBVK_FTBUF_SIZE - 1));
                tmp[i] = g_gbvk_ft_buf[idx];
            }
            /* Insertion sort ascending. */
            for (uint32_t i = 1; i < count; i++) {
                uint64_t key = tmp[i];
                int32_t j = (int32_t)i - 1;
                while (j >= 0 && tmp[j] > key) { tmp[j + 1] = tmp[j]; j--; }
                tmp[j + 1] = key;
            }
            /* P99 frame time (99th percentile) → 1% low FPS. */
            uint32_t idx99 = (uint32_t)(count * 99 / 100);
            if (idx99 >= count) idx99 = count - 1;
            double p99_ms = (double)tmp[idx99] / 1.0e6;
            if (p99_ms > 0.0) p1_fps = 1000.0 / p99_ms;
        }
    }

    /* Human-readable dump for the log file / developers. */
    gbvk_log("[dump] T2=%u/%lluMB T3=%u/%lluMB oom=%u | PSO=%llug+%lluc "
             "wall=%.2fs slowest=%.1fms | present=%llu fps=%.1f "
             "worst=%.1fms p1_fps=%.1f hitches=%llu",
             t2, (unsigned long long)(t2b >> 20),
             t3, (unsigned long long)(t3b >> 20),
             oom,
             (unsigned long long)gpc, (unsigned long long)cpc,
             (double)tot / 1.0e9, (double)slow / 1.0e6,
             (unsigned long long)pcount, fps,
             (double)pworst / 1.0e6, p1_fps,
             (unsigned long long)phitches);

    /* Machine-parseable line consumed by the GreenBoost Gaming Suite UI. */
    gbvk_log("GreenBoost|fps=%.1f|mean_ms=%.2f|p1_fps=%.1f|worst_ms=%.1f"
             "|hitches=%llu|t2_mb=%llu|t3_mb=%llu|oom=%u"
             "|pso_compiles=%llu|present_count=%llu",
             fps, mean_ms, p1_fps, (double)pworst / 1.0e6,
             (unsigned long long)phitches,
             (unsigned long long)(t2b >> 20),
             (unsigned long long)(t3b >> 20),
             oom,
             (unsigned long long)(gpc + cpc),
             (unsigned long long)pcount);
}

static void gbvk_sigusr_handler(int signo)
{
    atomic_store_explicit(&g_gbvk_dump_requested,
                          (signo == SIGUSR2) ? 2 : 1,
                          memory_order_relaxed);
}

static void gbvk_install_signal_handlers_init(void)
{
    struct sigaction sa = {0};
    sa.sa_handler = gbvk_sigusr_handler;
    sigemptyset(&sa.sa_mask);
    /* SA_RESTART so blocked syscalls don't fail with EINTR. */
    sa.sa_flags = SA_RESTART;
    sigaction(SIGUSR1, &sa, NULL);
    sigaction(SIGUSR2, &sa, NULL);
}

static void gbvk_install_signal_handlers_once(void)
{
    static pthread_once_t once = PTHREAD_ONCE_INIT;
    pthread_once(&once, gbvk_install_signal_handlers_init);
}

/* ── Process-exit cleanup ─────────────────────────────────────────────── */

/* Forward declarations , periodic pipeline-cache snapshot state lives
 * below but gbvk_fini references it.  Tentative-definition style: C lets
 * us write `static T x;` here and `static T x = init;` later in the same
 * TU without conflict. */
static pthread_t       g_pipe_snap_thread;
static int             g_pipe_snap_thread_started;
static _Atomic int     g_pipe_snap_stop;

__attribute__((destructor))
static void gbvk_fini(void)
{
    /* PR-GGGG: stop the periodic snapshot thread first so it can't race
     * with the rest of cleanup.  pthread_join with a short timeout , we
     * already woke it via the stop flag and it polls every second. */
    if (g_pipe_snap_thread_started) {
        atomic_store_explicit(&g_pipe_snap_stop, 1, memory_order_relaxed);
        pthread_join(g_pipe_snap_thread, NULL);
        g_pipe_snap_thread_started = 0;
    }

    uint32_t t2 = atomic_load(&g_gbvk_t2_count);
    uint32_t t3 = atomic_load(&g_gbvk_t3_count);
    uint64_t t2b = atomic_load(&g_gbvk_t2_bytes);
    uint64_t t3b = atomic_load(&g_gbvk_t3_bytes);
    uint32_t oom = atomic_load(&g_gbvk_oom_count);

    if (t2 || t3 || oom)
        gbvk_log("session end: T2=%u allocs (%llu MB) T3=%u allocs (%llu MB) failed=%u",
                 t2, (unsigned long long)(t2b >> 20),
                 t3, (unsigned long long)(t3b >> 20),
                 oom);

    /* PR-CCC: only run cleanup if we actually opened the device during this
     * session.  The previous code read the raw `g_gbvk_dev_fd` global, which
     * is fine in the common case (pthread_once already fired), but if the
     * game never spilled to T2/T3 we'd leave a stale -1.  Use the accessor so
     * the check is unambiguous, and skip lazy-opening just to do cleanup. */
    int fd = g_gbvk_dev_fd;
    if (fd >= 0) {
        /* Demote session priority before releasing , signals to any concurrent
         * session that we are no longer competing for T2 space. */
        struct gb_session_req sr = { .pid = 0, .reserved = 0 };
        if (ioctl(fd, GB_IOCTL_SESSION_IDLE, &sr) != 0)
            gbvk_dbg("session end: SESSION_IDLE ioctl failed (%d)", errno);
        /* Release all buffers owned by this process , covers the rare case
         * where we tracked a fd but the dup leaked (e.g. a vkAllocateMemory
         * that succeeded inside the driver but failed to insert in our hash
         * table).  Idempotent , kernel returns 0 with no buffers to free.
         * A failure here means buffers this process owned may leak in the
         * kernel's own accounting until its own PID-exit cleanup reclaims
         * them , worth a log line since that's exactly the kind of gap that
         * looks like "GPU/DDR pool slowly fills over many sessions" from
         * the outside with nothing pointing at the cause. */
        struct gb_release_pid_req r = { .pid = 0 };
        if (ioctl(fd, GB_IOCTL_RELEASE_PID, &r) != 0)
            gbvk_log("session end: RELEASE_PID ioctl failed (%d) , buffers may "
                     "leak until kernel PID-exit cleanup reclaims them", errno);
        close(fd);
        g_gbvk_dev_fd = -1;
    }
}

/* ── Per-instance state ───────────────────────────────────────────────── */

typedef struct {
    VkInstance                               instance;
    PFN_vkGetInstanceProcAddr                next_gipa;
    PFN_vkDestroyInstance                    next_destroy_instance;
    PFN_vkGetPhysicalDeviceMemoryProperties  next_get_mem_props;
    PFN_vkGetPhysicalDeviceMemoryProperties2 next_get_mem_props2;
} GbInstData;

/* PR-GGGG: vkEnumerateDeviceExtensionProperties, resolved at CreateInstance.
 *
 * It cannot be resolved inside gbvk_CreateDevice: vkGetInstanceProcAddr
 * returns NULL for it when passed VK_NULL_HANDLE, and CreateDevice is handed
 * a VkPhysicalDevice, not the VkInstance it came from.  Resolving it once at
 * CreateInstance, where a real instance handle exists, is what the rest of
 * this file already does for the other instance-level entry points.
 *
 * A process with two Vulkan instances would have the second overwrite the
 * first.  Both are valid pointers into the same loader/driver for the same
 * physical devices, so the query still answers correctly; and if it were ever
 * wrong, CreateDevice falls back to creating the device without our
 * extensions rather than failing.
 */
static PFN_vkEnumerateDeviceExtensionProperties g_next_enum_dev_ext = NULL;

/* ── Per-device state ─────────────────────────────────────────────────── */

typedef struct GbDevData_ {
    VkDevice                             device;
    PFN_vkGetDeviceProcAddr              next_gdpa;
    PFN_vkDestroyDevice                  next_destroy_device;
    PFN_vkAllocateMemory                 next_alloc_mem;
    PFN_vkFreeMemory                     next_free_mem;
    PFN_vkGetMemoryFdPropertiesKHR       next_get_mem_fd_props;
    VkPhysicalDeviceMemoryProperties     mem_props; /* cached for overflow path */
    /* PR-GGGG: pipeline cache pre-warm + shader compile telemetry. */
    PFN_vkCreatePipelineCache            next_create_pipeline_cache;
    PFN_vkDestroyPipelineCache           next_destroy_pipeline_cache;
    PFN_vkGetPipelineCacheData           next_get_pipeline_cache_data;
    PFN_vkMergePipelineCaches            next_merge_pipeline_caches;
    PFN_vkCreateGraphicsPipelines        next_create_graphics_pipelines;
    PFN_vkCreateComputePipelines         next_create_compute_pipelines;
    PFN_vkQueuePresentKHR                next_queue_present_khr;
    /* PR-GGGG: runtime priority , only set when VK_EXT_pageable_device_local_memory
     * was enabled by the application. */
    PFN_vkSetDeviceMemoryPriorityEXT     next_set_device_memory_priority;
    /* PR-GGGG: bind hooks for image-on-spill detection. */
    PFN_vkBindImageMemory                next_bind_image_memory;
    PFN_vkBindImageMemory2               next_bind_image_memory2;
    /* PR-GGGG: device identity captured at vkCreateDevice so we can sanity-
     * check the on-disk pipeline cache header before injecting it. */
    uint32_t                             vendor_id;
    uint32_t                             device_id;
    uint8_t                              cache_uuid[16];
    int                                  identity_valid;
    /* PR-GGGG: NIS post-process state.  Lazy-init on first vkCreateSwapchainKHR
     * when GREENBOOST_NIS=1.  Everything below is created once per device. */
    int                                  nis_initialised;
    int                                  nis_failed;     /* sticky: skip retries */
    int                                  nis_use_upscale; /* 1 = upscale, 0 = sharpen */
    float                                nis_scale;       /* GREENBOOST_NIS_SCALE */
    VkShaderModule                       nis_module;
    VkDescriptorSetLayout                nis_dsl;
    VkPipelineLayout                     nis_player;
    VkPipeline                           nis_pipeline;
    VkSampler                            nis_sampler;
    /* Function pointers needed for the post-process pass. */
    PFN_vkCreateSwapchainKHR             next_create_swapchain;
    PFN_vkDestroySwapchainKHR            next_destroy_swapchain;
    PFN_vkGetSwapchainImagesKHR          next_get_swapchain_images;
    PFN_vkCreateShaderModule             next_create_shader_module;
    PFN_vkDestroyShaderModule            next_destroy_shader_module;
    PFN_vkCreateDescriptorSetLayout      next_create_dsl;
    PFN_vkDestroyDescriptorSetLayout     next_destroy_dsl;
    PFN_vkCreatePipelineLayout           next_create_player;
    PFN_vkDestroyPipelineLayout          next_destroy_player;
    PFN_vkDestroyPipeline                next_destroy_pipeline;
    PFN_vkCreateSampler                  next_create_sampler;
    PFN_vkDestroySampler                 next_destroy_sampler;
    /* PR-GGGG: NIS dispatch , resource alloc + command-buffer + queue submit. */
    PFN_vkCreateImage                    next_create_image;
    PFN_vkDestroyImage                   next_destroy_image;
    PFN_vkGetImageMemoryRequirements     next_get_image_mem_req;
    PFN_vkBindImageMemory                next_bind_image_memory_call;
    PFN_vkCreateImageView                next_create_image_view;
    PFN_vkDestroyImageView               next_destroy_image_view;
    PFN_vkCreateDescriptorPool           next_create_desc_pool;
    PFN_vkDestroyDescriptorPool          next_destroy_desc_pool;
    PFN_vkAllocateDescriptorSets         next_alloc_desc_sets;
    PFN_vkUpdateDescriptorSets           next_update_desc_sets;
    PFN_vkCreateBuffer                   next_create_buffer;
    PFN_vkDestroyBuffer                  next_destroy_buffer;
    PFN_vkGetBufferMemoryRequirements    next_get_buf_mem_req;
    PFN_vkBindBufferMemory               next_bind_buf_memory;
    PFN_vkMapMemory                      next_map_memory;
    PFN_vkUnmapMemory                    next_unmap_memory;
    PFN_vkCreateCommandPool              next_create_cmd_pool;
    PFN_vkDestroyCommandPool             next_destroy_cmd_pool;
    PFN_vkAllocateCommandBuffers         next_alloc_cmd_buffers;
    PFN_vkBeginCommandBuffer             next_begin_cmd_buffer;
    PFN_vkEndCommandBuffer               next_end_cmd_buffer;
    PFN_vkCmdPipelineBarrier             next_cmd_pipeline_barrier;
    PFN_vkCmdCopyImage                   next_cmd_copy_image;
    PFN_vkCmdBindPipeline                next_cmd_bind_pipeline;
    PFN_vkCmdBindDescriptorSets          next_cmd_bind_desc_sets;
    PFN_vkCmdPushConstants               next_cmd_push_constants;
    PFN_vkCmdDispatch                    next_cmd_dispatch;
    PFN_vkCreateSemaphore                next_create_semaphore;
    PFN_vkDestroySemaphore               next_destroy_semaphore;
    PFN_vkQueueSubmit                    next_queue_submit;
    uint32_t                             nis_queue_family;
    /* A7: VK_NV_low_latency2 / Reflex latency markers.
     * Gate: GREENBOOST_REFLEX=1.  Resolved once at CreateDevice when the
     * extension is enabled by the application. */
    PFN_vkSetLatencySleepModeNV          next_set_latency_sleep_mode;
    PFN_vkLatencySleepNV                 next_latency_sleep;
    PFN_vkSetLatencyMarkerNV             next_set_latency_marker;
    PFN_vkGetLatencyTimingsNV            next_get_latency_timings;
    PFN_vkAcquireNextImageKHR            next_acquire_image;
    uint64_t                             reflex_frame_id;   /* monotonic frame counter */
} GbDevData;

static GbInstData       g_inst[GBVK_MAX_INSTANCES];
static GbDevData        g_dev[GBVK_MAX_DEVICES];
static pthread_mutex_t  g_mutex = PTHREAD_MUTEX_INITIALIZER;

/* ── Table helpers (all called under g_mutex) ─────────────────────────── */

static GbInstData *inst_alloc(VkInstance h)
{
    for (int i = 0; i < GBVK_MAX_INSTANCES; i++)
        if (!g_inst[i].instance) { g_inst[i].instance = h; return &g_inst[i]; }
    return NULL;
}
static GbInstData *inst_find(VkInstance h)
{
    for (int i = 0; i < GBVK_MAX_INSTANCES; i++)
        if (g_inst[i].instance == h) return &g_inst[i];
    return NULL;
}
static void inst_free(VkInstance h)
{
    for (int i = 0; i < GBVK_MAX_INSTANCES; i++)
        if (g_inst[i].instance == h) { memset(&g_inst[i], 0, sizeof g_inst[i]); return; }
}

static GbDevData *dev_alloc(VkDevice h)
{
    for (int i = 0; i < GBVK_MAX_DEVICES; i++)
        if (!g_dev[i].device) { g_dev[i].device = h; return &g_dev[i]; }
    return NULL;
}
static GbDevData *dev_find(VkDevice h)
{
    for (int i = 0; i < GBVK_MAX_DEVICES; i++)
        if (g_dev[i].device == h) return &g_dev[i];
    return NULL;
}
static void dev_free(VkDevice h)
{
    for (int i = 0; i < GBVK_MAX_DEVICES; i++)
        if (g_dev[i].device == h) { memset(&g_dev[i], 0, sizeof g_dev[i]); return; }
}

/* ── Helper: inflate device-local heaps ──────────────────────────────── */

static void inflate_heaps(VkPhysicalDeviceMemoryProperties *p)
{
    if (!g_gbvk_virtual_vram_bytes) return;
    if (!gbvk_is_game_process()) return;   /* see gbvk_is_game_process() comment */
    for (uint32_t i = 0; i < p->memoryHeapCount; i++) {
        if ((p->memoryHeaps[i].flags & VK_MEMORY_HEAP_DEVICE_LOCAL_BIT) &&
            p->memoryHeaps[i].size < g_gbvk_virtual_vram_bytes)
            p->memoryHeaps[i].size = g_gbvk_virtual_vram_bytes;
    }
}

/* ── Helper: hold back headroom from a reported budget ───────────────── */
/*
 * Rule #1 keeps ~10% of physical VRAM free so the system never collapses
 * under memory pressure. The same argument applies to the VIRTUAL pool we
 * report to games, and nothing applied it: heapBudget[] was set to the gross
 * T1+T2 capacity, so a game that allocates right up to what we told it drives
 * the T2 pool to 100% with no margin at all. That matters most in exactly the
 * case GreenBoost exists to handle , a served model already holding VRAM on
 * the same card, which is what the gaming_inference_contention segment is for.
 *
 * A CAPPED FRACTION, not a flat percentage. A flat 10% of a 76 GB virtual
 * pool would hold back 7.6 GB of DDR for no reason; the risk being hedged
 * against does not grow with pool size the way a percentage does. So:
 *
 *     reserve = min(capacity / DIV, MAX)
 *
 * 12.5% capped at 1 GiB by default: a 12 GB card reserves 1 GiB, and a 76 GB
 * virtual pool also reserves 1 GiB rather than 9.5 GB.
 *
 * Derived, never a literal , the inputs are the detected pool sizes, so this
 * stays correct on hardware that is not this box.
 *
 * Set GREENBOOST_VK_BUDGET_RESERVE_MAX_MB=0 to disable entirely and get the
 * previous gross-capacity behaviour back (A/B escape hatch).
 */
static uint64_t gbvk_reserved_budget(uint64_t capacity)
{
    if (!capacity || !g_gbvk_budget_reserve_max || !g_gbvk_budget_reserve_div)
        return capacity;

    uint64_t reserve = capacity / g_gbvk_budget_reserve_div;
    if (reserve > g_gbvk_budget_reserve_max)
        reserve = g_gbvk_budget_reserve_max;

    /* Never report zero (or a uselessly tiny budget) on a small pool , a
     * game that is told it has nothing behaves far worse than one told it
     * has a little. Keep at least half of whatever we were given. */
    if (reserve > capacity / 2)
        reserve = capacity / 2;

    return capacity - reserve;
}

/* ── Helper: inflate VK_EXT_memory_budget heapBudget[] ───────────────── */
/*
 * DXVK and VKD3D-Proton query VK_EXT_memory_budget (heapBudget[]) to decide
 * how much VRAM they can actually allocate , heap size alone is not enough.
 * Without this, they see the real ~12 GB physical budget and cap textures there
 * despite the inflated heap size. We overwrite heapBudget[] for device-local
 * heaps to match g_gbvk_virtual_vram_bytes so the full T1+T2 pool is usable.
 * heapUsage[] is left unchanged (reflects real driver usage, keeps OOM sane)
 * , a game computing free = budget - usage therefore still sees whatever
 * other processes (a served model, the compositor) are really holding.
 *
 * The budget we write is the pool capacity MINUS headroom , see
 * gbvk_reserved_budget(). Reporting the gross capacity, as this did before,
 * invited a game to fill T2 to exactly 100%.
 */
static void inflate_budget(VkPhysicalDeviceMemoryProperties2 *p)
{
    if (!g_gbvk_virtual_vram_bytes) return;
    if (!gbvk_is_game_process()) return;   /* see gbvk_is_game_process() comment */

    VkBaseOutStructure *chain = (VkBaseOutStructure *)p->pNext;
    while (chain) {
        if (chain->sType == VK_STRUCTURE_TYPE_PHYSICAL_DEVICE_MEMORY_BUDGET_PROPERTIES_EXT) {
            VkPhysicalDeviceMemoryBudgetPropertiesEXT *budget =
                (VkPhysicalDeviceMemoryBudgetPropertiesEXT *)chain;
            uint64_t reported = gbvk_reserved_budget(g_gbvk_virtual_vram_bytes);
            for (uint32_t i = 0; i < p->memoryProperties.memoryHeapCount; i++) {
                if (p->memoryProperties.memoryHeaps[i].flags & VK_MEMORY_HEAP_DEVICE_LOCAL_BIT)
                    budget->heapBudget[i] = reported;
            }
            gbvk_dbg("inflate_budget: heapBudget[] = %llu MB "
                     "(pool %llu MB - %llu MB headroom) for device-local heaps",
                     (unsigned long long)(reported >> 20),
                     (unsigned long long)(g_gbvk_virtual_vram_bytes >> 20),
                     (unsigned long long)((g_gbvk_virtual_vram_bytes - reported) >> 20));
            break;
        }
        chain = (VkBaseOutStructure *)chain->pNext;
    }
}

/* ── Helper: attempt one DMA-BUF overflow alloc ─────────────────────── */
/* Forward declaration , defined just below gbvk_try_dmabuf_alloc, but used
 * from inside it for the T2/T3 priority chain. */
static const void *gbvk_chain_priority(const void                            *orig_pNext,
                                       VkMemoryPriorityAllocateInfoEXT       *storage,
                                       float                                  priority);

/*
 * Allocates a GreenBoost kernel buffer and imports it as a Vulkan device memory
 * object using VK_KHR_external_memory_fd. Used for both T2 and T3 paths.
 *
 * Returns VK_SUCCESS and fills *pMemory on success.
 * Returns the original OOM result on any failure.
 */
static VkResult gbvk_try_dmabuf_alloc(
    VkDevice                            device,
    const VkMemoryAllocateInfo         *pAllocInfo,
    const VkAllocationCallbacks        *pAllocator,
    VkDeviceMemory                     *pMemory,
    PFN_vkAllocateMemory                fn_alloc,
    PFN_vkGetMemoryFdPropertiesKHR      fn_fd_props,
    const VkPhysicalDeviceMemoryProperties *mem_props,
    uint32_t                            alloc_flags,
    uint8_t                             tier)
{
    int fd = gbvk_dev_fd();
    if (fd < 0) return VK_ERROR_OUT_OF_DEVICE_MEMORY;

    struct gb_alloc_req req;
    memset(&req, 0, sizeof req);
    req.size  = pAllocInfo->allocationSize;
    req.flags = alloc_flags;

    if (ioctl(fd, GB_IOCTL_ALLOC, &req) < 0)
        return VK_ERROR_OUT_OF_DEVICE_MEMORY;

    /* Query memory types compatible with this DMA-BUF. */
    VkMemoryFdPropertiesKHR fd_props = {
        .sType = VK_STRUCTURE_TYPE_MEMORY_FD_PROPERTIES_KHR,
        .pNext = NULL,
    };
    if (!fn_fd_props ||
        fn_fd_props(device, VK_EXTERNAL_MEMORY_HANDLE_TYPE_DMA_BUF_BIT_EXT,
                    req.fd, &fd_props) != VK_SUCCESS) {
        close(req.fd);
        return VK_ERROR_OUT_OF_DEVICE_MEMORY;
    }

    /* Prefer host-cached+coherent; settle for host-visible+coherent. */
    uint32_t fallback_type = UINT32_MAX;
    for (uint32_t i = 0; i < mem_props->memoryTypeCount; i++) {
        if (!(fd_props.memoryTypeBits & (1u << i))) continue;
        VkMemoryPropertyFlags f = mem_props->memoryTypes[i].propertyFlags;
        if ((f & VK_MEMORY_PROPERTY_HOST_VISIBLE_BIT) &&
            (f & VK_MEMORY_PROPERTY_HOST_COHERENT_BIT)) {
            fallback_type = i;
            if (f & VK_MEMORY_PROPERTY_HOST_CACHED_BIT) break;
        }
    }
    if (fallback_type == UINT32_MAX) {
        close(req.fd);
        return VK_ERROR_OUT_OF_DEVICE_MEMORY;
    }

    VkImportMemoryFdInfoKHR import_info = {
        .sType      = VK_STRUCTURE_TYPE_IMPORT_MEMORY_FD_INFO_KHR,
        .pNext      = NULL,
        .handleType = VK_EXTERNAL_MEMORY_HANDLE_TYPE_DMA_BUF_BIT_EXT,
        .fd         = req.fd,
    };
    /* PR-GGGG: chain VK_EXT_memory_priority so the driver evicts T2/T3
     * spill before T1 working-set pages under pressure. */
    VkMemoryPriorityAllocateInfoEXT prio_storage;
    const float tier_priority = (tier == 2) ? 0.30f : 0.05f;
    import_info.pNext = gbvk_chain_priority(import_info.pNext,
                                            &prio_storage, tier_priority);
    VkMemoryAllocateInfo fallback = {
        .sType           = VK_STRUCTURE_TYPE_MEMORY_ALLOCATE_INFO,
        .pNext           = &import_info,
        .allocationSize  = pAllocInfo->allocationSize,
        .memoryTypeIndex = fallback_type,
    };

    VkResult res = fn_alloc(device, &fallback, pAllocator, pMemory);
    if (res != VK_SUCCESS) {
        close(req.fd);
        return VK_ERROR_OUT_OF_DEVICE_MEMORY;
    }

    /* fd ownership transferred to Vulkan driver on success. Dup for our tracking. */
    int track_fd = dup(req.fd);

    gbvk_ht_insert((uint64_t)(uintptr_t)*pMemory,
                   pAllocInfo->allocationSize,
                   track_fd, req.fd /*buf_id = kernel IDR fd*/,
                   tier, (uint8_t)alloc_flags);

    /* PR-GGGG: VK_EXT_pageable_device_local_memory , set runtime priority so
     * the driver evicts T2/T3 spill before T1 working set even after alloc. */
    PFN_vkSetDeviceMemoryPriorityEXT fn_runtime_prio = NULL;
    pthread_mutex_lock(&g_mutex);
    GbDevData *dd = dev_find(device);
    if (dd) fn_runtime_prio = dd->next_set_device_memory_priority;
    pthread_mutex_unlock(&g_mutex);
    if (fn_runtime_prio) {
        fn_runtime_prio(device, *pMemory, tier_priority);
    }

    if (tier == 2) {
        atomic_fetch_add(&g_gbvk_t2_count, 1);
        atomic_fetch_add(&g_gbvk_t2_bytes, pAllocInfo->allocationSize);
    } else {
        atomic_fetch_add(&g_gbvk_t3_count, 1);
        atomic_fetch_add(&g_gbvk_t3_bytes, pAllocInfo->allocationSize);
    }
    gbvk_burst_record();

    gbvk_log("AllocateMemory: T%u DMA-BUF OK , %llu MB (type %u, flags=0x%x)",
             tier, (unsigned long long)(pAllocInfo->allocationSize >> 20),
             fallback_type, alloc_flags);
    return VK_SUCCESS;
}

/* ── PR-GGGG: VK_EXT_memory_priority chaining helper ──────────────────── */
/*
 * Adds a VkMemoryPriorityAllocateInfoEXT to an allocation's pNext chain
 * if (1) the app didn't already provide one and (2) the env opt-out is
 * not set.  Drivers that don't implement the extension silently ignore
 * the struct (per Vulkan pNext semantics) , no compatibility risk.
 *
 * Priority semantics from VK_EXT_memory_priority:
 *   1.0  → highest, driver tries hardest to keep resident
 *   0.0  → lowest, evict first under pressure
 *
 * GreenBoost mapping:
 *   T1 (real VRAM)   → 1.0   (game working set, must stay resident)
 *   T2 (DMA-BUF DDR) → 0.30  (acceptable to spill, still faster than T3)
 *   T3 (NVMe spill)  → 0.05  (evict first under pressure)
 *
 * Caller passes a stack-allocated VkMemoryPriorityAllocateInfoEXT for the
 * helper to populate; helper returns the new pNext head.  Returns the
 * original pNext unchanged if the chain already contains a priority struct
 * or the user has set GREENBOOST_VK_MEMORY_PRIORITY=0.
 */
static const void *gbvk_chain_priority(const void                            *orig_pNext,
                                       VkMemoryPriorityAllocateInfoEXT       *storage,
                                       float                                  priority)
{
    static int opt_out_cached = -1;
    if (opt_out_cached < 0) {
        const char *v = getenv("GREENBOOST_VK_MEMORY_PRIORITY");
        opt_out_cached = (v && strcmp(v, "0") == 0) ? 1 : 0;
    }
    if (opt_out_cached) return orig_pNext;

    /* Walk the existing chain , if there's already a priority struct, the
     * app knows what it wants; don't second-guess it. */
    const VkBaseInStructure *p = (const VkBaseInStructure *)orig_pNext;
    while (p) {
        if (p->sType == VK_STRUCTURE_TYPE_MEMORY_PRIORITY_ALLOCATE_INFO_EXT)
            return orig_pNext;
        p = p->pNext;
    }

    storage->sType    = VK_STRUCTURE_TYPE_MEMORY_PRIORITY_ALLOCATE_INFO_EXT;
    storage->pNext    = orig_pNext;
    storage->priority = priority;
    return storage;
}

/* ── Hook: vkAllocateMemory ────────────────────────────────────────────── */

static VKAPI_ATTR VkResult VKAPI_CALL
gbvk_AllocateMemory(VkDevice                       device,
                    const VkMemoryAllocateInfo    *pAllocInfo,
                    const VkAllocationCallbacks   *pAllocator,
                    VkDeviceMemory                *pMemory)
{
    /* Snapshot function pointers and mem_props under lock; call outside. */
    pthread_mutex_lock(&g_mutex);
    GbDevData *d = dev_find(device);
    PFN_vkAllocateMemory           fn_alloc    = d ? d->next_alloc_mem       : NULL;
    PFN_vkGetMemoryFdPropertiesKHR fn_fd_props = d ? d->next_get_mem_fd_props : NULL;
    VkPhysicalDeviceMemoryProperties mem_props = d ? d->mem_props
                                                   : (VkPhysicalDeviceMemoryProperties){};
    pthread_mutex_unlock(&g_mutex);

    if (!fn_alloc) return VK_ERROR_DEVICE_LOST;

    /* Refresh pool info every 16 alloc attempts (piggyback, no background thread). */
    uint32_t cnt = atomic_fetch_add(&g_gbvk_alloc_counter, 1);
    if ((cnt % GBVK_INFO_REFRESH_INTERVAL) == 0) {
        gbvk_refresh_pool_info();
        gbvk_burst_check();
    }

    /* Try the real allocator first (T1 VRAM).  Chain a priority=1.0 hint
     * so the driver keeps this allocation resident over our T2/T3 spill
     * allocations under pressure. */
    VkMemoryPriorityAllocateInfoEXT t1_prio;
    VkMemoryAllocateInfo t1_info = *pAllocInfo;
    t1_info.pNext = gbvk_chain_priority(t1_info.pNext, &t1_prio, 1.0f);
    VkResult res = fn_alloc(device, &t1_info, pAllocator, pMemory);
    if (res != VK_ERROR_OUT_OF_DEVICE_MEMORY)
        return res;

    /* Below the minimum size , not a candidate for overflow. */
    if (pAllocInfo->allocationSize < g_gbvk_overflow_min_bytes) {
        atomic_fetch_add(&g_gbvk_oom_count, 1);
        return res;
    }

    /* Pressure-aware T2 routing:
     *   CRITICAL + large alloc → skip T2 (already saturated), go to T3
     *   WARN → try T2 but go to T3 immediately on failure
     *   OK   → normal T2 path */
    uint32_t pressure = gbvk_t2_pressure();
    int skip_t2 = (pressure == GB_T2_PRESSURE_CRITICAL &&
                   pAllocInfo->allocationSize >= 256ULL * 1024ULL * 1024ULL);

    if (!skip_t2) {
        /* Gaming T2 allocs are marked SESSION_PROTECTED so an AI inference
         * session running concurrently (e.g. background Ollama) cannot evict
         * the game's texture working set from T2 DDR. */
        VkResult t2_res = gbvk_try_dmabuf_alloc(
            device, pAllocInfo, pAllocator, pMemory,
            fn_alloc, fn_fd_props, &mem_props,
            GB_ALLOC_WEIGHTS | GB_ALLOC_SESSION_PROTECTED, 2);
        if (t2_res == VK_SUCCESS) return VK_SUCCESS;
    } else {
        gbvk_dbg("AllocateMemory: T2 skipped (CRITICAL pressure), %llu MB → T3 direct",
                 (unsigned long long)(pAllocInfo->allocationSize >> 20));
    }

    /* T3 NVMe fallback , only for large streaming textures. */
    if (pAllocInfo->allocationSize >= g_gbvk_t3_min_bytes) {
        VkResult t3_res = gbvk_try_dmabuf_alloc(
            device, pAllocInfo, pAllocator, pMemory,
            fn_alloc, fn_fd_props, &mem_props,
            GB_ALLOC_WEIGHTS | GB_ALLOC_NO_HUGEPAGE, 3);
        if (t3_res == VK_SUCCESS) return VK_SUCCESS;
    }

    atomic_fetch_add(&g_gbvk_oom_count, 1);
    gbvk_log("AllocateMemory: all tiers failed for %llu MB , returning OOM",
             (unsigned long long)(pAllocInfo->allocationSize >> 20));
    return res;
}

/* ── Hook: vkFreeMemory ────────────────────────────────────────────────── */

static VKAPI_ATTR void VKAPI_CALL
gbvk_FreeMemory(VkDevice                       device,
                VkDeviceMemory                 memory,
                const VkAllocationCallbacks   *pAllocator)
{
    pthread_mutex_lock(&g_mutex);
    GbDevData *d = dev_find(device);
    PFN_vkFreeMemory fn = d ? d->next_free_mem : NULL;
    pthread_mutex_unlock(&g_mutex);

    /* Check if this was a tracked T2/T3 overflow alloc. */
    if (memory != VK_NULL_HANDLE) {
        GbVkHtEntry entry;
        if (gbvk_ht_remove((uint64_t)(uintptr_t)memory, &entry)) {
            /* PR-CCC: closing the tracking dup of the DMA-BUF fd is sufficient.
             * The kernel module (greenboost.ko PR-KK + PR-AA) tracks importer
             * liveness via the active_mappings refcount on map_dma_buf /
             * unmap_dma_buf and gates eviction with dma_resv_test_signaled, so
             * a redundant GB_IOCTL_MADVISE COLD call here would only add a
             * syscall per free with no observable effect.  The Vulkan driver's
             * own dma_buf_detach drops active_mappings; our close() drops the
             * last ref and lets the kernel reclaim pages. */
            if (entry.dma_fd >= 0)
                close(entry.dma_fd);

            gbvk_dbg("FreeMemory: T%u %llu MB freed (fd dropped)",
                     entry.tier, (unsigned long long)(entry.size >> 20));
        }
    }

    if (fn) fn(device, memory, pAllocator);
}

/* ── Hook: vkGetPhysicalDeviceMemoryProperties ────────────────────────── */

static VKAPI_ATTR void VKAPI_CALL
gbvk_GetPhysicalDeviceMemoryProperties(
    VkPhysicalDevice physicalDevice,
    VkPhysicalDeviceMemoryProperties *pMemoryProperties)
{
    pthread_mutex_lock(&g_mutex);
    PFN_vkGetPhysicalDeviceMemoryProperties fn = NULL;
    for (int i = 0; i < GBVK_MAX_INSTANCES; i++)
        if (g_inst[i].next_get_mem_props) { fn = g_inst[i].next_get_mem_props; break; }
    pthread_mutex_unlock(&g_mutex);

    if (fn) fn(physicalDevice, pMemoryProperties);
    inflate_heaps(pMemoryProperties);
}

static VKAPI_ATTR void VKAPI_CALL
gbvk_GetPhysicalDeviceMemoryProperties2(
    VkPhysicalDevice physicalDevice,
    VkPhysicalDeviceMemoryProperties2 *pMemoryProperties)
{
    pthread_mutex_lock(&g_mutex);
    PFN_vkGetPhysicalDeviceMemoryProperties2 fn = NULL;
    for (int i = 0; i < GBVK_MAX_INSTANCES; i++)
        if (g_inst[i].next_get_mem_props2) { fn = g_inst[i].next_get_mem_props2; break; }
    pthread_mutex_unlock(&g_mutex);

    if (fn) fn(physicalDevice, pMemoryProperties);
    inflate_heaps(&pMemoryProperties->memoryProperties);
    inflate_budget(pMemoryProperties);
}

/* ── PR-GGGG: image-on-spill detection ─────────────────────────────────
 *
 * When an image is bound to a VkDeviceMemory that lives in our T2/T3
 * spill pool, every read/write traverses PCIe instead of GDDR , a 10×
 * bandwidth hit.  For framebuffers and other render targets this is
 * usually catastrophic; for streaming textures it's tolerable.  Emit a
 * one-shot warning per device so the operator can investigate.
 */
static _Atomic uint32_t g_gbvk_spill_image_count = 0;
static _Atomic uint32_t g_gbvk_spill_image_warned = 0;

static void gbvk_note_spill_image(VkDeviceMemory mem)
{
    if (mem == VK_NULL_HANDLE) return;
    /* Lookup is O(1) , uses the same hash table that tracks T2/T3 frees. */
    uint32_t idx = gbvk_ht_hash((uint64_t)(uintptr_t)mem);
    pthread_mutex_t *lk = &g_gbvk_ht_locks[idx & (GBVK_HT_LOCKS - 1)];
    pthread_mutex_lock(lk);
    GbVkHtEntry *e = &g_gbvk_ht[idx];
    int hit = (e->key == (uint64_t)(uintptr_t)mem);
    uint8_t tier = hit ? e->tier : 0;
    pthread_mutex_unlock(lk);
    if (!hit) return;
    uint32_t total = atomic_fetch_add(&g_gbvk_spill_image_count, 1) + 1;
    uint32_t warned = atomic_load_explicit(&g_gbvk_spill_image_warned,
                                           memory_order_relaxed);
    if (!warned) {
        uint32_t zero = 0;
        if (atomic_compare_exchange_strong_explicit(
                &g_gbvk_spill_image_warned, &zero, 1,
                memory_order_relaxed, memory_order_relaxed)) {
            gbvk_log("WARN: VkImage bound to T%u spill memory , render-target "
                     "or texture access will run at PCIe speed (~32 GB/s) "
                     "instead of VRAM (~336 GB/s).  Lower in-game settings "
                     "or close other GPU apps to keep working set in VRAM.",
                     tier);
        }
    }
    (void)total;
}

static VKAPI_ATTR VkResult VKAPI_CALL
gbvk_BindImageMemory(VkDevice device, VkImage image,
                     VkDeviceMemory memory, VkDeviceSize memoryOffset)
{
    pthread_mutex_lock(&g_mutex);
    GbDevData *d = dev_find(device);
    PFN_vkBindImageMemory fn = d ? d->next_bind_image_memory : NULL;
    pthread_mutex_unlock(&g_mutex);
    if (!fn) return VK_ERROR_INITIALIZATION_FAILED;
    VkResult r = fn(device, image, memory, memoryOffset);
    if (r == VK_SUCCESS) gbvk_note_spill_image(memory);
    return r;
}

static VKAPI_ATTR VkResult VKAPI_CALL
gbvk_BindImageMemory2(VkDevice device, uint32_t bindInfoCount,
                      const VkBindImageMemoryInfo *pBindInfos)
{
    pthread_mutex_lock(&g_mutex);
    GbDevData *d = dev_find(device);
    PFN_vkBindImageMemory2 fn = d ? d->next_bind_image_memory2 : NULL;
    pthread_mutex_unlock(&g_mutex);
    if (!fn) return VK_ERROR_INITIALIZATION_FAILED;
    VkResult r = fn(device, bindInfoCount, pBindInfos);
    if (r == VK_SUCCESS) {
        for (uint32_t i = 0; i < bindInfoCount; i++)
            gbvk_note_spill_image(pBindInfos[i].memory);
    }
    return r;
}

/* ── PR-GGGG: NVIDIA Image Scaling (NIS) post-process ──────────────────────
 *
 * Sharpen-only Vulkan compute post-process applied to swapchain images
 * before vkQueuePresentKHR.  All initialisation is lazy and best-effort;
 * any failure sets nis_failed and silently disables the pass for the rest
 * of the session.  The game never sees a behavioural difference if NIS is
 * unavailable.
 *
 * This stage:
 *   • lazy device-level init  (shader module, descriptor-set layout,
 *     pipeline layout, sampler, compute pipeline)
 *   • vkCreateSwapchainKHR hook records (extent, format, image count)
 *     and logs that NIS is staged for the swapchain
 *
 * Deferred to the dispatch task:
 *   • per-swapchain intermediate VkImage allocation
 *   • per-image descriptor set + command buffer
 *   • vkQueuePresentKHR queue-submit chaining
 *
 * Enable: GREENBOOST_NIS=1   (default off , pipeline is created but
 *                              never dispatched until the dispatch path
 *                              lands)
 */

/* ── NIS per-swapchain state ──────────────────────────────────────────
 * Bounded table , apps almost never create more than 1–2 swapchains.  Up
 * to 8 images per swapchain (typical 2–3). */
#define GBVK_MAX_SWAPCHAINS  4
#define GBVK_MAX_SWAP_IMAGES 8

typedef struct GbNisSwapState_ {
    VkSwapchainKHR   swapchain;
    VkDevice         device;
    uint32_t         width;
    uint32_t         height;
    VkFormat         format;
    uint32_t         image_count;
    VkImage          swap_images[GBVK_MAX_SWAP_IMAGES];
    VkImageView      swap_views_storage[GBVK_MAX_SWAP_IMAGES];
    VkImage          inter_images[GBVK_MAX_SWAP_IMAGES];
    VkDeviceMemory   inter_memory[GBVK_MAX_SWAP_IMAGES];
    VkImageView      inter_views_sampled[GBVK_MAX_SWAP_IMAGES];
    VkBuffer         ub_buffer;
    VkDeviceMemory   ub_memory;
    VkDescriptorPool desc_pool;
    VkDescriptorSet  desc_sets[GBVK_MAX_SWAP_IMAGES];
    VkCommandPool    cmd_pool;
    VkCommandBuffer  cmd_buffers[GBVK_MAX_SWAP_IMAGES];
    VkSemaphore      done_semaphores[GBVK_MAX_SWAP_IMAGES];
    int              ready;        /* 1 = full alloc succeeded */
} GbNisSwapState;

static GbNisSwapState g_nis_swap[GBVK_MAX_SWAPCHAINS];
static pthread_mutex_t g_nis_swap_mu = PTHREAD_MUTEX_INITIALIZER;

/* Embedded NIS SPIR-V blobs (linked from nis_blobs.S via .incbin).
 * These are the primary source; disk files are a fallback override. */
extern const char nis_sharpen_spv_start[], nis_sharpen_spv_end[];
extern const char nis_upscale_spv_start[],  nis_upscale_spv_end[];

/* ── GreenBoost overlay ───────────────────────────────────────────────
 *
 * An in-game HUD drawn by a compute pass over the swapchain image, the same
 * mechanism MangoHud uses, except this one reports the things GreenBoost
 * knows and MangoHud cannot see: which memory tier the game's allocations
 * actually landed in, and whether the recorder is armed.
 *
 * Enable with GREENBOOST_OVERLAY=1. Visibility and page are then driven at
 * runtime by $XDG_RUNTIME_DIR/greenboost-overlay.state, which
 * gb_gaming/hotkey_daemon.py rewrites atomically when a bound key is pressed.
 * A file rather than a socket because this code runs inside the game's
 * present path: a read that blocks there is a stutter, and a rename is the
 * one update a reader cannot catch half-done.
 */
extern const char gb_hud_spv_start[], gb_hud_spv_end[];
#include "gb_hud_font.h"

#define GB_HUD_COLS      44
#define GB_HUD_ROWS      8
#define GB_HUD_CELLS     (GB_HUD_COLS * GB_HUD_ROWS)
#define GB_HUD_PAGES     4
/* Re-stat the control file at most this often. The present path must not
 * pay a syscall per frame for a value that changes when a human presses a
 * key. */
#define GB_HUD_POLL_NS   250000000ull

/* Read the NIS SPIR-V for the requested variant.
 * use_upscale=1 → upscale shader (NIS_SCALER=1); 0 → sharpen-only.
 * Primary: embedded blobs linked into the .so.
 * Override: $GREENBOOST_NIS_SHADERS_DIR/{nis_sharpen,nis_upscale}.spv (disk).
 * Returns a malloc'd buffer + size.  Caller frees. */
static uint32_t *gbvk_nis_read_spirv(size_t *out_bytes, int use_upscale)
{
    *out_bytes = 0;

    /* Check for disk override (allows hot-patching shaders without rebuilding). */
    const char *dir = getenv("GREENBOOST_NIS_SHADERS_DIR");
    if (dir && *dir) {
        char path[1024];
        snprintf(path, sizeof path, "%s/%s", dir,
                 use_upscale ? "nis_upscale.spv" : "nis_sharpen.spv");
        int fd = open(path, O_RDONLY | O_CLOEXEC);
        if (fd >= 0) {
            struct stat st;
            if (fstat(fd, &st) == 0 && st.st_size > 0 &&
                st.st_size <= (1 << 20) && (st.st_size % 4) == 0) {
                uint32_t *buf = malloc((size_t)st.st_size);
                if (buf && read(fd, buf, (size_t)st.st_size) == st.st_size) {
                    close(fd);
                    *out_bytes = (size_t)st.st_size;
                    gbvk_log("NIS: loaded %s from disk override (%zu bytes)",
                             use_upscale ? "upscale" : "sharpen", *out_bytes);
                    return buf;
                }
                free(buf);
            }
            close(fd);
        }
    }

    /* Use embedded blob. */
    const char *start = use_upscale ? nis_upscale_spv_start : nis_sharpen_spv_start;
    const char *end   = use_upscale ? nis_upscale_spv_end   : nis_sharpen_spv_end;
    size_t sz = (size_t)(end - start);
    if (sz == 0 || sz > (1 << 20) || (sz % 4) != 0) {
        gbvk_log("NIS: embedded %s blob is invalid (size=%zu) , "
                 "rebuild with `make nis-shaders`",
                 use_upscale ? "upscale" : "sharpen", sz);
        return NULL;
    }
    uint32_t *buf = malloc(sz);
    if (!buf) return NULL;
    memcpy(buf, start, sz);
    *out_bytes = sz;
    gbvk_log("NIS: using embedded %s SPIR-V (%zu bytes)",
             use_upscale ? "upscale" : "sharpen", sz);
    return buf;
}

/* One-shot device-level NIS bring-up.  Must be called outside g_mutex ,
 * does Vulkan calls that may block.  After success or sticky failure,
 * sets d->nis_initialised or d->nis_failed under g_mutex. */
static void gbvk_nis_init_device(GbDevData *d, VkDevice device)
{
    if (!d || d->nis_initialised || d->nis_failed) return;
    if (!d->next_create_shader_module || !d->next_create_dsl ||
        !d->next_create_player || !d->next_create_sampler) {
        d->nis_failed = 1;
        return;
    }

    float nis_scale = nis_read_scale();
    int use_upscale = (nis_scale < 0.999f);  /* sharpen-only when scale == 1.0 */

    size_t spv_size = 0;
    uint32_t *spv = gbvk_nis_read_spirv(&spv_size, use_upscale);
    if (!spv) {
        gbvk_log("NIS: SPIR-V unavailable (use_upscale=%d) , disabled", use_upscale);
        d->nis_failed = 1;
        return;
    }
    gbvk_log("NIS: loading %s shader (scale=%.2f)",
             use_upscale ? "upscale" : "sharpen", nis_scale);

    VkShaderModuleCreateInfo smci = {
        .sType    = VK_STRUCTURE_TYPE_SHADER_MODULE_CREATE_INFO,
        .codeSize = spv_size,
        .pCode    = spv,
    };
    VkShaderModule mod = VK_NULL_HANDLE;
    VkResult r = d->next_create_shader_module(device, &smci, NULL, &mod);
    free(spv);
    if (r != VK_SUCCESS) { d->nis_failed = 1; return; }

    /* Six bindings , matches NIS_Main.glsl exactly:
     *   0: uniform buffer  (NIS constants)
     *   1: sampler         (immutable linear-clamp)
     *   2: texture2D       (input , sampled)
     *   3: writeonly image (output , storage)
     *   4: texture2D       (coef_scaler)  , not used in sharpen, still in layout
     *   5: texture2D       (coef_usm)
     * For sharpen-only we still need bindings 4 and 5 present in the layout
     * to keep the SPIR-V interface valid, but the descriptors can be VK_NULL
     * (driver ignores unused). */
    VkDescriptorSetLayoutBinding bindings[6] = {
        {0, VK_DESCRIPTOR_TYPE_UNIFORM_BUFFER,         1, VK_SHADER_STAGE_COMPUTE_BIT, NULL},
        {1, VK_DESCRIPTOR_TYPE_SAMPLER,                1, VK_SHADER_STAGE_COMPUTE_BIT, NULL},
        {2, VK_DESCRIPTOR_TYPE_SAMPLED_IMAGE,          1, VK_SHADER_STAGE_COMPUTE_BIT, NULL},
        {3, VK_DESCRIPTOR_TYPE_STORAGE_IMAGE,          1, VK_SHADER_STAGE_COMPUTE_BIT, NULL},
        {4, VK_DESCRIPTOR_TYPE_SAMPLED_IMAGE,          1, VK_SHADER_STAGE_COMPUTE_BIT, NULL},
        {5, VK_DESCRIPTOR_TYPE_SAMPLED_IMAGE,          1, VK_SHADER_STAGE_COMPUTE_BIT, NULL},
    };
    VkDescriptorSetLayoutCreateInfo dslci = {
        .sType        = VK_STRUCTURE_TYPE_DESCRIPTOR_SET_LAYOUT_CREATE_INFO,
        .bindingCount = 6,
        .pBindings    = bindings,
    };
    VkDescriptorSetLayout dsl = VK_NULL_HANDLE;
    r = d->next_create_dsl(device, &dslci, NULL, &dsl);
    if (r != VK_SUCCESS) {
        if (d->next_destroy_shader_module) d->next_destroy_shader_module(device, mod, NULL);
        d->nis_failed = 1; return;
    }

    VkPipelineLayoutCreateInfo plci = {
        .sType          = VK_STRUCTURE_TYPE_PIPELINE_LAYOUT_CREATE_INFO,
        .setLayoutCount = 1,
        .pSetLayouts    = &dsl,
    };
    VkPipelineLayout player = VK_NULL_HANDLE;
    r = d->next_create_player(device, &plci, NULL, &player);
    if (r != VK_SUCCESS) {
        if (d->next_destroy_dsl) d->next_destroy_dsl(device, dsl, NULL);
        if (d->next_destroy_shader_module) d->next_destroy_shader_module(device, mod, NULL);
        d->nis_failed = 1; return;
    }

    VkComputePipelineCreateInfo cpci = {
        .sType  = VK_STRUCTURE_TYPE_COMPUTE_PIPELINE_CREATE_INFO,
        .stage  = {
            .sType  = VK_STRUCTURE_TYPE_PIPELINE_SHADER_STAGE_CREATE_INFO,
            .stage  = VK_SHADER_STAGE_COMPUTE_BIT,
            .module = mod,
            .pName  = "main",
        },
        .layout = player,
    };
    VkPipeline pipeline = VK_NULL_HANDLE;
    r = d->next_create_compute_pipelines(device, VK_NULL_HANDLE, 1, &cpci, NULL, &pipeline);
    if (r != VK_SUCCESS) {
        if (d->next_destroy_player)        d->next_destroy_player(device, player, NULL);
        if (d->next_destroy_dsl)           d->next_destroy_dsl(device, dsl, NULL);
        if (d->next_destroy_shader_module) d->next_destroy_shader_module(device, mod, NULL);
        d->nis_failed = 1; return;
    }

    VkSamplerCreateInfo sci = {
        .sType        = VK_STRUCTURE_TYPE_SAMPLER_CREATE_INFO,
        .magFilter    = VK_FILTER_LINEAR,
        .minFilter    = VK_FILTER_LINEAR,
        .mipmapMode   = VK_SAMPLER_MIPMAP_MODE_NEAREST,
        .addressModeU = VK_SAMPLER_ADDRESS_MODE_CLAMP_TO_EDGE,
        .addressModeV = VK_SAMPLER_ADDRESS_MODE_CLAMP_TO_EDGE,
        .addressModeW = VK_SAMPLER_ADDRESS_MODE_CLAMP_TO_EDGE,
        .minLod       = 0.0f,
        .maxLod       = 0.0f,
    };
    VkSampler sampler = VK_NULL_HANDLE;
    r = d->next_create_sampler(device, &sci, NULL, &sampler);
    if (r != VK_SUCCESS) {
        if (d->next_destroy_pipeline)      d->next_destroy_pipeline(device, pipeline, NULL);
        if (d->next_destroy_player)        d->next_destroy_player(device, player, NULL);
        if (d->next_destroy_dsl)           d->next_destroy_dsl(device, dsl, NULL);
        if (d->next_destroy_shader_module) d->next_destroy_shader_module(device, mod, NULL);
        d->nis_failed = 1; return;
    }

    pthread_mutex_lock(&g_mutex);
    d->nis_module          = mod;
    d->nis_dsl             = dsl;
    d->nis_player          = player;
    d->nis_pipeline        = pipeline;
    d->nis_sampler         = sampler;
    d->nis_initialised     = 1;
    d->nis_use_upscale     = use_upscale;
    d->nis_scale           = nis_scale;
    pthread_mutex_unlock(&g_mutex);

    gbvk_log("NIS: device-level pipeline ready (%s variant, scale=%.2f)",
             use_upscale ? "upscale" : "sharpen", nis_scale);
}

/* Shared with the NIS path, which defines it further down. */
static uint32_t nis_find_mem_type(const VkPhysicalDeviceMemoryProperties *mp,
                                  uint32_t type_bits, VkMemoryPropertyFlags want);

/* ── overlay: runtime control ─────────────────────────────────────────── */

typedef struct {
    int      visible;
    int      page;
    uint64_t last_poll_ns;
} GbHudControl;

static GbHudControl g_hud_ctl;
static pthread_mutex_t g_hud_ctl_mu = PTHREAD_MUTEX_INITIALIZER;

static uint64_t gbvk_now_ns(void)
{
    struct timespec ts;
    clock_gettime(CLOCK_MONOTONIC, &ts);
    return (uint64_t)ts.tv_sec * 1000000000ull + (uint64_t)ts.tv_nsec;
}

static int gbvk_hud_enabled(void)
{
    const char *e = getenv("GREENBOOST_OVERLAY");
    return e && e[0] == '1';
}

/* Read the control file, at most every GB_HUD_POLL_NS. */
static void gbvk_hud_poll_control(int *visible, int *page)
{
    uint64_t now = gbvk_now_ns();
    pthread_mutex_lock(&g_hud_ctl_mu);
    if (now - g_hud_ctl.last_poll_ns >= GB_HUD_POLL_NS) {
        g_hud_ctl.last_poll_ns = now;
        const char *rt = getenv("XDG_RUNTIME_DIR");
        char path[512];
        if (rt && *rt)
            snprintf(path, sizeof(path), "%s/greenboost-overlay.state", rt);
        else
            snprintf(path, sizeof(path), "/run/user/%u/greenboost-overlay.state",
                     (unsigned)getuid());
        FILE *f = fopen(path, "r");
        if (f) {
            int v = 0, pg = 0;
            if (fscanf(f, "%d %d", &v, &pg) == 2) {
                g_hud_ctl.visible = v ? 1 : 0;
                g_hud_ctl.page    = (pg % GB_HUD_PAGES + GB_HUD_PAGES) % GB_HUD_PAGES;
            }
            fclose(f);
        } else {
            /* No control file yet means nobody has pressed the toggle. Default
             * to visible so GREENBOOST_OVERLAY=1 shows something on its own,
             * rather than looking broken until the daemon is running. */
            g_hud_ctl.visible = 1;
        }
    }
    *visible = g_hud_ctl.visible;
    *page    = g_hud_ctl.page;
    pthread_mutex_unlock(&g_hud_ctl_mu);
}

/* ── overlay: frame timing ───────────────────────────────────────────── */

#define GB_HUD_FRAME_WINDOW 120

typedef struct {
    uint64_t last_ns;
    float    dt_ms[GB_HUD_FRAME_WINDOW];
    int      n;
    int      head;
} GbHudTiming;

static GbHudTiming g_hud_time;

static void gbvk_hud_tick(float *fps, float *avg_ms, float *p1_ms)
{
    uint64_t now = gbvk_now_ns();
    if (g_hud_time.last_ns) {
        float dt = (float)(now - g_hud_time.last_ns) / 1e6f;
        /* A frame longer than a second is an alt-tab or a load screen, not a
         * frame time. Letting it into the window poisons the average and the
         * percentile for the next two seconds. */
        if (dt > 0.0f && dt < 1000.0f) {
            g_hud_time.dt_ms[g_hud_time.head] = dt;
            g_hud_time.head = (g_hud_time.head + 1) % GB_HUD_FRAME_WINDOW;
            if (g_hud_time.n < GB_HUD_FRAME_WINDOW) g_hud_time.n++;
        }
    }
    g_hud_time.last_ns = now;

    *fps = 0.0f; *avg_ms = 0.0f; *p1_ms = 0.0f;
    if (g_hud_time.n <= 0) return;

    float sum = 0.0f, worst = 0.0f;
    /* 1% low, reported as a frame TIME: the number that shows a stutter the
     * average hides. With a 120-frame window the top 1% is one frame, so this
     * is the window maximum by construction , say so rather than implying a
     * real percentile over a larger sample. */
    for (int i = 0; i < g_hud_time.n; i++) {
        sum += g_hud_time.dt_ms[i];
        if (g_hud_time.dt_ms[i] > worst) worst = g_hud_time.dt_ms[i];
    }
    *avg_ms = sum / (float)g_hud_time.n;
    *p1_ms  = worst;
    if (*avg_ms > 0.0f) *fps = 1000.0f / *avg_ms;
}

/* ── overlay: per-swapchain resources ────────────────────────────────── */

typedef struct {
    VkSwapchainKHR   swapchain;
    VkDevice         device;
    uint32_t         width, height, image_count;
    VkFormat         storage_fmt;
    VkImageView      views[GBVK_MAX_SWAP_IMAGES];
    VkBuffer         font_buf,  text_buf;
    VkDeviceMemory   font_mem,  text_mem;
    void            *text_map;
    VkDescriptorPool desc_pool;
    VkDescriptorSet  sets[GBVK_MAX_SWAP_IMAGES];
    VkCommandPool    cmd_pool;
    VkCommandBuffer  cbs[GBVK_MAX_SWAP_IMAGES];
    VkSemaphore      done[GBVK_MAX_SWAP_IMAGES];
    int              ready;
} GbHudSwapState;

static GbHudSwapState g_hud_swap[GBVK_MAX_SWAPCHAINS];
static pthread_mutex_t g_hud_swap_mu = PTHREAD_MUTEX_INITIALIZER;

typedef struct {
    int32_t origin[2];
    int32_t cell[2];
    int32_t grid[2];
    float   fg[4];
    float   bg[4];
    int32_t bgra;
    int32_t pad;
} GbHudPush;

static GbHudSwapState *hud_state_find(VkSwapchainKHR sc)
{
    for (int i = 0; i < GBVK_MAX_SWAPCHAINS; i++)
        if (g_hud_swap[i].swapchain == sc) return &g_hud_swap[i];
    return NULL;
}

static GbHudSwapState *hud_state_alloc(VkSwapchainKHR sc)
{
    for (int i = 0; i < GBVK_MAX_SWAPCHAINS; i++)
        if (g_hud_swap[i].swapchain == VK_NULL_HANDLE) {
            memset(&g_hud_swap[i], 0, sizeof(g_hud_swap[i]));
            g_hud_swap[i].swapchain = sc;
            return &g_hud_swap[i];
        }
    return NULL;
}

/* ── overlay: device-level pipeline ──────────────────────────────────── */

static int g_hud_dev_ready, g_hud_dev_failed;
static VkShaderModule        g_hud_module;
static VkDescriptorSetLayout g_hud_dsl;
static VkPipelineLayout      g_hud_playout;
static VkPipeline            g_hud_pipeline;

static int gbvk_hud_init_device(GbDevData *d, VkDevice dev)
{
    if (g_hud_dev_ready)  return 1;
    if (g_hud_dev_failed) return 0;
    if (!d || !d->next_create_shader_module || !d->next_create_dsl ||
        !d->next_create_player || !d->next_create_compute_pipelines ||
        !d->next_cmd_push_constants) {
        g_hud_dev_failed = 1;
        gbvk_log("overlay: device lacks an entry point we need, disabled");
        return 0;
    }

    size_t spv_len = (size_t)(gb_hud_spv_end - gb_hud_spv_start);
    VkShaderModuleCreateInfo smci = {
        .sType    = VK_STRUCTURE_TYPE_SHADER_MODULE_CREATE_INFO,
        .codeSize = spv_len,
        .pCode    = (const uint32_t *)gb_hud_spv_start,
    };
    if (d->next_create_shader_module(dev, &smci, NULL, &g_hud_module) != VK_SUCCESS) {
        g_hud_dev_failed = 1;
        gbvk_log("overlay: shader module creation failed, disabled");
        return 0;
    }

    VkDescriptorSetLayoutBinding b[3] = {
        { .binding = 0, .descriptorType = VK_DESCRIPTOR_TYPE_STORAGE_IMAGE,
          .descriptorCount = 1, .stageFlags = VK_SHADER_STAGE_COMPUTE_BIT },
        { .binding = 1, .descriptorType = VK_DESCRIPTOR_TYPE_STORAGE_BUFFER,
          .descriptorCount = 1, .stageFlags = VK_SHADER_STAGE_COMPUTE_BIT },
        { .binding = 2, .descriptorType = VK_DESCRIPTOR_TYPE_STORAGE_BUFFER,
          .descriptorCount = 1, .stageFlags = VK_SHADER_STAGE_COMPUTE_BIT },
    };
    VkDescriptorSetLayoutCreateInfo dslci = {
        .sType = VK_STRUCTURE_TYPE_DESCRIPTOR_SET_LAYOUT_CREATE_INFO,
        .bindingCount = 3, .pBindings = b,
    };
    if (d->next_create_dsl(dev, &dslci, NULL, &g_hud_dsl) != VK_SUCCESS) {
        g_hud_dev_failed = 1; return 0;
    }

    VkPushConstantRange pcr = {
        .stageFlags = VK_SHADER_STAGE_COMPUTE_BIT,
        .offset = 0, .size = sizeof(GbHudPush),
    };
    VkPipelineLayoutCreateInfo plci = {
        .sType = VK_STRUCTURE_TYPE_PIPELINE_LAYOUT_CREATE_INFO,
        .setLayoutCount = 1, .pSetLayouts = &g_hud_dsl,
        .pushConstantRangeCount = 1, .pPushConstantRanges = &pcr,
    };
    if (d->next_create_player(dev, &plci, NULL, &g_hud_playout) != VK_SUCCESS) {
        g_hud_dev_failed = 1; return 0;
    }

    VkComputePipelineCreateInfo cpci = {
        .sType  = VK_STRUCTURE_TYPE_COMPUTE_PIPELINE_CREATE_INFO,
        .stage  = { .sType  = VK_STRUCTURE_TYPE_PIPELINE_SHADER_STAGE_CREATE_INFO,
                    .stage  = VK_SHADER_STAGE_COMPUTE_BIT,
                    .module = g_hud_module, .pName = "main" },
        .layout = g_hud_playout,
    };
    if (d->next_create_compute_pipelines(dev, VK_NULL_HANDLE, 1, &cpci, NULL,
                                         &g_hud_pipeline) != VK_SUCCESS) {
        g_hud_dev_failed = 1; return 0;
    }

    g_hud_dev_ready = 1;
    gbvk_log("overlay: pipeline ready (%zu-byte shader, %ux%u cells)",
             spv_len, (unsigned)GB_HUD_COLS, (unsigned)GB_HUD_ROWS);
    return 1;
}

/* ── overlay: text ───────────────────────────────────────────────────── */

/* Writes one left-aligned line into the cell grid. Truncates rather than
 * wrapping: a HUD that reflows when a number gains a digit is unreadable. */
static void gbvk_hud_line(uint32_t *cells, int row, const char *fmt, ...)
{
    if (row < 0 || row >= GB_HUD_ROWS) return;
    char buf[GB_HUD_COLS + 1];
    va_list ap;
    va_start(ap, fmt);
    vsnprintf(buf, sizeof(buf), fmt, ap);
    va_end(ap);
    uint32_t *dst = cells + (size_t)row * GB_HUD_COLS;
    int i = 0;
    for (; buf[i] && i < GB_HUD_COLS; i++)
        dst[i] = (unsigned char)buf[i];
    for (; i < GB_HUD_COLS; i++) dst[i] = ' ';
}

static void gbvk_hud_build_text(uint32_t *cells, int page)
{
    for (int i = 0; i < GB_HUD_CELLS; i++) cells[i] = ' ';

    float fps, avg_ms, p1_ms;
    gbvk_hud_tick(&fps, &avg_ms, &p1_ms);

    unsigned t2_n     = (unsigned)atomic_load(&g_gbvk_t2_count);
    unsigned long t2_b = (unsigned long)atomic_load(&g_gbvk_t2_bytes);

    gbvk_hud_line(cells, 0, "GreenBoost  page %d/%d", page + 1, GB_HUD_PAGES);

    if (page == 0) {
        gbvk_hud_line(cells, 1, "%6.1f FPS   %5.2f ms avg", fps, avg_ms);
        gbvk_hud_line(cells, 2, "worst frame in window %5.2f ms", p1_ms);
        gbvk_hud_line(cells, 3, "T2 spill %u alloc  %lu MB",
                      t2_n, t2_b / (1024ul * 1024ul));
    } else if (page == 1) {
        /* Tier detail: the thing MangoHud cannot show, because only this
         * layer sees which allocations were redirected. */
        gbvk_hud_line(cells, 1, "tier detail");
        gbvk_hud_line(cells, 2, "T2 allocations  %u", t2_n);
        gbvk_hud_line(cells, 3, "T2 bytes        %lu MB",
                      t2_b / (1024ul * 1024ul));
        gbvk_hud_line(cells, 4, "frames sampled  %d", g_hud_time.n);
    } else if (page == 2) {
        gbvk_hud_line(cells, 1, "frame time");
        gbvk_hud_line(cells, 2, "avg   %6.2f ms", avg_ms);
        gbvk_hud_line(cells, 3, "worst %6.2f ms", p1_ms);
        gbvk_hud_line(cells, 4, "fps   %6.1f", fps);
    } else {
        gbvk_hud_line(cells, 1, "alt+F11 toggle   alt+F12 page");
        gbvk_hud_line(cells, 2, "alt+F10 save replay");
        gbvk_hud_line(cells, 3, "alt+F9  record   alt+F1 shot");
    }
}

/* ── overlay: per-swapchain setup ────────────────────────────────────── */

static void gbvk_hud_swap_free(GbDevData *d, GbHudSwapState *s)
{
    if (!s || !s->swapchain) return;
    VkDevice dev = s->device;
    if (d) {
        for (uint32_t i = 0; i < s->image_count; i++) {
            if (s->done[i] && d->next_destroy_semaphore)
                d->next_destroy_semaphore(dev, s->done[i], NULL);
            if (s->views[i] && d->next_destroy_image_view)
                d->next_destroy_image_view(dev, s->views[i], NULL);
        }
        if (s->cmd_pool && d->next_destroy_cmd_pool)
            d->next_destroy_cmd_pool(dev, s->cmd_pool, NULL);
        if (s->desc_pool && d->next_destroy_desc_pool)
            d->next_destroy_desc_pool(dev, s->desc_pool, NULL);
        if (s->text_buf && d->next_destroy_buffer)
            d->next_destroy_buffer(dev, s->text_buf, NULL);
        if (s->font_buf && d->next_destroy_buffer)
            d->next_destroy_buffer(dev, s->font_buf, NULL);
        if (s->text_mem && d->next_free_mem) d->next_free_mem(dev, s->text_mem, NULL);
        if (s->font_mem && d->next_free_mem) d->next_free_mem(dev, s->font_mem, NULL);
    }
    memset(s, 0, sizeof(*s));
}

/* Host-visible + coherent buffer. Coherent on purpose: the text is rewritten
 * from the present thread every frame and an explicit flush there is one more
 * thing to get wrong in the hot path. */
static int gbvk_hud_make_buffer(GbDevData *d, VkDevice dev, VkDeviceSize size,
                                VkBuffer *buf, VkDeviceMemory *mem, void **map)
{
    VkBufferCreateInfo bci = {
        .sType = VK_STRUCTURE_TYPE_BUFFER_CREATE_INFO,
        .size = size, .usage = VK_BUFFER_USAGE_STORAGE_BUFFER_BIT,
        .sharingMode = VK_SHARING_MODE_EXCLUSIVE,
    };
    if (d->next_create_buffer(dev, &bci, NULL, buf) != VK_SUCCESS) return 0;
    VkMemoryRequirements mr;
    d->next_get_buf_mem_req(dev, *buf, &mr);
    uint32_t mt = nis_find_mem_type(&d->mem_props, mr.memoryTypeBits,
                                    VK_MEMORY_PROPERTY_HOST_VISIBLE_BIT |
                                    VK_MEMORY_PROPERTY_HOST_COHERENT_BIT);
    if (mt == UINT32_MAX) return 0;
    VkMemoryAllocateInfo mai = {
        .sType = VK_STRUCTURE_TYPE_MEMORY_ALLOCATE_INFO,
        .allocationSize = mr.size, .memoryTypeIndex = mt,
    };
    if (d->next_alloc_mem(dev, &mai, NULL, mem) != VK_SUCCESS) return 0;
    if (d->next_bind_buf_memory(dev, *buf, *mem, 0) != VK_SUCCESS) return 0;
    if (map && d->next_map_memory(dev, *mem, 0, size, 0, map) != VK_SUCCESS) return 0;
    return 1;
}

/* Build every per-swapchain object and pre-record one dispatch per image.
 *
 * The command buffers are recorded once. Only the TEXT changes per frame, and
 * it lives in a host-coherent buffer the CPU rewrites before submit, so a
 * moving number costs a memcpy rather than a re-record.
 */
static int gbvk_hud_swap_init(GbDevData *d, VkDevice dev, VkSwapchainKHR sc,
                              VkFormat storage_fmt, uint32_t w, uint32_t h)
{
    if (!gbvk_hud_init_device(d, dev)) return 0;

    pthread_mutex_lock(&g_hud_swap_mu);
    GbHudSwapState *s = hud_state_find(sc);
    if (s) { pthread_mutex_unlock(&g_hud_swap_mu); return s->ready; }
    s = hud_state_alloc(sc);
    pthread_mutex_unlock(&g_hud_swap_mu);
    if (!s) return 0;

    s->device = dev; s->width = w; s->height = h; s->storage_fmt = storage_fmt;

    uint32_t n = 0;
    if (d->next_get_swapchain_images(dev, sc, &n, NULL) != VK_SUCCESS || n == 0)
        goto fail;
    if (n > GBVK_MAX_SWAP_IMAGES) n = GBVK_MAX_SWAP_IMAGES;
    VkImage images[GBVK_MAX_SWAP_IMAGES];
    if (d->next_get_swapchain_images(dev, sc, &n, images) != VK_SUCCESS) goto fail;
    s->image_count = n;

    for (uint32_t i = 0; i < n; i++) {
        VkImageViewCreateInfo ivci = {
            .sType = VK_STRUCTURE_TYPE_IMAGE_VIEW_CREATE_INFO,
            .image = images[i], .viewType = VK_IMAGE_VIEW_TYPE_2D,
            .format = storage_fmt,
            .subresourceRange = { VK_IMAGE_ASPECT_COLOR_BIT, 0, 1, 0, 1 },
        };
        if (d->next_create_image_view(dev, &ivci, NULL, &s->views[i]) != VK_SUCCESS)
            goto fail;
    }

    void *font_map = NULL;
    if (!gbvk_hud_make_buffer(d, dev, sizeof(gb_hud_font),
                              &s->font_buf, &s->font_mem, &font_map)) goto fail;
    memcpy(font_map, gb_hud_font, sizeof(gb_hud_font));

    if (!gbvk_hud_make_buffer(d, dev, GB_HUD_CELLS * sizeof(uint32_t),
                              &s->text_buf, &s->text_mem, &s->text_map)) goto fail;
    for (int i = 0; i < GB_HUD_CELLS; i++) ((uint32_t *)s->text_map)[i] = ' ';

    VkDescriptorPoolSize ps[2] = {
        { VK_DESCRIPTOR_TYPE_STORAGE_IMAGE,  n },
        { VK_DESCRIPTOR_TYPE_STORAGE_BUFFER, n * 2 },
    };
    VkDescriptorPoolCreateInfo dpci = {
        .sType = VK_STRUCTURE_TYPE_DESCRIPTOR_POOL_CREATE_INFO,
        .maxSets = n, .poolSizeCount = 2, .pPoolSizes = ps,
    };
    if (d->next_create_desc_pool(dev, &dpci, NULL, &s->desc_pool) != VK_SUCCESS)
        goto fail;

    VkDescriptorSetLayout layouts[GBVK_MAX_SWAP_IMAGES];
    for (uint32_t i = 0; i < n; i++) layouts[i] = g_hud_dsl;
    VkDescriptorSetAllocateInfo dsai = {
        .sType = VK_STRUCTURE_TYPE_DESCRIPTOR_SET_ALLOCATE_INFO,
        .descriptorPool = s->desc_pool, .descriptorSetCount = n,
        .pSetLayouts = layouts,
    };
    if (d->next_alloc_desc_sets(dev, &dsai, s->sets) != VK_SUCCESS) goto fail;

    for (uint32_t i = 0; i < n; i++) {
        VkDescriptorImageInfo  ii = { .imageView = s->views[i],
                                      .imageLayout = VK_IMAGE_LAYOUT_GENERAL };
        VkDescriptorBufferInfo fb = { .buffer = s->font_buf, .range = VK_WHOLE_SIZE };
        VkDescriptorBufferInfo tb = { .buffer = s->text_buf, .range = VK_WHOLE_SIZE };
        VkWriteDescriptorSet w3[3] = {
            { .sType = VK_STRUCTURE_TYPE_WRITE_DESCRIPTOR_SET, .dstSet = s->sets[i],
              .dstBinding = 0, .descriptorCount = 1,
              .descriptorType = VK_DESCRIPTOR_TYPE_STORAGE_IMAGE, .pImageInfo = &ii },
            { .sType = VK_STRUCTURE_TYPE_WRITE_DESCRIPTOR_SET, .dstSet = s->sets[i],
              .dstBinding = 1, .descriptorCount = 1,
              .descriptorType = VK_DESCRIPTOR_TYPE_STORAGE_BUFFER, .pBufferInfo = &fb },
            { .sType = VK_STRUCTURE_TYPE_WRITE_DESCRIPTOR_SET, .dstSet = s->sets[i],
              .dstBinding = 2, .descriptorCount = 1,
              .descriptorType = VK_DESCRIPTOR_TYPE_STORAGE_BUFFER, .pBufferInfo = &tb },
        };
        d->next_update_desc_sets(dev, 3, w3, 0, NULL);
    }

    VkCommandPoolCreateInfo cpci = {
        .sType = VK_STRUCTURE_TYPE_COMMAND_POOL_CREATE_INFO,
        .flags = VK_COMMAND_POOL_CREATE_RESET_COMMAND_BUFFER_BIT,
        .queueFamilyIndex = 0,
    };
    if (d->next_create_cmd_pool(dev, &cpci, NULL, &s->cmd_pool) != VK_SUCCESS) goto fail;
    VkCommandBufferAllocateInfo cbai = {
        .sType = VK_STRUCTURE_TYPE_COMMAND_BUFFER_ALLOCATE_INFO,
        .commandPool = s->cmd_pool, .level = VK_COMMAND_BUFFER_LEVEL_PRIMARY,
        .commandBufferCount = n,
    };
    if (d->next_alloc_cmd_buffers(dev, &cbai, s->cbs) != VK_SUCCESS) goto fail;

    GbHudPush push = {
        .origin = { 16, 16 },
        .cell   = { 8 * 2, 8 * 2 },        /* 2x scale, 16px cells */
        .grid   = { GB_HUD_COLS, GB_HUD_ROWS },
        .fg     = { 0.62f, 0.93f, 0.36f, 1.0f },   /* GreenBoost green */
        .bg     = { 0.02f, 0.03f, 0.02f, 0.55f },
        .bgra   = (storage_fmt == VK_FORMAT_B8G8R8A8_UNORM ||
                   storage_fmt == VK_FORMAT_B8G8R8A8_SRGB) ? 1 : 0,
    };

    for (uint32_t i = 0; i < n; i++) {
        VkCommandBufferBeginInfo bbi = {
            .sType = VK_STRUCTURE_TYPE_COMMAND_BUFFER_BEGIN_INFO };
        if (d->next_begin_cmd_buffer(s->cbs[i], &bbi) != VK_SUCCESS) goto fail;

        /* PRESENT_SRC -> GENERAL so the compute pass may write it, then back.
         * srcAccessMask 0 because the acquire semaphore already orders the
         * previous use of this image against us. */
        VkImageMemoryBarrier to_general = {
            .sType = VK_STRUCTURE_TYPE_IMAGE_MEMORY_BARRIER,
            .srcAccessMask = 0,
            .dstAccessMask = VK_ACCESS_SHADER_READ_BIT | VK_ACCESS_SHADER_WRITE_BIT,
            .oldLayout = VK_IMAGE_LAYOUT_PRESENT_SRC_KHR,
            .newLayout = VK_IMAGE_LAYOUT_GENERAL,
            .srcQueueFamilyIndex = VK_QUEUE_FAMILY_IGNORED,
            .dstQueueFamilyIndex = VK_QUEUE_FAMILY_IGNORED,
            .image = images[i],
            .subresourceRange = { VK_IMAGE_ASPECT_COLOR_BIT, 0, 1, 0, 1 },
        };
        d->next_cmd_pipeline_barrier(s->cbs[i],
            VK_PIPELINE_STAGE_TOP_OF_PIPE_BIT,
            VK_PIPELINE_STAGE_COMPUTE_SHADER_BIT,
            0, 0, NULL, 0, NULL, 1, &to_general);

        d->next_cmd_bind_pipeline(s->cbs[i], VK_PIPELINE_BIND_POINT_COMPUTE,
                                  g_hud_pipeline);
        d->next_cmd_bind_desc_sets(s->cbs[i], VK_PIPELINE_BIND_POINT_COMPUTE,
                                   g_hud_playout, 0, 1, &s->sets[i], 0, NULL);
        d->next_cmd_push_constants(s->cbs[i], g_hud_playout,
                                   VK_SHADER_STAGE_COMPUTE_BIT, 0,
                                   sizeof(push), &push);
        uint32_t gx = (uint32_t)((GB_HUD_COLS * push.cell[0] + 7) / 8);
        uint32_t gy = (uint32_t)((GB_HUD_ROWS * push.cell[1] + 7) / 8);
        d->next_cmd_dispatch(s->cbs[i], gx, gy, 1);

        VkImageMemoryBarrier to_present = to_general;
        to_present.srcAccessMask = VK_ACCESS_SHADER_WRITE_BIT;
        to_present.dstAccessMask = 0;
        to_present.oldLayout     = VK_IMAGE_LAYOUT_GENERAL;
        to_present.newLayout     = VK_IMAGE_LAYOUT_PRESENT_SRC_KHR;
        d->next_cmd_pipeline_barrier(s->cbs[i],
            VK_PIPELINE_STAGE_COMPUTE_SHADER_BIT,
            VK_PIPELINE_STAGE_BOTTOM_OF_PIPE_BIT,
            0, 0, NULL, 0, NULL, 1, &to_present);

        if (d->next_end_cmd_buffer(s->cbs[i]) != VK_SUCCESS) goto fail;

        VkSemaphoreCreateInfo sci = { .sType = VK_STRUCTURE_TYPE_SEMAPHORE_CREATE_INFO };
        if (d->next_create_semaphore(dev, &sci, NULL, &s->done[i]) != VK_SUCCESS)
            goto fail;
    }

    s->ready = 1;
    gbvk_log("overlay: ready on swapchain %ux%u (%u images)", w, h, n);
    return 1;

fail:
    gbvk_log("overlay: swapchain setup failed, overlay disabled for it");
    gbvk_hud_swap_free(d, s);
    return 0;
}

/* Submit the overlay pass for this present and chain its semaphore.
 * Returns 1 when *pInfo was rewritten to wait on ours. */
static int gbvk_hud_present(VkQueue queue, const VkPresentInfoKHR *pInfo,
                            VkPresentInfoKHR *out, VkSemaphore *sem_store)
{
    if (pInfo->swapchainCount != 1) return 0;

    int visible = 0, page = 0;
    gbvk_hud_poll_control(&visible, &page);
    if (!visible) return 0;

    pthread_mutex_lock(&g_hud_swap_mu);
    GbHudSwapState *s = hud_state_find(pInfo->pSwapchains[0]);
    if (!s || !s->ready) { pthread_mutex_unlock(&g_hud_swap_mu); return 0; }
    pthread_mutex_unlock(&g_hud_swap_mu);

    uint32_t img = pInfo->pImageIndices[0];
    if (img >= s->image_count) return 0;

    GbDevData *d = NULL;
    pthread_mutex_lock(&g_mutex);
    d = dev_find(s->device);
    pthread_mutex_unlock(&g_mutex);
    if (!d || !d->next_queue_submit) return 0;

    gbvk_hud_build_text((uint32_t *)s->text_map, page);

    VkPipelineStageFlags wait_stage[16];
    for (int i = 0; i < 16; i++) wait_stage[i] = VK_PIPELINE_STAGE_COMPUTE_SHADER_BIT;

    VkSubmitInfo si = {
        .sType                = VK_STRUCTURE_TYPE_SUBMIT_INFO,
        .waitSemaphoreCount   = pInfo->waitSemaphoreCount,
        .pWaitSemaphores      = pInfo->pWaitSemaphores,
        .pWaitDstStageMask    = wait_stage,
        .commandBufferCount   = 1,
        .pCommandBuffers      = &s->cbs[img],
        .signalSemaphoreCount = 1,
        .pSignalSemaphores    = &s->done[img],
    };
    if (d->next_queue_submit(queue, 1, &si, VK_NULL_HANDLE) != VK_SUCCESS)
        return 0;

    *sem_store = s->done[img];
    *out = *pInfo;
    out->waitSemaphoreCount = 1;
    out->pWaitSemaphores    = sem_store;
    return 1;
}

static VKAPI_ATTR VkResult VKAPI_CALL
gbvk_CreateSwapchainKHR(VkDevice                        device,
                        const VkSwapchainCreateInfoKHR *pCreateInfo,
                        const VkAllocationCallbacks    *pAllocator,
                        VkSwapchainKHR                 *pSwapchain)
{
    pthread_mutex_lock(&g_mutex);
    GbDevData *d = dev_find(device);
    PFN_vkCreateSwapchainKHR fn = d ? d->next_create_swapchain : NULL;
    pthread_mutex_unlock(&g_mutex);
    if (!fn) return VK_ERROR_INITIALIZATION_FAILED;

    /* Lazy NIS device-level init on first swapchain. */
    if (getenv("GREENBOOST_NIS") && getenv("GREENBOOST_NIS")[0] == '1')
        gbvk_nis_init_device(d, device);

    int want_dispatch = (d && d->nis_initialised &&
                         getenv("GREENBOOST_NIS_DISPATCH") &&
                         getenv("GREENBOOST_NIS_DISPATCH")[0] == '1');
    /* The overlay writes the swapchain image as a storage image exactly like
     * NIS does, so it needs the same STORAGE_BIT + mutable-format swapchain.
     * Gating that on NIS alone is why the overlay has to ask here rather than
     * later: image usage cannot be added after the swapchain exists. */
    int want_hud = gbvk_hud_enabled();
    int want_storage = want_dispatch || want_hud;

    VkResult r;
    VkFormat storage_fmt = pCreateInfo->imageFormat;

    if (want_storage) {
        /* NIS writes the swapchain image as a storage image.  We need:
         *   1. VK_IMAGE_USAGE_STORAGE_BIT on the swapchain images.
         *   2. VK_SWAPCHAIN_CREATE_MUTABLE_FORMAT_BIT_KHR + a format list
         *      so the driver accepts a UNORM storage view on an SRGB image.
         * Try with the modified info first; fall back to passthrough if the
         * driver rejects STORAGE_BIT (common on older compositors). */
        storage_fmt = nis_srgb_to_storage_format(pCreateInfo->imageFormat);
        VkFormat view_fmts[2] = { pCreateInfo->imageFormat, storage_fmt };
        VkImageFormatListCreateInfo fmt_list = {
            .sType           = VK_STRUCTURE_TYPE_IMAGE_FORMAT_LIST_CREATE_INFO,
            .pNext           = pCreateInfo->pNext,
            .viewFormatCount = (storage_fmt != pCreateInfo->imageFormat) ? 2 : 1,
            .pViewFormats    = view_fmts,
        };
        VkSwapchainCreateInfoKHR sci_mod = *pCreateInfo;
        sci_mod.imageUsage |= VK_IMAGE_USAGE_STORAGE_BIT;
        if (storage_fmt != pCreateInfo->imageFormat)
            sci_mod.flags |= VK_SWAPCHAIN_CREATE_MUTABLE_FORMAT_BIT_KHR;
        sci_mod.pNext = &fmt_list;

        r = fn(device, &sci_mod, pAllocator, pSwapchain);
        if (r != VK_SUCCESS) {
            /* WSI doesn't support STORAGE on swapchain images , disable both
             * post-process paths for this swapchain and create a plain one. */
            gbvk_log("swapchain STORAGE_BIT unsupported (VkResult %d) , NIS "
                     "dispatch and overlay disabled for this swapchain", (int)r);
            want_dispatch = 0;
            want_hud      = 0;
            storage_fmt   = pCreateInfo->imageFormat;
            r = fn(device, pCreateInfo, pAllocator, pSwapchain);
        }
    } else {
        r = fn(device, pCreateInfo, pAllocator, pSwapchain);
    }

    if (r == VK_SUCCESS && want_hud)
        gbvk_hud_swap_init(d, device, *pSwapchain, storage_fmt,
                           pCreateInfo->imageExtent.width,
                           pCreateInfo->imageExtent.height);

    if (r == VK_SUCCESS && d && d->nis_initialised) {
        if (want_dispatch) {
            pthread_mutex_lock(&g_nis_swap_mu);
            GbNisSwapState *s = nis_state_alloc_slot(*pSwapchain);
            pthread_mutex_unlock(&g_nis_swap_mu);
            if (s)
                gbvk_nis_swap_alloc(d, device, *pSwapchain, pCreateInfo, s, storage_fmt);
            else
                gbvk_log("NIS: swapchain table full , dispatch disabled for %p",
                         (void *)*pSwapchain);
        } else {
            gbvk_log("NIS: swapchain %p ready (dispatch disabled , set "
                     "GREENBOOST_NIS_DISPATCH=1 to enable post-process)",
                     (void *)*pSwapchain);
        }
    }

    /* A7: enable Reflex low-latency mode for this swapchain. */
    if (r == VK_SUCCESS && d && d->next_set_latency_sleep_mode) {
        VkLatencySleepModeInfoNV lsm = {
            .sType            = VK_STRUCTURE_TYPE_LATENCY_SLEEP_MODE_INFO_NV,
            .pNext            = NULL,
            .lowLatencyMode   = VK_TRUE,
            .lowLatencyBoost  = VK_FALSE,
            .minimumIntervalUs = 0,
        };
        VkResult lr = d->next_set_latency_sleep_mode(device, *pSwapchain, &lsm);
        if (lr == VK_SUCCESS)
            gbvk_log("Reflex: low-latency sleep mode enabled for swapchain %p",
                     (void *)*pSwapchain);
        else
            gbvk_log("Reflex: vkSetLatencySleepModeNV failed (%d) , markers still active",
                     (int)lr);
    }
    return r;
}

static VKAPI_ATTR void VKAPI_CALL
gbvk_DestroySwapchainKHR(VkDevice                       device,
                        VkSwapchainKHR                  swapchain,
                        const VkAllocationCallbacks    *pAllocator)
{
    pthread_mutex_lock(&g_mutex);
    GbDevData *d = dev_find(device);
    PFN_vkDestroySwapchainKHR fn = d ? d->next_destroy_swapchain : NULL;
    pthread_mutex_unlock(&g_mutex);

    /* Free our per-swapchain NIS resources before the upstream destroy
     * (which invalidates the VkImages we hold views on). */
    pthread_mutex_lock(&g_nis_swap_mu);
    GbNisSwapState *s = nis_state_find(swapchain);
    pthread_mutex_unlock(&g_nis_swap_mu);
    if (s && s->ready) gbvk_nis_swap_free(d, s);

    /* Same ordering rule for the overlay: its image views point at swapchain
     * images that the upstream destroy is about to invalidate. */
    pthread_mutex_lock(&g_hud_swap_mu);
    GbHudSwapState *hs = hud_state_find(swapchain);
    pthread_mutex_unlock(&g_hud_swap_mu);
    if (hs) gbvk_hud_swap_free(d, hs);

    if (fn) fn(device, swapchain, pAllocator);
}

/* ── PR-GGGG: NIS dispatch integration point ────────────────────────────
 *
 * Placeholder for the actual sharpen pass.  When fully wired:
 *
 *  for each swapchain in pPresentInfo->pSwapchains[i]:
 *     image_index = pPresentInfo->pImageIndices[i]
 *     1. vkBeginCommandBuffer on cmd_buf[image_index] (or use a fresh one)
 *     2. vkCmdPipelineBarrier , swapchain image PRESENT_SRC_KHR → TRANSFER_SRC
 *                              intermediate[image_index]        → TRANSFER_DST
 *     3. vkCmdCopyImage      swap → intermediate (full extent)
 *     4. vkCmdPipelineBarrier , intermediate → SHADER_READ
 *                              swapchain     → GENERAL (storage write)
 *     5. vkCmdBindPipeline (compute, d->nis_pipeline)
 *     6. vkCmdBindDescriptorSets (per-image set bound to intermediate+swap)
 *     7. vkCmdDispatch(ceil(W/32), ceil(H/32), 1)
 *     8. vkCmdPipelineBarrier , swapchain → PRESENT_SRC_KHR
 *     9. vkEndCommandBuffer
 *     10. vkQueueSubmit:
 *           waitSemaphoreCount = pPresentInfo->waitSemaphoreCount
 *           pWaitSemaphores    = pPresentInfo->pWaitSemaphores
 *           pWaitDstStageMask  = [COMPUTE_SHADER_BIT]*N
 *           signalSemaphoreCount = 1
 *           pSignalSemaphores    = &nis_signal[image_index]
 *  Replace pPresentInfo->pWaitSemaphores with the array of nis_signal[].
 *
 * The per-swapchain resources (intermediate images, memory, descriptor sets,
 * command buffers, semaphores) are allocated in gbvk_CreateSwapchainKHR
 * and freed in gbvk_DestroySwapchainKHR , see the TODO notes above.
 *
 * Why this is staged separately:
 *   • Bad sync in this path crashes the game.  The hooks need a real-game
 *     test pass before going live.
 *   • Memory budget: intermediate images at 4K RGBA8 × 3 frames ≈ 96 MB.
 *     Needs to be accounted against the heap-inflation budget.
 *
 * Until this is wired, GREENBOOST_NIS=1 builds + caches the pipeline so the
 * SPIR-V/driver compatibility can be validated, but no dispatch occurs.
 */
/* ── NIS dispatch implementation ──────────────────────────────────────
 *
 * Per-swapchain alloc creates intermediate images, descriptor sets, the
 * uniform buffer (populated with sharpen-only NIS defaults), command
 * buffers (one per swapchain image, pre-recorded), and signal semaphores.
 * The dispatch path on each vkQueuePresentKHR submits the pre-recorded
 * CB for the indicated image index, waiting on the present's wait
 * semaphores and signalling one of ours.  A fresh VkPresentInfoKHR is
 * built whose pWaitSemaphores points at our signals, and forwarded to
 * the next layer's vkQueuePresentKHR.
 *
 * Any allocation failure leaves nis_state.ready == 0 and the dispatch
 * silently passes through , the game continues without NIS.
 */

static GbNisSwapState *nis_state_alloc_slot(VkSwapchainKHR sc)
{
    for (int i = 0; i < GBVK_MAX_SWAPCHAINS; i++) {
        if (g_nis_swap[i].swapchain == VK_NULL_HANDLE) {
            memset(&g_nis_swap[i], 0, sizeof g_nis_swap[i]);
            g_nis_swap[i].swapchain = sc;
            return &g_nis_swap[i];
        }
    }
    return NULL;
}
static GbNisSwapState *nis_state_find(VkSwapchainKHR sc)
{
    for (int i = 0; i < GBVK_MAX_SWAPCHAINS; i++)
        if (g_nis_swap[i].swapchain == sc) return &g_nis_swap[i];
    return NULL;
}

/* Find a memory type matching the requirements bitmask + property flags. */
static uint32_t nis_find_mem_type(const VkPhysicalDeviceMemoryProperties *mp,
                                  uint32_t type_bits,
                                  VkMemoryPropertyFlags want)
{
    for (uint32_t i = 0; i < mp->memoryTypeCount; i++) {
        if (!(type_bits & (1u << i))) continue;
        if ((mp->memoryTypes[i].propertyFlags & want) == want) return i;
    }
    return UINT32_MAX;
}

/* Return the storage-compatible UNORM alias for common SRGB swapchain formats.
 * Storage images cannot use SRGB on most GPUs; we create a view with the UNORM
 * alias instead (requires VK_SWAPCHAIN_CREATE_MUTABLE_FORMAT_BIT_KHR on the
 * swapchain and a VkImageFormatListCreateInfo enumerating both formats). */
static VkFormat nis_srgb_to_storage_format(VkFormat fmt)
{
    switch (fmt) {
    case VK_FORMAT_B8G8R8A8_SRGB:         return VK_FORMAT_B8G8R8A8_UNORM;
    case VK_FORMAT_R8G8B8A8_SRGB:         return VK_FORMAT_R8G8B8A8_UNORM;
    case VK_FORMAT_A8B8G8R8_SRGB_PACK32:  return VK_FORMAT_A8B8G8R8_UNORM_PACK32;
    default:                               return fmt;
    }
}

/* Read GREENBOOST_NIS_SHARPNESS (0.0–1.0, default 0.5). */
static float nis_read_sharpness(void)
{
    const char *e = getenv("GREENBOOST_NIS_SHARPNESS");
    if (!e || !*e) return 0.5f;
    float v = strtof(e, NULL);
    if (v < 0.0f) v = 0.0f;
    if (v > 1.0f) v = 1.0f;
    return v;
}

/* Read GREENBOOST_NIS_SCALE (0.5–1.0, default 1.0).
 * 1.0 → sharpen-only; < 1.0 → NIS upscale from (W*scale × H*scale). */
static float nis_read_scale(void)
{
    const char *e = getenv("GREENBOOST_NIS_SCALE");
    if (!e || !*e) return 1.0f;
    float v = strtof(e, NULL);
    if (v < 0.5f) v = 0.5f;
    if (v > 1.0f) v = 1.0f;
    return v;
}

/* Populate a 256-byte uniform block for NIS.  std140 layout; offsets match
 * `uniform const_buffer` in NIS_Main.glsl.  Unused fields stay zero.
 *   dst_w/dst_h = swapchain (display) resolution.
 *   src_w/src_h = intermediate (render) resolution.
 *   For sharpen-only: src == dst. */
static void nis_fill_sharpen_defaults(void *p,
                                      uint32_t dst_w, uint32_t dst_h,
                                      uint32_t src_w, uint32_t src_h,
                                      float sharpness)
{
    float *f = (float *)p;
    memset(p, 0, 256);
    f[0]  = 1127.0f / 1024.0f;           /* kDetectRatio       */
    f[1]  =   64.0f / 1024.0f;           /* kDetectThres       */
    f[2]  = 2.0f;                        /* kMinContrastRatio  */
    f[3]  = 1.0f / 8.0f;                 /* kRatioNorm         */
    f[4]  = 1.0f;                        /* kContrastBoost     */
    f[5]  = 1.0f / 255.0f;              /* kEps (LDR)         */
    f[6]  = 0.45f;                       /* kSharpStartY       */
    f[7]  = 0.9f;                        /* kSharpEndY         */
    f[8]  = 0.0f;                        /* kSharpStrengthMin  */
    f[9]  = 1.0f;                        /* kSharpStrengthScale*/
    f[10] = 0.1f;                        /* kSharpLimitMin     */
    f[11] = 0.6f;                        /* kSharpLimitScale   */
    /* kScaleX/Y: ratio of destination to source (> 1.0 for upscale). */
    f[12] = (float)dst_w / (float)src_w; /* kScaleX            */
    f[13] = (float)dst_h / (float)src_h; /* kScaleY            */
    f[14] = 1.0f / (float)dst_w;         /* kDstNormX          */
    f[15] = 1.0f / (float)dst_h;         /* kDstNormY          */
    f[16] = 1.0f / (float)src_w;         /* kSrcNormX          */
    f[17] = 1.0f / (float)src_h;         /* kSrcNormY          */
    f[20] = 0.0f;  f[21] = 0.0f;
    f[22] = (float)src_w;                /* kInputViewportWidth  */
    f[23] = (float)src_h;                /* kInputViewportHeight */
    f[24] = 0.0f;  f[25] = 0.0f;
    f[26] = (float)dst_w;                /* kOutputViewportWidth */
    f[27] = (float)dst_h;                /* kOutputViewportHeight*/
    f[28] = sharpness;                   /* kSharpenSlider       */
}

static void gbvk_nis_swap_free(GbDevData *d, GbNisSwapState *s);

/* Allocate every per-swapchain resource.  All-or-nothing , leaves
 * s->ready=0 on partial failure and frees what it allocated. */
static void gbvk_nis_swap_alloc(GbDevData *d, VkDevice device,
                                VkSwapchainKHR sc,
                                const VkSwapchainCreateInfoKHR *ci,
                                GbNisSwapState *s,
                                VkFormat storage_fmt)
{
    if (!d || !d->nis_initialised) return;

    /* Resolve the swapchain's images. */
    uint32_t n = 0;
    if (d->next_get_swapchain_images(device, sc, &n, NULL) != VK_SUCCESS) return;
    if (n == 0 || n > GBVK_MAX_SWAP_IMAGES) return;
    if (d->next_get_swapchain_images(device, sc, &n, s->swap_images) != VK_SUCCESS) return;
    s->image_count = n;
    s->width  = ci->imageExtent.width;
    s->height = ci->imageExtent.height;
    s->format = ci->imageFormat;

    /* For upscale mode the intermediate images hold the game's render-resolution
     * content, which NIS then upscales to the swapchain (display) resolution.
     * For sharpen-only the intermediate is a full-res copy used as the NIS input. */
    uint32_t inter_w = d->nis_use_upscale
        ? (uint32_t)((float)s->width  * d->nis_scale + 0.5f)
        : s->width;
    uint32_t inter_h = d->nis_use_upscale
        ? (uint32_t)((float)s->height * d->nis_scale + 0.5f)
        : s->height;
    if (inter_w < 16) inter_w = 16;
    if (inter_h < 16) inter_h = 16;

    /* Storage views into each swapchain image.  Use storage_fmt (the UNORM
     * alias) because SRGB storage is unsupported on most GPUs.  The swapchain
     * was created with MUTABLE_FORMAT + a format list containing storage_fmt,
     * so this view is valid even when storage_fmt != ci->imageFormat. */
    for (uint32_t i = 0; i < n; i++) {
        VkImageViewCreateInfo ivci = {
            .sType    = VK_STRUCTURE_TYPE_IMAGE_VIEW_CREATE_INFO,
            .image    = s->swap_images[i],
            .viewType = VK_IMAGE_VIEW_TYPE_2D,
            .format   = storage_fmt,
            .subresourceRange = { VK_IMAGE_ASPECT_COLOR_BIT, 0, 1, 0, 1 },
        };
        if (d->next_create_image_view(device, &ivci, NULL,
                                      &s->swap_views_storage[i]) != VK_SUCCESS) {
            gbvk_nis_swap_free(d, s); return;
        }
    }

    /* Intermediate images , inter_w × inter_h, TRANSFER_DST + SAMPLED.
     * In sharpen mode: same size as the swapchain (full-res copy → NIS sharpen).
     * In upscale mode: render resolution (game's lower-res frame → NIS upscale). */
    for (uint32_t i = 0; i < n; i++) {
        VkImageCreateInfo ici = {
            .sType         = VK_STRUCTURE_TYPE_IMAGE_CREATE_INFO,
            .imageType     = VK_IMAGE_TYPE_2D,
            .format        = ci->imageFormat,
            .extent        = { inter_w, inter_h, 1 },
            .mipLevels     = 1, .arrayLayers = 1,
            .samples       = VK_SAMPLE_COUNT_1_BIT,
            .tiling        = VK_IMAGE_TILING_OPTIMAL,
            .usage         = VK_IMAGE_USAGE_TRANSFER_DST_BIT |
                             VK_IMAGE_USAGE_SAMPLED_BIT,
            .sharingMode   = VK_SHARING_MODE_EXCLUSIVE,
            .initialLayout = VK_IMAGE_LAYOUT_UNDEFINED,
        };
        if (d->next_create_image(device, &ici, NULL,
                                 &s->inter_images[i]) != VK_SUCCESS) {
            gbvk_nis_swap_free(d, s); return;
        }
        VkMemoryRequirements mr;
        d->next_get_image_mem_req(device, s->inter_images[i], &mr);
        uint32_t mt = nis_find_mem_type(&d->mem_props, mr.memoryTypeBits,
                                        VK_MEMORY_PROPERTY_DEVICE_LOCAL_BIT);
        if (mt == UINT32_MAX) { gbvk_nis_swap_free(d, s); return; }
        VkMemoryAllocateInfo mai = {
            .sType           = VK_STRUCTURE_TYPE_MEMORY_ALLOCATE_INFO,
            .allocationSize  = mr.size,
            .memoryTypeIndex = mt,
        };
        if (d->next_alloc_mem(device, &mai, NULL,
                              &s->inter_memory[i]) != VK_SUCCESS) {
            gbvk_nis_swap_free(d, s); return;
        }
        if (d->next_bind_image_memory_call(device, s->inter_images[i],
                                            s->inter_memory[i], 0) != VK_SUCCESS) {
            gbvk_nis_swap_free(d, s); return;
        }
        VkImageViewCreateInfo ivci = {
            .sType    = VK_STRUCTURE_TYPE_IMAGE_VIEW_CREATE_INFO,
            .image    = s->inter_images[i],
            .viewType = VK_IMAGE_VIEW_TYPE_2D,
            .format   = ci->imageFormat,
            .subresourceRange = { VK_IMAGE_ASPECT_COLOR_BIT, 0, 1, 0, 1 },
        };
        if (d->next_create_image_view(device, &ivci, NULL,
                                      &s->inter_views_sampled[i]) != VK_SUCCESS) {
            gbvk_nis_swap_free(d, s); return;
        }
    }

    /* Uniform buffer for NIS constants , 256 bytes, HOST_VISIBLE. */
    VkBufferCreateInfo bci = {
        .sType       = VK_STRUCTURE_TYPE_BUFFER_CREATE_INFO,
        .size        = 256,
        .usage       = VK_BUFFER_USAGE_UNIFORM_BUFFER_BIT,
        .sharingMode = VK_SHARING_MODE_EXCLUSIVE,
    };
    if (d->next_create_buffer(device, &bci, NULL, &s->ub_buffer) != VK_SUCCESS) {
        gbvk_nis_swap_free(d, s); return;
    }
    VkMemoryRequirements bmr;
    d->next_get_buf_mem_req(device, s->ub_buffer, &bmr);
    uint32_t bmt = nis_find_mem_type(&d->mem_props, bmr.memoryTypeBits,
                                     VK_MEMORY_PROPERTY_HOST_VISIBLE_BIT |
                                     VK_MEMORY_PROPERTY_HOST_COHERENT_BIT);
    if (bmt == UINT32_MAX) { gbvk_nis_swap_free(d, s); return; }
    VkMemoryAllocateInfo bmai = {
        .sType           = VK_STRUCTURE_TYPE_MEMORY_ALLOCATE_INFO,
        .allocationSize  = bmr.size,
        .memoryTypeIndex = bmt,
    };
    if (d->next_alloc_mem(device, &bmai, NULL, &s->ub_memory) != VK_SUCCESS) {
        gbvk_nis_swap_free(d, s); return;
    }
    if (d->next_bind_buf_memory(device, s->ub_buffer, s->ub_memory, 0) != VK_SUCCESS) {
        gbvk_nis_swap_free(d, s); return;
    }
    void *mapped = NULL;
    if (d->next_map_memory(device, s->ub_memory, 0, 256, 0, &mapped) != VK_SUCCESS) {
        gbvk_nis_swap_free(d, s); return;
    }
    nis_fill_sharpen_defaults(mapped,
                              s->width, s->height,      /* dst = swapchain */
                              inter_w, inter_h,          /* src = render res */
                              nis_read_sharpness());
    d->next_unmap_memory(device, s->ub_memory);

    /* Descriptor pool sized to fit n sets of the 6-binding layout. */
    VkDescriptorPoolSize psz[4] = {
        { VK_DESCRIPTOR_TYPE_UNIFORM_BUFFER, n     },
        { VK_DESCRIPTOR_TYPE_SAMPLER,        n     },
        { VK_DESCRIPTOR_TYPE_SAMPLED_IMAGE,  n * 3 },  /* in + 2x coef placeholders */
        { VK_DESCRIPTOR_TYPE_STORAGE_IMAGE,  n     },
    };
    VkDescriptorPoolCreateInfo dpci = {
        .sType         = VK_STRUCTURE_TYPE_DESCRIPTOR_POOL_CREATE_INFO,
        .maxSets       = n,
        .poolSizeCount = 4,
        .pPoolSizes    = psz,
    };
    if (d->next_create_desc_pool(device, &dpci, NULL, &s->desc_pool) != VK_SUCCESS) {
        gbvk_nis_swap_free(d, s); return;
    }
    VkDescriptorSetLayout layouts[GBVK_MAX_SWAP_IMAGES];
    for (uint32_t i = 0; i < n; i++) layouts[i] = d->nis_dsl;
    VkDescriptorSetAllocateInfo dsai = {
        .sType              = VK_STRUCTURE_TYPE_DESCRIPTOR_SET_ALLOCATE_INFO,
        .descriptorPool     = s->desc_pool,
        .descriptorSetCount = n,
        .pSetLayouts        = layouts,
    };
    if (d->next_alloc_desc_sets(device, &dsai, s->desc_sets) != VK_SUCCESS) {
        gbvk_nis_swap_free(d, s); return;
    }

    /* Populate descriptor sets , same uniform + sampler for all,
     * per-image input/output views.  Bindings 4/5 (coef textures) get
     * the input view too , the sharpen shader ignores them but the
     * descriptor must be non-null. */
    for (uint32_t i = 0; i < n; i++) {
        VkDescriptorBufferInfo bi = {
            .buffer = s->ub_buffer, .offset = 0, .range = 256,
        };
        VkDescriptorImageInfo si_in = {
            .sampler = VK_NULL_HANDLE,
            .imageView = s->inter_views_sampled[i],
            .imageLayout = VK_IMAGE_LAYOUT_SHADER_READ_ONLY_OPTIMAL,
        };
        VkDescriptorImageInfo si_smp = {
            .sampler = d->nis_sampler,
            .imageView = VK_NULL_HANDLE,
            .imageLayout = VK_IMAGE_LAYOUT_UNDEFINED,
        };
        VkDescriptorImageInfo si_out = {
            .sampler = VK_NULL_HANDLE,
            .imageView = s->swap_views_storage[i],
            .imageLayout = VK_IMAGE_LAYOUT_GENERAL,
        };
        VkWriteDescriptorSet w[6] = {
            { .sType = VK_STRUCTURE_TYPE_WRITE_DESCRIPTOR_SET,
              .dstSet = s->desc_sets[i], .dstBinding = 0, .descriptorCount = 1,
              .descriptorType = VK_DESCRIPTOR_TYPE_UNIFORM_BUFFER,
              .pBufferInfo = &bi },
            { .sType = VK_STRUCTURE_TYPE_WRITE_DESCRIPTOR_SET,
              .dstSet = s->desc_sets[i], .dstBinding = 1, .descriptorCount = 1,
              .descriptorType = VK_DESCRIPTOR_TYPE_SAMPLER,
              .pImageInfo = &si_smp },
            { .sType = VK_STRUCTURE_TYPE_WRITE_DESCRIPTOR_SET,
              .dstSet = s->desc_sets[i], .dstBinding = 2, .descriptorCount = 1,
              .descriptorType = VK_DESCRIPTOR_TYPE_SAMPLED_IMAGE,
              .pImageInfo = &si_in },
            { .sType = VK_STRUCTURE_TYPE_WRITE_DESCRIPTOR_SET,
              .dstSet = s->desc_sets[i], .dstBinding = 3, .descriptorCount = 1,
              .descriptorType = VK_DESCRIPTOR_TYPE_STORAGE_IMAGE,
              .pImageInfo = &si_out },
            { .sType = VK_STRUCTURE_TYPE_WRITE_DESCRIPTOR_SET,
              .dstSet = s->desc_sets[i], .dstBinding = 4, .descriptorCount = 1,
              .descriptorType = VK_DESCRIPTOR_TYPE_SAMPLED_IMAGE,
              .pImageInfo = &si_in },   /* coef placeholder */
            { .sType = VK_STRUCTURE_TYPE_WRITE_DESCRIPTOR_SET,
              .dstSet = s->desc_sets[i], .dstBinding = 5, .descriptorCount = 1,
              .descriptorType = VK_DESCRIPTOR_TYPE_SAMPLED_IMAGE,
              .pImageInfo = &si_in },   /* coef placeholder */
        };
        d->next_update_desc_sets(device, 6, w, 0, NULL);
    }

    /* Command pool + buffers , one CB per swapchain image, pre-recorded. */
    VkCommandPoolCreateInfo cpci = {
        .sType            = VK_STRUCTURE_TYPE_COMMAND_POOL_CREATE_INFO,
        .flags            = 0,
        .queueFamilyIndex = d->nis_queue_family,
    };
    if (d->next_create_cmd_pool(device, &cpci, NULL, &s->cmd_pool) != VK_SUCCESS) {
        gbvk_nis_swap_free(d, s); return;
    }
    VkCommandBufferAllocateInfo cbai = {
        .sType              = VK_STRUCTURE_TYPE_COMMAND_BUFFER_ALLOCATE_INFO,
        .commandPool        = s->cmd_pool,
        .level              = VK_COMMAND_BUFFER_LEVEL_PRIMARY,
        .commandBufferCount = n,
    };
    if (d->next_alloc_cmd_buffers(device, &cbai, s->cmd_buffers) != VK_SUCCESS) {
        gbvk_nis_swap_free(d, s); return;
    }

    /* Pre-record per-image CBs: copy swap→inter, NIS dispatch, transition. */
    for (uint32_t i = 0; i < n; i++) {
        VkCommandBufferBeginInfo bbi = {
            .sType = VK_STRUCTURE_TYPE_COMMAND_BUFFER_BEGIN_INFO,
        };
        if (d->next_begin_cmd_buffer(s->cmd_buffers[i], &bbi) != VK_SUCCESS) {
            gbvk_nis_swap_free(d, s); return;
        }

        /* 1) swap[i] PRESENT_SRC_KHR → TRANSFER_SRC_OPTIMAL
         *    inter[i] UNDEFINED      → TRANSFER_DST_OPTIMAL */
        VkImageMemoryBarrier b1[2] = {
            { .sType = VK_STRUCTURE_TYPE_IMAGE_MEMORY_BARRIER,
              .srcAccessMask = 0,
              .dstAccessMask = VK_ACCESS_TRANSFER_READ_BIT,
              .oldLayout = VK_IMAGE_LAYOUT_PRESENT_SRC_KHR,
              .newLayout = VK_IMAGE_LAYOUT_TRANSFER_SRC_OPTIMAL,
              .srcQueueFamilyIndex = VK_QUEUE_FAMILY_IGNORED,
              .dstQueueFamilyIndex = VK_QUEUE_FAMILY_IGNORED,
              .image = s->swap_images[i],
              .subresourceRange = { VK_IMAGE_ASPECT_COLOR_BIT, 0, 1, 0, 1 } },
            { .sType = VK_STRUCTURE_TYPE_IMAGE_MEMORY_BARRIER,
              .srcAccessMask = 0,
              .dstAccessMask = VK_ACCESS_TRANSFER_WRITE_BIT,
              .oldLayout = VK_IMAGE_LAYOUT_UNDEFINED,
              .newLayout = VK_IMAGE_LAYOUT_TRANSFER_DST_OPTIMAL,
              .srcQueueFamilyIndex = VK_QUEUE_FAMILY_IGNORED,
              .dstQueueFamilyIndex = VK_QUEUE_FAMILY_IGNORED,
              .image = s->inter_images[i],
              .subresourceRange = { VK_IMAGE_ASPECT_COLOR_BIT, 0, 1, 0, 1 } },
        };
        d->next_cmd_pipeline_barrier(s->cmd_buffers[i],
            VK_PIPELINE_STAGE_TOP_OF_PIPE_BIT,
            VK_PIPELINE_STAGE_TRANSFER_BIT,
            0, 0, NULL, 0, NULL, 2, b1);

        /* 2) Copy swap → inter at inter resolution.
         *    Sharpen: copy full frame (inter == swap extent).
         *    Upscale: copy the game's render-res region (inter < swap). */
        VkImageCopy region = {
            .srcSubresource = { VK_IMAGE_ASPECT_COLOR_BIT, 0, 0, 1 },
            .dstSubresource = { VK_IMAGE_ASPECT_COLOR_BIT, 0, 0, 1 },
            .extent = { inter_w, inter_h, 1 },
        };
        d->next_cmd_copy_image(s->cmd_buffers[i],
            s->swap_images[i],   VK_IMAGE_LAYOUT_TRANSFER_SRC_OPTIMAL,
            s->inter_images[i],  VK_IMAGE_LAYOUT_TRANSFER_DST_OPTIMAL,
            1, &region);

        /* 3) inter → SHADER_READ_ONLY ;  swap → GENERAL (for storage write). */
        VkImageMemoryBarrier b2[2] = {
            { .sType = VK_STRUCTURE_TYPE_IMAGE_MEMORY_BARRIER,
              .srcAccessMask = VK_ACCESS_TRANSFER_WRITE_BIT,
              .dstAccessMask = VK_ACCESS_SHADER_READ_BIT,
              .oldLayout = VK_IMAGE_LAYOUT_TRANSFER_DST_OPTIMAL,
              .newLayout = VK_IMAGE_LAYOUT_SHADER_READ_ONLY_OPTIMAL,
              .srcQueueFamilyIndex = VK_QUEUE_FAMILY_IGNORED,
              .dstQueueFamilyIndex = VK_QUEUE_FAMILY_IGNORED,
              .image = s->inter_images[i],
              .subresourceRange = { VK_IMAGE_ASPECT_COLOR_BIT, 0, 1, 0, 1 } },
            { .sType = VK_STRUCTURE_TYPE_IMAGE_MEMORY_BARRIER,
              .srcAccessMask = VK_ACCESS_TRANSFER_READ_BIT,
              .dstAccessMask = VK_ACCESS_SHADER_WRITE_BIT,
              .oldLayout = VK_IMAGE_LAYOUT_TRANSFER_SRC_OPTIMAL,
              .newLayout = VK_IMAGE_LAYOUT_GENERAL,
              .srcQueueFamilyIndex = VK_QUEUE_FAMILY_IGNORED,
              .dstQueueFamilyIndex = VK_QUEUE_FAMILY_IGNORED,
              .image = s->swap_images[i],
              .subresourceRange = { VK_IMAGE_ASPECT_COLOR_BIT, 0, 1, 0, 1 } },
        };
        d->next_cmd_pipeline_barrier(s->cmd_buffers[i],
            VK_PIPELINE_STAGE_TRANSFER_BIT,
            VK_PIPELINE_STAGE_COMPUTE_SHADER_BIT,
            0, 0, NULL, 0, NULL, 2, b2);

        /* 4) Dispatch NIS.
         *    Sharpen: 32×32 blocks → ceil(dstW/32) × ceil(dstH/32) groups.
         *    Upscale: 32×24 blocks → ceil(dstW/32) × ceil(dstH/24) groups.
         *    The NIS shader iterates over the output (display) resolution. */
        d->next_cmd_bind_pipeline(s->cmd_buffers[i],
            VK_PIPELINE_BIND_POINT_COMPUTE, d->nis_pipeline);
        d->next_cmd_bind_desc_sets(s->cmd_buffers[i],
            VK_PIPELINE_BIND_POINT_COMPUTE, d->nis_player,
            0, 1, &s->desc_sets[i], 0, NULL);
        uint32_t blk_h = d->nis_use_upscale ? 24 : 32;
        uint32_t gx = (s->width  + 31)       / 32;
        uint32_t gy = (s->height + blk_h - 1) / blk_h;
        d->next_cmd_dispatch(s->cmd_buffers[i], gx, gy, 1);

        /* 5) swap GENERAL → PRESENT_SRC_KHR. */
        VkImageMemoryBarrier b3 = {
            .sType = VK_STRUCTURE_TYPE_IMAGE_MEMORY_BARRIER,
            .srcAccessMask = VK_ACCESS_SHADER_WRITE_BIT,
            .dstAccessMask = 0,
            .oldLayout = VK_IMAGE_LAYOUT_GENERAL,
            .newLayout = VK_IMAGE_LAYOUT_PRESENT_SRC_KHR,
            .srcQueueFamilyIndex = VK_QUEUE_FAMILY_IGNORED,
            .dstQueueFamilyIndex = VK_QUEUE_FAMILY_IGNORED,
            .image = s->swap_images[i],
            .subresourceRange = { VK_IMAGE_ASPECT_COLOR_BIT, 0, 1, 0, 1 },
        };
        d->next_cmd_pipeline_barrier(s->cmd_buffers[i],
            VK_PIPELINE_STAGE_COMPUTE_SHADER_BIT,
            VK_PIPELINE_STAGE_BOTTOM_OF_PIPE_BIT,
            0, 0, NULL, 0, NULL, 1, &b3);

        if (d->next_end_cmd_buffer(s->cmd_buffers[i]) != VK_SUCCESS) {
            gbvk_nis_swap_free(d, s); return;
        }
    }

    /* Per-image done semaphore , used to chain into the present's wait. */
    for (uint32_t i = 0; i < n; i++) {
        VkSemaphoreCreateInfo sci_sem = {
            .sType = VK_STRUCTURE_TYPE_SEMAPHORE_CREATE_INFO,
        };
        if (d->next_create_semaphore(device, &sci_sem, NULL,
                                     &s->done_semaphores[i]) != VK_SUCCESS) {
            gbvk_nis_swap_free(d, s); return;
        }
    }

    s->device = device;
    s->ready  = 1;
    gbvk_log("NIS: swapchain %p resources ready , %u image(s) at %ux%u",
             (void *)sc, n, s->width, s->height);
}

static void gbvk_nis_swap_free(GbDevData *d, GbNisSwapState *s)
{
    if (!d || !s) return;
    VkDevice dev = s->device;
    for (uint32_t i = 0; i < GBVK_MAX_SWAP_IMAGES; i++) {
        if (s->done_semaphores[i] && d->next_destroy_semaphore)
            d->next_destroy_semaphore(dev, s->done_semaphores[i], NULL);
        if (s->swap_views_storage[i] && d->next_destroy_image_view)
            d->next_destroy_image_view(dev, s->swap_views_storage[i], NULL);
        if (s->inter_views_sampled[i] && d->next_destroy_image_view)
            d->next_destroy_image_view(dev, s->inter_views_sampled[i], NULL);
        if (s->inter_images[i] && d->next_destroy_image)
            d->next_destroy_image(dev, s->inter_images[i], NULL);
        if (s->inter_memory[i] && d->next_free_mem)
            d->next_free_mem(dev, s->inter_memory[i], NULL);
    }
    if (s->cmd_pool && d->next_destroy_cmd_pool)
        d->next_destroy_cmd_pool(dev, s->cmd_pool, NULL);
    if (s->desc_pool && d->next_destroy_desc_pool)
        d->next_destroy_desc_pool(dev, s->desc_pool, NULL);
    if (s->ub_buffer && d->next_destroy_buffer)
        d->next_destroy_buffer(dev, s->ub_buffer, NULL);
    if (s->ub_memory && d->next_free_mem)
        d->next_free_mem(dev, s->ub_memory, NULL);
    memset(s, 0, sizeof *s);
}

/* Called from gbvk_QueuePresentKHR when NIS dispatch is enabled.  Submits
 * the pre-recorded CB for each (swapchain, image_index) and rewrites the
 * present-info to wait on our signal semaphores.  Returns the (possibly
 * rewritten) info to pass to the next layer, plus storage backing it. */
typedef struct {
    VkPresentInfoKHR     info;
    VkSemaphore          waits[GBVK_MAX_SWAPCHAINS];
} GbNisPresentRewrite;

static int gbvk_nis_dispatch_present_v2(VkQueue queue,
                                        const VkPresentInfoKHR *pInfo,
                                        GbNisPresentRewrite *out)
{
    /* NIS dispatch is limited to single-swapchain presents.  Multi-swapchain
     * presents (rare) would require per-swapchain acquire-semaphore splitting
     * which the loader doesn't expose , fall back to passthrough for safety. */
    if (pInfo->swapchainCount != 1)
        return 0;
    GbNisSwapState *states[GBVK_MAX_SWAPCHAINS] = {0};
    GbDevData      *d = NULL;
    pthread_mutex_lock(&g_nis_swap_mu);
    for (uint32_t i = 0; i < pInfo->swapchainCount; i++) {
        states[i] = nis_state_find(pInfo->pSwapchains[i]);
        if (!states[i] || !states[i]->ready) {
            pthread_mutex_unlock(&g_nis_swap_mu);
            return 0;
        }
    }
    pthread_mutex_unlock(&g_nis_swap_mu);
    pthread_mutex_lock(&g_mutex);
    if (states[0]) d = dev_find(states[0]->device);
    pthread_mutex_unlock(&g_mutex);
    if (!d || !d->next_queue_submit) return 0;

    /* Submit one CB per swapchain.  The first submit waits on the
     * caller's pWaitSemaphores; subsequent submits are independent
     * (they wait on nothing of ours but go to the same queue, so
     * they're ordered).  Each signals the corresponding done-sem. */
    /* Wait at TRANSFER (first barrier copies swap→inter) AND COMPUTE_SHADER
     * (NIS dispatch reads inter then writes swap as storage) so the driver
     * correctly orders the acquire semaphore before both stages. */
    VkPipelineStageFlags wait_stage[16];
    for (int i = 0; i < 16; i++)
        wait_stage[i] = VK_PIPELINE_STAGE_TRANSFER_BIT |
                        VK_PIPELINE_STAGE_COMPUTE_SHADER_BIT;

    for (uint32_t i = 0; i < pInfo->swapchainCount; i++) {
        GbNisSwapState *s = states[i];
        uint32_t img = pInfo->pImageIndices[i];
        if (img >= s->image_count) return 0;
        VkSubmitInfo si = {
            .sType                = VK_STRUCTURE_TYPE_SUBMIT_INFO,
            .commandBufferCount   = 1,
            .pCommandBuffers      = &s->cmd_buffers[img],
            .signalSemaphoreCount = 1,
            .pSignalSemaphores    = &s->done_semaphores[img],
        };
        if (i == 0) {
            si.waitSemaphoreCount = pInfo->waitSemaphoreCount;
            si.pWaitSemaphores    = pInfo->pWaitSemaphores;
            si.pWaitDstStageMask  = wait_stage;
        }
        if (d->next_queue_submit(queue, 1, &si, VK_NULL_HANDLE) != VK_SUCCESS)
            return 0;
        out->waits[i] = s->done_semaphores[img];
    }

    out->info = *pInfo;
    out->info.waitSemaphoreCount = pInfo->swapchainCount;
    out->info.pWaitSemaphores    = out->waits;
    return 1;
}

__attribute__((unused))
static VkResult gbvk_nis_dispatch_present(VkQueue              queue,
                                          const VkPresentInfoKHR *pInfo)
{
    (void)queue; (void)pInfo;
    return VK_SUCCESS;
}

/* ── Hook: vkCreateInstance ────────────────────────────────────────────── */

static VKAPI_ATTR VkResult VKAPI_CALL
gbvk_CreateInstance(const VkInstanceCreateInfo  *pCreateInfo,
                    const VkAllocationCallbacks *pAllocator,
                    VkInstance                  *pInstance)
{
    VkLayerInstanceCreateInfo *ldci =
        (VkLayerInstanceCreateInfo *)pCreateInfo->pNext;
    while (ldci && !(ldci->sType == VK_STRUCTURE_TYPE_LOADER_INSTANCE_CREATE_INFO &&
                     ldci->function == VK_LAYER_LINK_INFO))
        ldci = (VkLayerInstanceCreateInfo *)ldci->pNext;
    if (!ldci) return VK_ERROR_INITIALIZATION_FAILED;

    PFN_vkGetInstanceProcAddr next_gipa = ldci->u.pLayerInfo->pfnNextGetInstanceProcAddr;
    ldci->u.pLayerInfo = ldci->u.pLayerInfo->pNext;

    PFN_vkCreateInstance next_ci =
        (PFN_vkCreateInstance)next_gipa(VK_NULL_HANDLE, "vkCreateInstance");
    if (!next_ci) return VK_ERROR_INITIALIZATION_FAILED;

    VkResult res = next_ci(pCreateInfo, pAllocator, pInstance);
    if (res != VK_SUCCESS) return res;

    PFN_vkDestroyInstance next_di = (PFN_vkDestroyInstance)
        next_gipa(*pInstance, "vkDestroyInstance");
    PFN_vkGetPhysicalDeviceMemoryProperties next_props =
        (PFN_vkGetPhysicalDeviceMemoryProperties)
        next_gipa(*pInstance, "vkGetPhysicalDeviceMemoryProperties");
    PFN_vkGetPhysicalDeviceMemoryProperties2 next_props2 =
        (PFN_vkGetPhysicalDeviceMemoryProperties2)
        next_gipa(*pInstance, "vkGetPhysicalDeviceMemoryProperties2");
    g_next_enum_dev_ext = (PFN_vkEnumerateDeviceExtensionProperties)
        next_gipa(*pInstance, "vkEnumerateDeviceExtensionProperties");

    pthread_mutex_lock(&g_mutex);
    GbInstData *d = inst_alloc(*pInstance);
    if (d) {
        d->next_gipa              = next_gipa;
        d->next_destroy_instance  = next_di;
        d->next_get_mem_props     = next_props;
        d->next_get_mem_props2    = next_props2;
    }
    pthread_mutex_unlock(&g_mutex);

    /* PR-GGGG: log application identification + key extensions on first
     * instance creation.  Helps post-mortem: a single line per session tells
     * you "GreenBoost saw FF XVI v1.04 ask for VK_EXT_graphics_pipeline_library
     * + VK_NV_low_latency2". */
    const char *app_name = "unknown";
    uint32_t    app_ver  = 0;
    const char *eng_name = "unknown";
    if (pCreateInfo->pApplicationInfo) {
        if (pCreateInfo->pApplicationInfo->pApplicationName)
            app_name = pCreateInfo->pApplicationInfo->pApplicationName;
        if (pCreateInfo->pApplicationInfo->pEngineName)
            eng_name = pCreateInfo->pApplicationInfo->pEngineName;
        app_ver = pCreateInfo->pApplicationInfo->applicationVersion;
    }
    int has_gpl = 0, has_prio = 0, has_lowlat = 0, has_pageable = 0;
    for (uint32_t i = 0; i < pCreateInfo->enabledExtensionCount; i++) {
        const char *e = pCreateInfo->ppEnabledExtensionNames[i];
        if (!e) continue;
        if (strstr(e, "graphics_pipeline_library"))  has_gpl++;
        if (strstr(e, "memory_priority"))            has_prio++;
        if (strstr(e, "low_latency"))                has_lowlat++;
        if (strstr(e, "pageable_device_local"))      has_pageable++;
    }
    gbvk_log("CreateInstance: app='%s' v%u.%u.%u engine='%s' | "
             "GPL=%d prio=%d lowlat=%d pageable=%d",
             app_name,
             (app_ver >> 22) & 0x7F, (app_ver >> 12) & 0x3FF, app_ver & 0xFFF,
             eng_name, has_gpl, has_prio, has_lowlat, has_pageable);
    return VK_SUCCESS;
}

static VKAPI_ATTR void VKAPI_CALL
gbvk_DestroyInstance(VkInstance instance, const VkAllocationCallbacks *pAllocator)
{
    pthread_mutex_lock(&g_mutex);
    GbInstData *d = inst_find(instance);
    PFN_vkDestroyInstance fn = d ? d->next_destroy_instance : NULL;
    inst_free(instance);
    pthread_mutex_unlock(&g_mutex);
    if (fn) fn(instance, pAllocator);
}

/* ── Hook: vkCreateDevice ──────────────────────────────────────────────── */

static VKAPI_ATTR VkResult VKAPI_CALL
gbvk_CreateDevice(VkPhysicalDevice             physDev,
                  const VkDeviceCreateInfo    *pCreateInfo,
                  const VkAllocationCallbacks *pAllocator,
                  VkDevice                    *pDevice)
{
    VkLayerDeviceCreateInfo *ldci =
        (VkLayerDeviceCreateInfo *)pCreateInfo->pNext;
    while (ldci && !(ldci->sType == VK_STRUCTURE_TYPE_LOADER_DEVICE_CREATE_INFO &&
                     ldci->function == VK_LAYER_LINK_INFO))
        ldci = (VkLayerDeviceCreateInfo *)ldci->pNext;
    if (!ldci) return VK_ERROR_INITIALIZATION_FAILED;

    PFN_vkGetInstanceProcAddr next_gipa = ldci->u.pLayerInfo->pfnNextGetInstanceProcAddr;
    PFN_vkGetDeviceProcAddr   next_gdpa = ldci->u.pLayerInfo->pfnNextGetDeviceProcAddr;
    ldci->u.pLayerInfo = ldci->u.pLayerInfo->pNext;

    PFN_vkCreateDevice next_cd =
        (PFN_vkCreateDevice)next_gipa(VK_NULL_HANDLE, "vkCreateDevice");
    if (!next_cd) return VK_ERROR_INITIALIZATION_FAILED;

    /* PR-GGGG: inject VK_EXT_global_priority HIGH into each queue create
     * info so the NVIDIA driver schedules the game's graphics/compute work
     * ahead of background Vulkan apps (compositor, browser GPU process).
     *
     * Best-effort: if the driver returns VK_ERROR_NOT_PERMITTED_KHR (lacks
     * CAP_SYS_NICE or the extension), we retry once without the chain.
     * Opt-out: GREENBOOST_VK_QUEUE_PRIORITY=0
     *
     * Stack-allocate the modified queue infos array , apps almost never
     * create more than 4 queue families; we cap at 8 for safety. */
    int want_priority = 1;
    {
        const char *e = getenv("GREENBOOST_VK_QUEUE_PRIORITY");
        if (e && strcmp(e, "0") == 0) want_priority = 0;
    }

    VkDeviceCreateInfo dci_mod = *pCreateInfo;
    VkDeviceQueueCreateInfo queues_mod[8];
    VkDeviceQueueGlobalPriorityCreateInfoKHR prio_chain[8];
    int n_q = (int)pCreateInfo->queueCreateInfoCount;
    if (want_priority && n_q > 0 && n_q <= 8) {
        for (int i = 0; i < n_q; i++) {
            queues_mod[i] = pCreateInfo->pQueueCreateInfos[i];
            /* Skip if the app already supplied a priority struct. */
            const VkBaseInStructure *p = (const VkBaseInStructure *)queues_mod[i].pNext;
            int has_prio = 0;
            while (p) {
                if (p->sType == VK_STRUCTURE_TYPE_DEVICE_QUEUE_GLOBAL_PRIORITY_CREATE_INFO_KHR ||
                    p->sType == VK_STRUCTURE_TYPE_DEVICE_QUEUE_GLOBAL_PRIORITY_CREATE_INFO_EXT) {
                    has_prio = 1; break;
                }
                p = p->pNext;
            }
            if (!has_prio) {
                prio_chain[i].sType         = VK_STRUCTURE_TYPE_DEVICE_QUEUE_GLOBAL_PRIORITY_CREATE_INFO_KHR;
                prio_chain[i].pNext         = queues_mod[i].pNext;
                prio_chain[i].globalPriority = VK_QUEUE_GLOBAL_PRIORITY_HIGH_KHR;
                queues_mod[i].pNext = &prio_chain[i];
            }
        }
        dci_mod.pQueueCreateInfos = queues_mod;
    } else if (n_q > 8) {
        /* Don't try to inject if the app uses more queue families than we
         * have stack room for , falls through to passthrough. */
        want_priority = 0;
    }

    /* PR-GGGG: enable VK_EXT_pageable_device_local_memory ourselves.
     *
     * Everything below in this file that lowers the priority of a T2/T3
     * spill allocation goes through vkSetDeviceMemoryPriorityEXT, and that
     * entry point only exists when the extension is enabled at device
     * creation.  Enabling extensions is the *application's* call, and
     * almost no game asks for this one , it is recent and mostly used by
     * emulators and D3D12 translation layers.  So until now the tier
     * priority mechanism resolved to NULL and did nothing on virtually
     * every real title: no error, no warning, just no effect.
     *
     * A layer is allowed to add device extensions, so add it.  Ryujinx
     * does the same thing for VK_EXT_external_memory_host with the
     * "desirable INTERSECT supported" pattern, which is what this is.
     *
     * Three things have to be true together or this is inert again:
     *   1. the physical device must actually support it,
     *   2. VK_EXT_memory_priority must be enabled too , pageable depends
     *      on it, and a device create naming only pageable is invalid,
     *   3. VkPhysicalDevicePageableDeviceLocalMemoryFeaturesEXT must be
     *      chained with pageableDeviceLocalMemory = VK_TRUE.  Naming the
     *      extension without enabling the feature is exactly the silent
     *      no-op this comment exists to stop repeating.
     *
     * Opt-out: GREENBOOST_VK_PAGEABLE=0
     */
    int want_pageable = 1;
    {
        const char *e = getenv("GREENBOOST_VK_PAGEABLE");
        if (e && strcmp(e, "0") == 0) want_pageable = 0;
    }

    /* Room for the app's own list plus the two we may add. */
    const char *ext_mod[256];
    VkPhysicalDevicePageableDeviceLocalMemoryFeaturesEXT pageable_feat;
    VkPhysicalDeviceMemoryPriorityFeaturesEXT            mempri_feat;
    int injected_pageable = 0;

    if (want_pageable && pCreateInfo->enabledExtensionCount + 2u <=
                         (uint32_t)(sizeof(ext_mod) / sizeof(ext_mod[0]))) {
        int app_has_pageable = 0, app_has_prio = 0;
        for (uint32_t i = 0; i < pCreateInfo->enabledExtensionCount; i++) {
            const char *e = pCreateInfo->ppEnabledExtensionNames[i];
            if (!e) continue;
            if (strcmp(e, VK_EXT_PAGEABLE_DEVICE_LOCAL_MEMORY_EXTENSION_NAME) == 0)
                app_has_pageable = 1;
            if (strcmp(e, VK_EXT_MEMORY_PRIORITY_EXTENSION_NAME) == 0)
                app_has_prio = 1;
        }

        if (!app_has_pageable) {
            /* Ask the driver what it supports.  Never assume , an
             * unsupported extension name makes vkCreateDevice fail
             * outright, which would break the game we are trying to help. */
            PFN_vkEnumerateDeviceExtensionProperties fn_enum = g_next_enum_dev_ext;
            int dev_has_pageable = 0, dev_has_prio = 0;
            if (fn_enum) {
                uint32_t n = 0;
                if (fn_enum(physDev, NULL, &n, NULL) == VK_SUCCESS && n) {
                    VkExtensionProperties *props =
                        (VkExtensionProperties *)calloc(n, sizeof(*props));
                    if (props && fn_enum(physDev, NULL, &n, props) == VK_SUCCESS) {
                        for (uint32_t i = 0; i < n; i++) {
                            if (strcmp(props[i].extensionName,
                                VK_EXT_PAGEABLE_DEVICE_LOCAL_MEMORY_EXTENSION_NAME) == 0)
                                dev_has_pageable = 1;
                            if (strcmp(props[i].extensionName,
                                VK_EXT_MEMORY_PRIORITY_EXTENSION_NAME) == 0)
                                dev_has_prio = 1;
                        }
                    }
                    free(props);
                }
            }

            if (dev_has_pageable && (dev_has_prio || app_has_prio)) {
                uint32_t n = 0;
                for (; n < pCreateInfo->enabledExtensionCount; n++)
                    ext_mod[n] = pCreateInfo->ppEnabledExtensionNames[n];
                if (!app_has_prio)
                    ext_mod[n++] = VK_EXT_MEMORY_PRIORITY_EXTENSION_NAME;
                ext_mod[n++] = VK_EXT_PAGEABLE_DEVICE_LOCAL_MEMORY_EXTENSION_NAME;

                /* VUID-VkDeviceCreateInfo-pageableDeviceLocalMemory-06839:
                 * pageableDeviceLocalMemory = TRUE requires memoryPriority =
                 * TRUE as well.  Naming both extensions but chaining only the
                 * pageable feature makes the driver reject the whole device
                 * create, which is how this was caught. */
                mempri_feat.sType =
                    VK_STRUCTURE_TYPE_PHYSICAL_DEVICE_MEMORY_PRIORITY_FEATURES_EXT;
                mempri_feat.pNext = (void *)dci_mod.pNext;
                mempri_feat.memoryPriority = VK_TRUE;

                pageable_feat.sType =
                    VK_STRUCTURE_TYPE_PHYSICAL_DEVICE_PAGEABLE_DEVICE_LOCAL_MEMORY_FEATURES_EXT;
                pageable_feat.pNext = &mempri_feat;
                pageable_feat.pageableDeviceLocalMemory = VK_TRUE;

                dci_mod.pNext                   = &pageable_feat;
                dci_mod.ppEnabledExtensionNames = ext_mod;
                dci_mod.enabledExtensionCount   = n;
                injected_pageable = 1;
                gbvk_log("CreateDevice: enabling %s%s ourselves , the app did "
                         "not ask for it, and tier memory priority is a no-op "
                         "without it",
                         VK_EXT_PAGEABLE_DEVICE_LOCAL_MEMORY_EXTENSION_NAME,
                         app_has_prio ? "" : " + " VK_EXT_MEMORY_PRIORITY_EXTENSION_NAME);
            } else if (!fn_enum) {
                /* Distinguish these two.  Reporting "unsupported" when the
                 * query never ran sends the reader to the driver, which is
                 * the wrong place to look. */
                gbvk_log("CreateDevice: could not query device extensions, "
                         "tier memory priority left inactive");
            } else if (!dev_has_pageable) {
                gbvk_log("CreateDevice: %s not supported by this device, "
                         "tier memory priority will be inactive",
                         VK_EXT_PAGEABLE_DEVICE_LOCAL_MEMORY_EXTENSION_NAME);
            } else {
                gbvk_log("CreateDevice: %s present but %s missing, cannot "
                         "enable pageable memory",
                         VK_EXT_PAGEABLE_DEVICE_LOCAL_MEMORY_EXTENSION_NAME,
                         VK_EXT_MEMORY_PRIORITY_EXTENSION_NAME);
            }
        }
    }

    /* Any injection means dci_mod must be the one we submit, even when
     * queue-priority injection was skipped. */
    int use_mod = want_priority || injected_pageable;

    VkResult res = next_cd(physDev,
                           use_mod ? &dci_mod : pCreateInfo,
                           pAllocator, pDevice);

    /* Back our additions out one at a time, cause-first.  The two are
     * independent and a blanket "drop everything we added" retry throws away
     * the working one: VK_ERROR_NOT_PERMITTED_KHR means the HIGH queue
     * priority was refused (no CAP_SYS_NICE), and says nothing at all about
     * the extensions, so dropping the extensions for it would silently lose
     * tier memory priority on every machine that simply lacks the capability.
     */
    if (res == VK_ERROR_NOT_PERMITTED_KHR && want_priority) {
        dci_mod.pQueueCreateInfos = pCreateInfo->pQueueCreateInfos;
        want_priority = 0;
        use_mod = injected_pageable;
        res = next_cd(physDev,
                      use_mod ? &dci_mod : pCreateInfo,
                      pAllocator, pDevice);
        if (res == VK_SUCCESS)
            gbvk_log("CreateDevice: HIGH queue priority rejected (no "
                     "CAP_SYS_NICE?), running at default priority");
    }

    /* Anything still failing while we are adding extensions is attributable
     * to them; drop them and let the app have its device. */
    if (res != VK_SUCCESS && injected_pageable) {
        gbvk_log("CreateDevice: rejected with our extensions (VkResult %d), "
                 "retrying without them", (int)res);
        dci_mod.pNext                   = pCreateInfo->pNext;
        dci_mod.ppEnabledExtensionNames = pCreateInfo->ppEnabledExtensionNames;
        dci_mod.enabledExtensionCount   = pCreateInfo->enabledExtensionCount;
        injected_pageable = 0;
        use_mod = want_priority;
        res = next_cd(physDev,
                      use_mod ? &dci_mod : pCreateInfo,
                      pAllocator, pDevice);
    }

    if (res == VK_SUCCESS && want_priority)
        gbvk_log("CreateDevice: HIGH global queue priority granted on %d queue family(s)",
                 n_q);

    if (res != VK_SUCCESS) return res;

    /*
     * Resolve all function pointers and cache memory properties BEFORE the mutex.
     * next_gdpa() and get_props() are external calls , must not run under g_mutex.
     */
    PFN_vkAllocateMemory  fn_alloc   = (PFN_vkAllocateMemory)
        next_gdpa(*pDevice, "vkAllocateMemory");
    PFN_vkFreeMemory      fn_free    = (PFN_vkFreeMemory)
        next_gdpa(*pDevice, "vkFreeMemory");
    PFN_vkDestroyDevice   fn_destroy = (PFN_vkDestroyDevice)
        next_gdpa(*pDevice, "vkDestroyDevice");
    PFN_vkGetMemoryFdPropertiesKHR fn_fd_props = (PFN_vkGetMemoryFdPropertiesKHR)
        next_gdpa(*pDevice, "vkGetMemoryFdPropertiesKHR");
    /* PR-GGGG: detect VK_NV_low_latency2 + VK_EXT_pageable_device_local_memory
     * in the device's enabled-extension list and resolve the runtime priority
     * setter when available.  We do *not* try to inject Reflex frame markers
     * (those need cooperation from the game's simulation/render-submit
     * timing) , we just log what we saw and use pageable_device_local_memory
     * to dynamically lower the priority of T2/T3 spills if the game opted in. */
    int has_lowlat2 = 0, has_pageable = 0;
    for (uint32_t i = 0; i < pCreateInfo->enabledExtensionCount; i++) {
        const char *e = pCreateInfo->ppEnabledExtensionNames[i];
        if (!e) continue;
        if (strcmp(e, "VK_NV_low_latency2") == 0)                       has_lowlat2  = 1;
        if (strcmp(e, "VK_EXT_pageable_device_local_memory") == 0)      has_pageable = 1;
    }
    /* We may have added it above; the app's list would not show that. */
    if (injected_pageable) has_pageable = 1;
    PFN_vkSetDeviceMemoryPriorityEXT fn_setprio = NULL;
    if (has_pageable) {
        fn_setprio = (PFN_vkSetDeviceMemoryPriorityEXT)
            next_gdpa(*pDevice, "vkSetDeviceMemoryPriorityEXT");
    }

    /* PR-GGGG: pipeline cache + shader-compile timing chain. */
    PFN_vkCreatePipelineCache fn_cpc = (PFN_vkCreatePipelineCache)
        next_gdpa(*pDevice, "vkCreatePipelineCache");
    PFN_vkDestroyPipelineCache fn_dpc = (PFN_vkDestroyPipelineCache)
        next_gdpa(*pDevice, "vkDestroyPipelineCache");
    PFN_vkGetPipelineCacheData fn_gpcd = (PFN_vkGetPipelineCacheData)
        next_gdpa(*pDevice, "vkGetPipelineCacheData");
    PFN_vkMergePipelineCaches fn_mpc = (PFN_vkMergePipelineCaches)
        next_gdpa(*pDevice, "vkMergePipelineCaches");
    PFN_vkCreateGraphicsPipelines fn_cgp = (PFN_vkCreateGraphicsPipelines)
        next_gdpa(*pDevice, "vkCreateGraphicsPipelines");
    PFN_vkCreateComputePipelines fn_ccp = (PFN_vkCreateComputePipelines)
        next_gdpa(*pDevice, "vkCreateComputePipelines");
    PFN_vkQueuePresentKHR fn_qpkhr = (PFN_vkQueuePresentKHR)
        next_gdpa(*pDevice, "vkQueuePresentKHR");
    PFN_vkBindImageMemory fn_bim = (PFN_vkBindImageMemory)
        next_gdpa(*pDevice, "vkBindImageMemory");
    PFN_vkBindImageMemory2 fn_bim2 = (PFN_vkBindImageMemory2)
        next_gdpa(*pDevice, "vkBindImageMemory2");
    if (!fn_bim2) fn_bim2 = (PFN_vkBindImageMemory2)
        next_gdpa(*pDevice, "vkBindImageMemory2KHR");
    /* PR-GGGG: NIS pipeline plumbing , resolve at CreateDevice; lazily
     * used by the first vkCreateSwapchainKHR call when NIS is enabled. */
    PFN_vkCreateSwapchainKHR         fn_csk   = (PFN_vkCreateSwapchainKHR)next_gdpa(*pDevice, "vkCreateSwapchainKHR");
    PFN_vkDestroySwapchainKHR        fn_dsk   = (PFN_vkDestroySwapchainKHR)next_gdpa(*pDevice, "vkDestroySwapchainKHR");
    PFN_vkGetSwapchainImagesKHR      fn_gsi   = (PFN_vkGetSwapchainImagesKHR)next_gdpa(*pDevice, "vkGetSwapchainImagesKHR");
    PFN_vkCreateShaderModule         fn_csm   = (PFN_vkCreateShaderModule)next_gdpa(*pDevice, "vkCreateShaderModule");
    PFN_vkDestroyShaderModule        fn_dsm   = (PFN_vkDestroyShaderModule)next_gdpa(*pDevice, "vkDestroyShaderModule");
    PFN_vkCreateDescriptorSetLayout  fn_cdsl  = (PFN_vkCreateDescriptorSetLayout)next_gdpa(*pDevice, "vkCreateDescriptorSetLayout");
    PFN_vkDestroyDescriptorSetLayout fn_ddsl  = (PFN_vkDestroyDescriptorSetLayout)next_gdpa(*pDevice, "vkDestroyDescriptorSetLayout");
    PFN_vkCreatePipelineLayout       fn_cpl   = (PFN_vkCreatePipelineLayout)next_gdpa(*pDevice, "vkCreatePipelineLayout");
    PFN_vkDestroyPipelineLayout      fn_dpl   = (PFN_vkDestroyPipelineLayout)next_gdpa(*pDevice, "vkDestroyPipelineLayout");
    PFN_vkDestroyPipeline            fn_dpipe = (PFN_vkDestroyPipeline)next_gdpa(*pDevice, "vkDestroyPipeline");
    PFN_vkCreateSampler              fn_cs    = (PFN_vkCreateSampler)next_gdpa(*pDevice, "vkCreateSampler");
    PFN_vkDestroySampler             fn_ds    = (PFN_vkDestroySampler)next_gdpa(*pDevice, "vkDestroySampler");
    /* PR-GGGG: NIS dispatch , full set of fn pointers for resource alloc,
     * command-buffer recording, and queue submit.  Resolved unconditionally;
     * only used when GREENBOOST_NIS_DISPATCH=1 turns the actual dispatch on. */
    PFN_vkCreateImage                fn_ci    = (PFN_vkCreateImage)next_gdpa(*pDevice, "vkCreateImage");
    PFN_vkDestroyImage               fn_di    = (PFN_vkDestroyImage)next_gdpa(*pDevice, "vkDestroyImage");
    PFN_vkGetImageMemoryRequirements fn_gimr  = (PFN_vkGetImageMemoryRequirements)next_gdpa(*pDevice, "vkGetImageMemoryRequirements");
    PFN_vkBindImageMemory            fn_bim_call = (PFN_vkBindImageMemory)next_gdpa(*pDevice, "vkBindImageMemory");
    PFN_vkCreateImageView            fn_civ   = (PFN_vkCreateImageView)next_gdpa(*pDevice, "vkCreateImageView");
    PFN_vkDestroyImageView           fn_div   = (PFN_vkDestroyImageView)next_gdpa(*pDevice, "vkDestroyImageView");
    PFN_vkCreateDescriptorPool       fn_cdp   = (PFN_vkCreateDescriptorPool)next_gdpa(*pDevice, "vkCreateDescriptorPool");
    PFN_vkDestroyDescriptorPool      fn_ddp   = (PFN_vkDestroyDescriptorPool)next_gdpa(*pDevice, "vkDestroyDescriptorPool");
    PFN_vkAllocateDescriptorSets     fn_ads   = (PFN_vkAllocateDescriptorSets)next_gdpa(*pDevice, "vkAllocateDescriptorSets");
    PFN_vkUpdateDescriptorSets       fn_uds   = (PFN_vkUpdateDescriptorSets)next_gdpa(*pDevice, "vkUpdateDescriptorSets");
    PFN_vkCreateBuffer               fn_cb    = (PFN_vkCreateBuffer)next_gdpa(*pDevice, "vkCreateBuffer");
    PFN_vkDestroyBuffer              fn_db    = (PFN_vkDestroyBuffer)next_gdpa(*pDevice, "vkDestroyBuffer");
    PFN_vkGetBufferMemoryRequirements fn_gbmr = (PFN_vkGetBufferMemoryRequirements)next_gdpa(*pDevice, "vkGetBufferMemoryRequirements");
    PFN_vkBindBufferMemory           fn_bbm   = (PFN_vkBindBufferMemory)next_gdpa(*pDevice, "vkBindBufferMemory");
    PFN_vkMapMemory                  fn_mm    = (PFN_vkMapMemory)next_gdpa(*pDevice, "vkMapMemory");
    PFN_vkUnmapMemory                fn_um    = (PFN_vkUnmapMemory)next_gdpa(*pDevice, "vkUnmapMemory");
    PFN_vkCreateCommandPool          fn_cmdpool = (PFN_vkCreateCommandPool)next_gdpa(*pDevice, "vkCreateCommandPool");
    PFN_vkDestroyCommandPool         fn_dcp   = (PFN_vkDestroyCommandPool)next_gdpa(*pDevice, "vkDestroyCommandPool");
    PFN_vkAllocateCommandBuffers     fn_acb   = (PFN_vkAllocateCommandBuffers)next_gdpa(*pDevice, "vkAllocateCommandBuffers");
    PFN_vkBeginCommandBuffer         fn_bcb   = (PFN_vkBeginCommandBuffer)next_gdpa(*pDevice, "vkBeginCommandBuffer");
    PFN_vkEndCommandBuffer           fn_ecb   = (PFN_vkEndCommandBuffer)next_gdpa(*pDevice, "vkEndCommandBuffer");
    PFN_vkCmdPipelineBarrier         fn_cpb   = (PFN_vkCmdPipelineBarrier)next_gdpa(*pDevice, "vkCmdPipelineBarrier");
    PFN_vkCmdCopyImage               fn_cci   = (PFN_vkCmdCopyImage)next_gdpa(*pDevice, "vkCmdCopyImage");
    PFN_vkCmdBindPipeline            fn_cbp   = (PFN_vkCmdBindPipeline)next_gdpa(*pDevice, "vkCmdBindPipeline");
    PFN_vkCmdBindDescriptorSets      fn_cbds  = (PFN_vkCmdBindDescriptorSets)next_gdpa(*pDevice, "vkCmdBindDescriptorSets");
    PFN_vkCmdDispatch                fn_cd    = (PFN_vkCmdDispatch)next_gdpa(*pDevice, "vkCmdDispatch");
    PFN_vkCmdPushConstants           fn_cpush = (PFN_vkCmdPushConstants)next_gdpa(*pDevice, "vkCmdPushConstants");
    PFN_vkCreateSemaphore            fn_csem  = (PFN_vkCreateSemaphore)next_gdpa(*pDevice, "vkCreateSemaphore");
    PFN_vkDestroySemaphore           fn_dsem  = (PFN_vkDestroySemaphore)next_gdpa(*pDevice, "vkDestroySemaphore");
    PFN_vkQueueSubmit                fn_qs    = (PFN_vkQueueSubmit)next_gdpa(*pDevice, "vkQueueSubmit");
    /* Pick the best queue family for NIS compute , scan the families the app
     * created, prefer one with COMPUTE_BIT but without GRAPHICS_BIT (async
     * compute queue).  Falls back to any family with COMPUTE_BIT, then to the
     * first requested family. */
    uint32_t nis_qfi = (pCreateInfo->queueCreateInfoCount > 0)
        ? pCreateInfo->pQueueCreateInfos[0].queueFamilyIndex : 0;
    {
        PFN_vkGetPhysicalDeviceQueueFamilyProperties fn_gqfp =
            (PFN_vkGetPhysicalDeviceQueueFamilyProperties)
            next_gipa(VK_NULL_HANDLE, "vkGetPhysicalDeviceQueueFamilyProperties");
        if (fn_gqfp && pCreateInfo->queueCreateInfoCount > 0) {
            uint32_t nfam = 0;
            fn_gqfp(physDev, &nfam, NULL);
            if (nfam > 0 && nfam <= 64) {
                VkQueueFamilyProperties fam_props[64];
                fn_gqfp(physDev, &nfam, fam_props);
                int found_async = 0, found_compute = 0;
                for (uint32_t qi = 0; qi < pCreateInfo->queueCreateInfoCount; qi++) {
                    uint32_t fi = pCreateInfo->pQueueCreateInfos[qi].queueFamilyIndex;
                    if (fi >= nfam) continue;
                    VkQueueFlags flags = fam_props[fi].queueFlags;
                    if (!(flags & VK_QUEUE_COMPUTE_BIT)) continue;
                    if (!(flags & VK_QUEUE_GRAPHICS_BIT) && !found_async) {
                        nis_qfi = fi; found_async = 1; /* async compute: best */
                    } else if (!found_async && !found_compute) {
                        nis_qfi = fi; found_compute = 1; /* graphics+compute: ok */
                    }
                }
                gbvk_log("CreateDevice: NIS queue family = %u%s", nis_qfi,
                         found_async ? " (async compute)" :
                         found_compute ? " (graphics+compute)" : " (fallback)");
            }
        }
    }

    /* A7: resolve Reflex entry points when the extension is present and the
     * user opted in via GREENBOOST_REFLEX=1. */
    PFN_vkSetLatencySleepModeNV fn_slsm  = NULL;
    PFN_vkLatencySleepNV        fn_ls    = NULL;
    PFN_vkSetLatencyMarkerNV    fn_slm   = NULL;
    PFN_vkGetLatencyTimingsNV   fn_glt   = NULL;
    PFN_vkAcquireNextImageKHR   fn_ani   = NULL;
    int want_reflex = 0;
    if (has_lowlat2) {
        const char *re = getenv("GREENBOOST_REFLEX");
        if (re && re[0] == '1') {
            fn_slsm = (PFN_vkSetLatencySleepModeNV)  next_gdpa(*pDevice, "vkSetLatencySleepModeNV");
            fn_ls   = (PFN_vkLatencySleepNV)          next_gdpa(*pDevice, "vkLatencySleepNV");
            fn_slm  = (PFN_vkSetLatencyMarkerNV)      next_gdpa(*pDevice, "vkSetLatencyMarkerNV");
            fn_glt  = (PFN_vkGetLatencyTimingsNV)     next_gdpa(*pDevice, "vkGetLatencyTimingsNV");
            fn_ani  = (PFN_vkAcquireNextImageKHR)     next_gdpa(*pDevice, "vkAcquireNextImageKHR");
            want_reflex = (fn_slsm && fn_ls && fn_slm) ? 1 : 0;
        }
    }

    gbvk_log("CreateDevice: extensions , VK_NV_low_latency2=%d "
             "VK_EXT_pageable_device_local_memory=%d reflex=%d",
             has_lowlat2, has_pageable, want_reflex);

    /* Snapshot mem_props using already-resolved instance-level function. */
    VkPhysicalDeviceMemoryProperties mem_props = {};
    pthread_mutex_lock(&g_mutex);
    PFN_vkGetPhysicalDeviceMemoryProperties get_props = NULL;
    for (int i = 0; i < GBVK_MAX_INSTANCES; i++)
        if (g_inst[i].next_get_mem_props) { get_props = g_inst[i].next_get_mem_props; break; }
    pthread_mutex_unlock(&g_mutex);

    if (get_props) get_props(physDev, &mem_props);  /* external call, outside mutex */

    /* PR-GGGG: log GPU + driver identification at first CreateDevice.
     * Done lazily , many apps create instance probe-style without a device. */
    {
        PFN_vkGetPhysicalDeviceProperties get_dev_props =
            (PFN_vkGetPhysicalDeviceProperties)next_gipa(VK_NULL_HANDLE,
                                                         "vkGetPhysicalDeviceProperties");
        /* next_gipa is per-instance; resolve via inst table if NULL. */
        if (!get_dev_props) {
            pthread_mutex_lock(&g_mutex);
            for (int i = 0; i < GBVK_MAX_INSTANCES; i++) {
                if (g_inst[i].next_gipa) {
                    get_dev_props = (PFN_vkGetPhysicalDeviceProperties)
                        g_inst[i].next_gipa(VK_NULL_HANDLE,
                                            "vkGetPhysicalDeviceProperties");
                    if (get_dev_props) break;
                }
            }
            pthread_mutex_unlock(&g_mutex);
        }
        if (get_dev_props) {
            VkPhysicalDeviceProperties props = {0};
            get_dev_props(physDev, &props);
            /* NVIDIA driver packs version as major(22)/minor(14)/sub(6)/patch(4). */
            unsigned drv_major = (props.driverVersion >> 22) & 0x3FF;
            unsigned drv_minor = (props.driverVersion >> 14) & 0xFF;
            unsigned drv_sub   = (props.driverVersion >>  6) & 0xFF;
            const char *vendor = "unknown";
            switch (props.vendorID) {
                case 0x10DE: vendor = "NVIDIA";  break;
                case 0x1002: vendor = "AMD";     break;
                case 0x8086: vendor = "Intel";   break;
                case 0x106B: vendor = "Apple";   break;
            }
            gbvk_log("CreateDevice: %s '%s' driver %u.%u.%u (api %u.%u.%u)",
                     vendor, props.deviceName,
                     drv_major, drv_minor, drv_sub,
                     VK_VERSION_MAJOR(props.apiVersion),
                     VK_VERSION_MINOR(props.apiVersion),
                     VK_VERSION_PATCH(props.apiVersion));
            /* PR-GGGG: capture identity used to gate pipeline-cache reload. */
            pthread_mutex_lock(&g_mutex);
            GbDevData *dd2 = dev_find(*pDevice);
            if (dd2) {
                dd2->vendor_id = props.vendorID;
                dd2->device_id = props.deviceID;
                memcpy(dd2->cache_uuid, props.pipelineCacheUUID, 16);
                dd2->identity_valid = 1;
            }
            pthread_mutex_unlock(&g_mutex);
        }
    }

    pthread_mutex_lock(&g_mutex);
    GbDevData *d = dev_alloc(*pDevice);
    if (d) {
        d->next_gdpa                       = next_gdpa;
        d->next_alloc_mem                  = fn_alloc;
        d->next_free_mem                   = fn_free;
        d->next_destroy_device             = fn_destroy;
        d->next_get_mem_fd_props           = fn_fd_props;
        d->mem_props                       = mem_props;
        d->next_create_pipeline_cache      = fn_cpc;
        d->next_destroy_pipeline_cache     = fn_dpc;
        d->next_get_pipeline_cache_data    = fn_gpcd;
        d->next_merge_pipeline_caches      = fn_mpc;
        d->next_create_graphics_pipelines  = fn_cgp;
        d->next_create_compute_pipelines   = fn_ccp;
        d->next_queue_present_khr          = fn_qpkhr;
        d->next_set_device_memory_priority = fn_setprio;
        d->next_bind_image_memory          = fn_bim;
        d->next_bind_image_memory2         = fn_bim2;
        d->next_create_swapchain           = fn_csk;
        d->next_destroy_swapchain          = fn_dsk;
        d->next_get_swapchain_images       = fn_gsi;
        d->next_create_shader_module       = fn_csm;
        d->next_destroy_shader_module      = fn_dsm;
        d->next_create_dsl                 = fn_cdsl;
        d->next_destroy_dsl                = fn_ddsl;
        d->next_create_player              = fn_cpl;
        d->next_destroy_player             = fn_dpl;
        d->next_destroy_pipeline           = fn_dpipe;
        d->next_create_sampler             = fn_cs;
        d->next_destroy_sampler            = fn_ds;
        d->next_create_image               = fn_ci;
        d->next_destroy_image              = fn_di;
        d->next_get_image_mem_req          = fn_gimr;
        d->next_bind_image_memory_call     = fn_bim_call;
        d->next_create_image_view          = fn_civ;
        d->next_destroy_image_view         = fn_div;
        d->next_create_desc_pool           = fn_cdp;
        d->next_destroy_desc_pool          = fn_ddp;
        d->next_alloc_desc_sets            = fn_ads;
        d->next_update_desc_sets           = fn_uds;
        d->next_create_buffer              = fn_cb;
        d->next_destroy_buffer             = fn_db;
        d->next_get_buf_mem_req            = fn_gbmr;
        d->next_bind_buf_memory            = fn_bbm;
        d->next_map_memory                 = fn_mm;
        d->next_unmap_memory               = fn_um;
        d->next_create_cmd_pool            = fn_cmdpool;
        d->next_destroy_cmd_pool           = fn_dcp;
        d->next_alloc_cmd_buffers          = fn_acb;
        d->next_begin_cmd_buffer           = fn_bcb;
        d->next_end_cmd_buffer             = fn_ecb;
        d->next_cmd_pipeline_barrier       = fn_cpb;
        d->next_cmd_copy_image             = fn_cci;
        d->next_cmd_bind_pipeline          = fn_cbp;
        d->next_cmd_bind_desc_sets         = fn_cbds;
        d->next_cmd_dispatch               = fn_cd;
        d->next_cmd_push_constants         = fn_cpush;
        d->next_create_semaphore           = fn_csem;
        d->next_destroy_semaphore          = fn_dsem;
        d->next_queue_submit               = fn_qs;
        d->nis_queue_family                = nis_qfi;
        /* A7: Reflex latency markers (VK_NV_low_latency2). */
        if (want_reflex) {
            d->next_set_latency_sleep_mode = fn_slsm;
            d->next_latency_sleep          = fn_ls;
            d->next_set_latency_marker     = fn_slm;
            d->next_get_latency_timings    = fn_glt;
            d->next_acquire_image          = fn_ani;
        }
    }
    pthread_mutex_unlock(&g_mutex);
    return VK_SUCCESS;
}

/* (g_gbvk_pipe_*, g_gbvk_present_* are forward-declared near the top of
 * the file.  They zero-initialise via the tentative-definition rule.) */

static VKAPI_ATTR void VKAPI_CALL
gbvk_DestroyDevice(VkDevice device, const VkAllocationCallbacks *pAllocator)
{
    /* PR-GGGG: drop any tracked pipeline caches that belong to this device
     * BEFORE we tear down the dispatch table.  Apps are supposed to destroy
     * caches before their owning device, but well-behaved teardown order is
     * not guaranteed in crashing/forced-exit paths.  The snapshot worker
     * takes a memcpy of the table once per tick , by clearing entries here
     * we ensure its next pass sees an empty slot rather than a stale fn_gpcd
     * pointing into freed driver memory. */
    gbvk_pipe_drop_device(device);

    /* PR-GGGG: tear down NIS device-level resources BEFORE dev_free ,
     * snapshot the destroyers + handles under lock, then call them outside.
     * Without this, the next session would leak the shader module / DSL
     * / pipeline on every device-level relaunch. */
    VkPipeline           nis_pipe    = VK_NULL_HANDLE;
    VkPipelineLayout     nis_player  = VK_NULL_HANDLE;
    VkDescriptorSetLayout nis_dsl    = VK_NULL_HANDLE;
    VkShaderModule       nis_mod     = VK_NULL_HANDLE;
    VkSampler            nis_samp    = VK_NULL_HANDLE;
    PFN_vkDestroyPipeline            fn_dpipe = NULL;
    PFN_vkDestroyPipelineLayout      fn_dpl   = NULL;
    PFN_vkDestroyDescriptorSetLayout fn_ddsl  = NULL;
    PFN_vkDestroyShaderModule        fn_dsm   = NULL;
    PFN_vkDestroySampler             fn_ds    = NULL;

    pthread_mutex_lock(&g_mutex);
    GbDevData *d = dev_find(device);
    PFN_vkDestroyDevice fn = d ? d->next_destroy_device : NULL;
    if (d && d->nis_initialised) {
        nis_pipe   = d->nis_pipeline;
        nis_player = d->nis_player;
        nis_dsl    = d->nis_dsl;
        nis_mod    = d->nis_module;
        nis_samp   = d->nis_sampler;
        fn_dpipe = d->next_destroy_pipeline;
        fn_dpl   = d->next_destroy_player;
        fn_ddsl  = d->next_destroy_dsl;
        fn_dsm   = d->next_destroy_shader_module;
        fn_ds    = d->next_destroy_sampler;
    }
    dev_free(device);
    pthread_mutex_unlock(&g_mutex);

    if (fn_dpipe && nis_pipe)   fn_dpipe(device, nis_pipe,   NULL);
    if (fn_dpl   && nis_player) fn_dpl  (device, nis_player, NULL);
    if (fn_ddsl  && nis_dsl)    fn_ddsl (device, nis_dsl,    NULL);
    if (fn_dsm   && nis_mod)    fn_dsm  (device, nis_mod,    NULL);
    if (fn_ds    && nis_samp)   fn_ds   (device, nis_samp,   NULL);

    /* PR-GGGG: emit shader-compile summary before tearing down. */
    uint64_t gpc  = atomic_load_explicit(&g_gbvk_pipe_count_g,  memory_order_relaxed);
    uint64_t cpc  = atomic_load_explicit(&g_gbvk_pipe_count_c,  memory_order_relaxed);
    uint64_t tot  = atomic_load_explicit(&g_gbvk_pipe_total_ns, memory_order_relaxed);
    uint64_t slow = atomic_load_explicit(&g_gbvk_pipe_slow_ns,  memory_order_relaxed);
    if (gpc + cpc > 0) {
        gbvk_emit(1,
            "shader compile summary: %llu graphics / %llu compute PSOs, "
            "%.2f s total wall, slowest single batch %.2f ms",
            (unsigned long long)gpc, (unsigned long long)cpc,
            (double)tot  / 1.0e9,
            (double)slow / 1.0e6);
    }

    /* PR-GGGG: frame-pacing summary. */
    uint64_t pcount   = atomic_load_explicit(&g_gbvk_present_count,    memory_order_relaxed);
    uint64_t ptotal   = atomic_load_explicit(&g_gbvk_present_total_ns, memory_order_relaxed);
    uint64_t pworst   = atomic_load_explicit(&g_gbvk_present_worst_ns, memory_order_relaxed);
    uint64_t phitches = atomic_load_explicit(&g_gbvk_present_hitches,  memory_order_relaxed);
    if (pcount > 1) {
        double mean_ms  = (double)ptotal / (double)(pcount - 1) / 1.0e6;
        double avg_fps  = (mean_ms > 0.0) ? (1000.0 / mean_ms) : 0.0;
        gbvk_emit(1,
            "frame pacing: %llu presents, %.2f avg fps (%.2f ms mean), "
            "worst gap %.1f ms, hitches (>33ms) %llu",
            (unsigned long long)pcount, avg_fps, mean_ms,
            (double)pworst / 1.0e6,
            (unsigned long long)phitches);
    }

    if (fn) fn(device, pAllocator);
}

/* ── PR-GGGG: persistent pipeline cache + shader-compile telemetry ────── */
/*
 * What this layer does for shader caches: when the application creates a
 * VkPipelineCache, we silently inject the contents of a per-AppID blob
 * stored at $HOME/.local/share/greenboost/proton-cache/vk-pipeline/<id>.bin
 * as `pInitialData`.  On destroy (or device tear-down), we snapshot the
 * cache and write it back atomically.  This is a *layer-side* cache that
 * is independent of and additive to DXVK_STATE_CACHE / VKD3D_SHADER_CACHE
 * , it captures driver-level SPIR-V→SASS compilation results, which the
 * NVIDIA driver wouldn't otherwise persist beyond the implicit
 * __GL_SHADER_DISK_CACHE.
 *
 * Telemetry: every vkCreate{Graphics,Compute}Pipelines call is timed via
 * CLOCK_MONOTONIC and aggregated into g_gbvk_pipe_*; emitted on device
 * destroy as a single summary line into the greenboost layer log.
 *
 * Opt-out: GREENBOOST_VK_PIPELINE_CACHE=0
 */

/* (g_gbvk_pipe_* atomics are forward-declared above gbvk_DestroyDevice.) */

/* PR-GGGG: tracked pipeline caches for periodic snapshot thread. */
#define GBVK_MAX_PIPE_CACHES 16

typedef struct {
    VkDevice                    device;
    VkPipelineCache             cache;
    PFN_vkGetPipelineCacheData  next_gpcd;
    uint64_t                    last_snapshot_ns;
    size_t                      last_snapshot_size;
} GbPipeCacheEntry;

static GbPipeCacheEntry  g_pipe_caches[GBVK_MAX_PIPE_CACHES];
static pthread_mutex_t   g_pipe_caches_mu = PTHREAD_MUTEX_INITIALIZER;
/* g_pipe_snap_thread, g_pipe_snap_thread_started, g_pipe_snap_stop are
 * forward-declared above gbvk_fini and defined here implicitly via the
 * tentative-definition rule (they default to zero/NULL). */

static uint64_t gbvk_mono_ns(void)
{
    struct timespec ts;
    if (clock_gettime(CLOCK_MONOTONIC, &ts) != 0) return 0;
    return (uint64_t)ts.tv_sec * 1000000000ULL + (uint64_t)ts.tv_nsec;
}

static int gbvk_pipe_cache_enabled(void)
{
    const char *v = getenv("GREENBOOST_VK_PIPELINE_CACHE");
    return !v || strcmp(v, "0") != 0;  /* default ON */
}

static void gbvk_pipe_cache_path(char *out, size_t n)
{
    const char *home = getenv("HOME");
    const char *appid = getenv("SteamGameId");
    if (!home) home = "/tmp";
    if (!appid || !*appid) appid = "default";
    snprintf(out, n,
             "%s/.local/share/greenboost/proton-cache/vk-pipeline/%s.bin",
             home, appid);
}

static void *gbvk_pipe_cache_load(size_t *out_size)
{
    *out_size = 0;
    if (!gbvk_pipe_cache_enabled()) return NULL;
    char path[1024];
    gbvk_pipe_cache_path(path, sizeof path);
    int fd = open(path, O_RDONLY | O_CLOEXEC);
    if (fd < 0) return NULL;
    struct stat st;
    if (fstat(fd, &st) != 0 || st.st_size <= 0 || st.st_size > (off_t)(512 << 20)) {
        close(fd); return NULL;
    }
    void *buf = malloc((size_t)st.st_size);
    if (!buf) { close(fd); return NULL; }
    ssize_t r = read(fd, buf, (size_t)st.st_size);
    close(fd);
    if (r != (ssize_t)st.st_size) { free(buf); return NULL; }
    *out_size = (size_t)st.st_size;
    return buf;
}

static void gbvk_pipe_cache_save(const void *data, size_t size)
{
    if (!gbvk_pipe_cache_enabled() || !data || !size) return;
    char path[1024];
    gbvk_pipe_cache_path(path, sizeof path);
    /* Ensure parent directory exists. */
    char parent[1024];
    snprintf(parent, sizeof parent, "%s", path);
    char *slash = strrchr(parent, '/');
    if (slash) { *slash = 0; gbvk_mkdirs(parent); }
    /* Atomic write via .tmp + rename. */
    char tmp[1100];
    snprintf(tmp, sizeof tmp, "%s.tmp", path);
    int fd = open(tmp, O_WRONLY | O_CREAT | O_TRUNC | O_CLOEXEC, 0644);
    if (fd < 0) return;
    ssize_t w = write(fd, data, size);
    int ok = (w == (ssize_t)size);
    close(fd);
    if (ok) {
        if (rename(tmp, path) != 0) unlink(tmp);
    } else {
        unlink(tmp);
    }
}

/* Periodic-snapshot tracker management.  Add/remove are O(N) over a tiny
 * fixed array , apps rarely create more than 1–2 VkPipelineCache instances. */
static void gbvk_pipe_track_add(VkDevice device, VkPipelineCache cache,
                                PFN_vkGetPipelineCacheData fn_gpcd)
{
    pthread_mutex_lock(&g_pipe_caches_mu);
    for (int i = 0; i < GBVK_MAX_PIPE_CACHES; i++) {
        if (g_pipe_caches[i].cache == VK_NULL_HANDLE) {
            g_pipe_caches[i] = (GbPipeCacheEntry){
                .device             = device,
                .cache              = cache,
                .next_gpcd          = fn_gpcd,
                .last_snapshot_ns   = 0,
                .last_snapshot_size = 0,
            };
            break;
        }
    }
    pthread_mutex_unlock(&g_pipe_caches_mu);
}

static void gbvk_pipe_track_remove(VkPipelineCache cache)
{
    pthread_mutex_lock(&g_pipe_caches_mu);
    for (int i = 0; i < GBVK_MAX_PIPE_CACHES; i++) {
        if (g_pipe_caches[i].cache == cache) {
            memset(&g_pipe_caches[i], 0, sizeof g_pipe_caches[i]);
            break;
        }
    }
    pthread_mutex_unlock(&g_pipe_caches_mu);
}

/* PR-GGGG: drop every tracked entry owned by `device` , called from
 * vkDestroyDevice to keep the snapshot worker from calling into a
 * freed dispatch table. */
static void gbvk_pipe_drop_device(VkDevice device)
{
    pthread_mutex_lock(&g_pipe_caches_mu);
    for (int i = 0; i < GBVK_MAX_PIPE_CACHES; i++) {
        if (g_pipe_caches[i].device == device) {
            memset(&g_pipe_caches[i], 0, sizeof g_pipe_caches[i]);
        }
    }
    pthread_mutex_unlock(&g_pipe_caches_mu);
}

static void *gbvk_pipe_snapshot_worker(void *unused)
{
    (void)unused;
    /* Interval default 60 s; clamp to [10, 600]. */
    int sec = 60;
    const char *env = getenv("GREENBOOST_VK_CACHE_SNAPSHOT_SEC");
    if (env) {
        int v = atoi(env);
        if (v >= 10 && v <= 600) sec = v;
    }

    while (!atomic_load_explicit(&g_pipe_snap_stop, memory_order_relaxed)) {
        /* Sleep in 1-second chunks so we can be stopped quickly on layer fini,
         * and so SIGUSR1 dump requests get serviced within ~1 s. */
        for (int s = 0; s < sec; s++) {
            if (atomic_load_explicit(&g_pipe_snap_stop, memory_order_relaxed))
                return NULL;
            int req = atomic_exchange_explicit(&g_gbvk_dump_requested, 0,
                                               memory_order_relaxed);
            if (req != 0) {
                gbvk_dump_stats();
                if (req == 2) {
                    /* USR2: force-write each tracked pipeline cache. */
                    GbPipeCacheEntry snap_now[GBVK_MAX_PIPE_CACHES];
                    pthread_mutex_lock(&g_pipe_caches_mu);
                    memcpy(snap_now, g_pipe_caches, sizeof snap_now);
                    pthread_mutex_unlock(&g_pipe_caches_mu);
                    for (int i = 0; i < GBVK_MAX_PIPE_CACHES; i++) {
                        if (!snap_now[i].cache || !snap_now[i].next_gpcd) continue;
                        size_t sz = 0;
                        if (snap_now[i].next_gpcd(snap_now[i].device,
                                                  snap_now[i].cache,
                                                  &sz, NULL) != VK_SUCCESS) continue;
                        if (sz == 0 || sz > (512U << 20)) continue;
                        void *buf = malloc(sz);
                        if (buf && snap_now[i].next_gpcd(snap_now[i].device,
                                                         snap_now[i].cache,
                                                         &sz, buf) == VK_SUCCESS) {
                            gbvk_pipe_cache_save(buf, sz);
                            gbvk_emit(1, "USR2 cache flush: %zu bytes", sz);
                        }
                        free(buf);
                    }

                    /* USR2: also reload NIS config (sharpness) from env and
                     * update the uniform buffer of every active NIS swapchain.
                     * Best-effort: no GPU sync , HOST_COHERENT writes on x86
                     * are seen by the GPU on the next dispatch. */
                    float new_sharpness = nis_read_sharpness();
                    pthread_mutex_lock(&g_nis_swap_mu);
                    GbNisSwapState snap_sw[GBVK_MAX_SWAPCHAINS];
                    memcpy(snap_sw, g_nis_swap, sizeof snap_sw);
                    pthread_mutex_unlock(&g_nis_swap_mu);

                    pthread_mutex_lock(&g_mutex);
                    for (int si = 0; si < GBVK_MAX_SWAPCHAINS; si++) {
                        if (!snap_sw[si].ready || !snap_sw[si].device) continue;
                        GbDevData *dv = dev_find(snap_sw[si].device);
                        if (!dv || !dv->next_map_memory || !dv->next_unmap_memory) continue;
                        void *mapped = NULL;
                        if (dv->next_map_memory(snap_sw[si].device,
                                                snap_sw[si].ub_memory,
                                                0, 256, 0, &mapped) == VK_SUCCESS) {
                            uint32_t iw = dv->nis_use_upscale
                                ? (uint32_t)((float)snap_sw[si].width  * dv->nis_scale + 0.5f)
                                : snap_sw[si].width;
                            uint32_t ih = dv->nis_use_upscale
                                ? (uint32_t)((float)snap_sw[si].height * dv->nis_scale + 0.5f)
                                : snap_sw[si].height;
                            if (iw < 16) iw = 16;
                            if (ih < 16) ih = 16;
                            nis_fill_sharpen_defaults(mapped,
                                                      snap_sw[si].width, snap_sw[si].height,
                                                      iw, ih, new_sharpness);
                            dv->next_unmap_memory(snap_sw[si].device, snap_sw[si].ub_memory);
                        }
                    }
                    pthread_mutex_unlock(&g_mutex);
                    gbvk_emit(1, "USR2 NIS config reloaded: sharpness=%.2f", new_sharpness);
                }
            }
            struct timespec ts = {1, 0};
            nanosleep(&ts, NULL);
        }

        /* Take a snapshot of the tracker under lock to avoid racing with
         * destroy.  Bound the local copy size so we never blow the stack. */
        GbPipeCacheEntry snap[GBVK_MAX_PIPE_CACHES];
        pthread_mutex_lock(&g_pipe_caches_mu);
        memcpy(snap, g_pipe_caches, sizeof snap);
        pthread_mutex_unlock(&g_pipe_caches_mu);

        for (int i = 0; i < GBVK_MAX_PIPE_CACHES; i++) {
            if (snap[i].cache == VK_NULL_HANDLE || !snap[i].next_gpcd) continue;
            size_t sz = 0;
            if (snap[i].next_gpcd(snap[i].device, snap[i].cache, &sz, NULL) != VK_SUCCESS)
                continue;
            if (sz == 0 || sz > (512U << 20)) continue;
            /* Skip if size hasn't grown since last snapshot , no new shaders. */
            if (sz == snap[i].last_snapshot_size) continue;
            void *buf = malloc(sz);
            if (!buf) continue;
            if (snap[i].next_gpcd(snap[i].device, snap[i].cache, &sz, buf) == VK_SUCCESS) {
                gbvk_pipe_cache_save(buf, sz);
                gbvk_emit(2, "periodic snapshot: %zu bytes (cache %p)",
                          sz, (void *)snap[i].cache);
                /* Update last_snapshot_size in the live entry. */
                pthread_mutex_lock(&g_pipe_caches_mu);
                for (int j = 0; j < GBVK_MAX_PIPE_CACHES; j++) {
                    if (g_pipe_caches[j].cache == snap[i].cache) {
                        g_pipe_caches[j].last_snapshot_size = sz;
                        break;
                    }
                }
                pthread_mutex_unlock(&g_pipe_caches_mu);
            }
            free(buf);
        }
    }
    return NULL;
}

/* Bridge to satisfy pthread_once's no-arg signature. */
static void gbvk_snap_init(void)
{
    if (pthread_create(&g_pipe_snap_thread, NULL,
                       gbvk_pipe_snapshot_worker, NULL) == 0) {
        g_pipe_snap_thread_started = 1;
    }
}

static void gbvk_pipe_snapshot_thread_start_once(void)
{
    static pthread_once_t once = PTHREAD_ONCE_INIT;
    pthread_once(&once, gbvk_snap_init);
}

static VKAPI_ATTR VkResult VKAPI_CALL
gbvk_CreatePipelineCache(VkDevice                          device,
                         const VkPipelineCacheCreateInfo  *pCreateInfo,
                         const VkAllocationCallbacks      *pAllocator,
                         VkPipelineCache                  *pPipelineCache)
{
    pthread_mutex_lock(&g_mutex);
    GbDevData *d = dev_find(device);
    PFN_vkCreatePipelineCache  fn       = d ? d->next_create_pipeline_cache    : NULL;
    PFN_vkGetPipelineCacheData fn_gpcd  = d ? d->next_get_pipeline_cache_data  : NULL;
    pthread_mutex_unlock(&g_mutex);
    if (!fn) return VK_ERROR_INITIALIZATION_FAILED;

    /* Fast path: the app already supplied seed data , don't second-guess it.
     * (DXVK/VKD3D-Proton pass cached blobs through their own caches.) */
    if (pCreateInfo->initialDataSize > 0) {
        VkResult r = fn(device, pCreateInfo, pAllocator, pPipelineCache);
        if (r == VK_SUCCESS && fn_gpcd) {
            gbvk_pipe_track_add(device, *pPipelineCache, fn_gpcd);
            gbvk_pipe_snapshot_thread_start_once();
        }
        return r;
    }

    /* Inject our persistent blob, if present and not opted out. */
    size_t blob_size = 0;
    void *blob = gbvk_pipe_cache_load(&blob_size);
    if (!blob) return fn(device, pCreateInfo, pAllocator, pPipelineCache);

    /* PR-GGGG: validate VkPipelineCacheHeaderVersionOne header against the
     * current device identity BEFORE we ask the driver to consume the
     * blob.  The Vulkan spec says a driver MAY accept a blob with mismatched
     * vendor/device/UUID but is allowed to discard it silently , checking
     * up front saves the driver a parse pass and gives us a clear log
     * line when a card swap / driver upgrade invalidates the cache. */
    if (blob_size >= 32) {
        const uint8_t *h = (const uint8_t *)blob;
        uint32_t hdr_size = (uint32_t)h[0] | ((uint32_t)h[1] << 8) |
                            ((uint32_t)h[2] << 16) | ((uint32_t)h[3] << 24);
        uint32_t hdr_ver  = (uint32_t)h[4] | ((uint32_t)h[5] << 8) |
                            ((uint32_t)h[6] << 16) | ((uint32_t)h[7] << 24);
        uint32_t vendor   = (uint32_t)h[8] | ((uint32_t)h[9] << 8) |
                            ((uint32_t)h[10] << 16) | ((uint32_t)h[11] << 24);
        uint32_t dev_id   = (uint32_t)h[12] | ((uint32_t)h[13] << 8) |
                            ((uint32_t)h[14] << 16) | ((uint32_t)h[15] << 24);

        pthread_mutex_lock(&g_mutex);
        GbDevData *dd = dev_find(device);
        int       valid_id = dd ? dd->identity_valid : 0;
        uint32_t  cur_vendor = dd ? dd->vendor_id : 0;
        uint32_t  cur_dev    = dd ? dd->device_id : 0;
        uint8_t   cur_uuid[16];
        if (dd) memcpy(cur_uuid, dd->cache_uuid, 16);
        pthread_mutex_unlock(&g_mutex);

        int mismatch = 0;
        if (hdr_size != 32 || hdr_ver != VK_PIPELINE_CACHE_HEADER_VERSION_ONE)
            mismatch |= 1;
        if (valid_id) {
            if (vendor != cur_vendor) mismatch |= 2;
            if (dev_id != cur_dev)    mismatch |= 4;
            if (memcmp(h + 16, cur_uuid, 16) != 0) mismatch |= 8;
        }
        if (mismatch) {
            char path[1024];
            gbvk_pipe_cache_path(path, sizeof path);
            unlink(path);
            free(blob);
            gbvk_emit(0, "vkCreatePipelineCache: blob header mismatch "
                         "(reason=0x%x, vendor=0x%04x dev=0x%04x) , purged",
                         mismatch, vendor, dev_id);
            return fn(device, pCreateInfo, pAllocator, pPipelineCache);
        }
    } else {
        /* Truncated blob , toss it. */
        char path[1024];
        gbvk_pipe_cache_path(path, sizeof path);
        unlink(path);
        free(blob);
        return fn(device, pCreateInfo, pAllocator, pPipelineCache);
    }

    VkPipelineCacheCreateInfo ci = *pCreateInfo;
    ci.initialDataSize = blob_size;
    ci.pInitialData    = blob;
    VkResult res = fn(device, &ci, pAllocator, pPipelineCache);
    if (res != VK_SUCCESS) {
        /* Driver rejected the blob (version mismatch, vendor change).  Retry
         * with no seed so the app still gets a valid cache, and nuke the
         * stale blob so we don't keep hitting this. */
        char path[1024];
        gbvk_pipe_cache_path(path, sizeof path);
        unlink(path);
        res = fn(device, pCreateInfo, pAllocator, pPipelineCache);
        gbvk_emit(0, "vkCreatePipelineCache: seed rejected, blob purged");
    } else {
        gbvk_emit(2, "vkCreatePipelineCache: seeded with %zu bytes", blob_size);
    }
    free(blob);
    if (res == VK_SUCCESS && fn_gpcd) {
        gbvk_pipe_track_add(device, *pPipelineCache, fn_gpcd);
        gbvk_pipe_snapshot_thread_start_once();
    }
    return res;
}

static VKAPI_ATTR void VKAPI_CALL
gbvk_DestroyPipelineCache(VkDevice                     device,
                          VkPipelineCache              pipelineCache,
                          const VkAllocationCallbacks *pAllocator)
{
    pthread_mutex_lock(&g_mutex);
    GbDevData *d = dev_find(device);
    PFN_vkDestroyPipelineCache fn_dpc = d ? d->next_destroy_pipeline_cache : NULL;
    PFN_vkGetPipelineCacheData fn_gpcd = d ? d->next_get_pipeline_cache_data : NULL;
    pthread_mutex_unlock(&g_mutex);
    gbvk_pipe_track_remove(pipelineCache);

    /* Snapshot the cache before tearing it down. */
    if (fn_gpcd && pipelineCache && gbvk_pipe_cache_enabled()) {
        size_t sz = 0;
        if (fn_gpcd(device, pipelineCache, &sz, NULL) == VK_SUCCESS && sz > 0 && sz < (512 << 20)) {
            void *buf = malloc(sz);
            if (buf && fn_gpcd(device, pipelineCache, &sz, buf) == VK_SUCCESS) {
                gbvk_pipe_cache_save(buf, sz);
                gbvk_emit(2, "vkDestroyPipelineCache: persisted %zu bytes", sz);
            }
            free(buf);
        }
    }
    if (fn_dpc) fn_dpc(device, pipelineCache, pAllocator);
}

static VKAPI_ATTR VkResult VKAPI_CALL
gbvk_CreateGraphicsPipelines(VkDevice                             device,
                             VkPipelineCache                      pipelineCache,
                             uint32_t                             count,
                             const VkGraphicsPipelineCreateInfo  *pCreateInfos,
                             const VkAllocationCallbacks         *pAllocator,
                             VkPipeline                          *pPipelines)
{
    pthread_mutex_lock(&g_mutex);
    GbDevData *d = dev_find(device);
    PFN_vkCreateGraphicsPipelines fn = d ? d->next_create_graphics_pipelines : NULL;
    pthread_mutex_unlock(&g_mutex);
    if (!fn) return VK_ERROR_INITIALIZATION_FAILED;
    uint64_t t0 = gbvk_mono_ns();
    VkResult res = fn(device, pipelineCache, count, pCreateInfos, pAllocator, pPipelines);
    uint64_t dt = gbvk_mono_ns() - t0;
    atomic_fetch_add_explicit(&g_gbvk_pipe_count_g, count, memory_order_relaxed);
    atomic_fetch_add_explicit(&g_gbvk_pipe_total_ns, dt, memory_order_relaxed);
    /* Track slowest single batch (lock-free max via CAS). */
    uint64_t cur = atomic_load_explicit(&g_gbvk_pipe_slow_ns, memory_order_relaxed);
    while (dt > cur && !atomic_compare_exchange_weak_explicit(
            &g_gbvk_pipe_slow_ns, &cur, dt,
            memory_order_relaxed, memory_order_relaxed)) {}
    return res;
}

/* PR-GGGG: vkMergePipelineCaches passthrough + post-merge snapshot.
 * When an app builds up incremental data via merging (DXVK does this with
 * its in-memory state cache → driver cache), capture the merge result so
 * a crash doesn't lose it. */
static VKAPI_ATTR VkResult VKAPI_CALL
gbvk_MergePipelineCaches(VkDevice                device,
                         VkPipelineCache         dst,
                         uint32_t                srcCount,
                         const VkPipelineCache  *pSrcCaches)
{
    pthread_mutex_lock(&g_mutex);
    GbDevData *d = dev_find(device);
    PFN_vkMergePipelineCaches  fn       = d ? d->next_merge_pipeline_caches    : NULL;
    PFN_vkGetPipelineCacheData fn_gpcd  = d ? d->next_get_pipeline_cache_data  : NULL;
    pthread_mutex_unlock(&g_mutex);
    if (!fn) return VK_ERROR_INITIALIZATION_FAILED;

    VkResult res = fn(device, dst, srcCount, pSrcCaches);
    if (res != VK_SUCCESS || !fn_gpcd || !gbvk_pipe_cache_enabled()) return res;

    /* Cheap snapshot only , skip if dst isn't being tracked (i.e. it was
     * created outside our CreatePipelineCache hook somehow). */
    int tracked = 0;
    pthread_mutex_lock(&g_pipe_caches_mu);
    for (int i = 0; i < GBVK_MAX_PIPE_CACHES; i++) {
        if (g_pipe_caches[i].cache == dst) { tracked = 1; break; }
    }
    pthread_mutex_unlock(&g_pipe_caches_mu);
    if (!tracked) return res;

    size_t sz = 0;
    if (fn_gpcd(device, dst, &sz, NULL) == VK_SUCCESS &&
        sz > 0 && sz < (512U << 20)) {
        void *buf = malloc(sz);
        if (buf && fn_gpcd(device, dst, &sz, buf) == VK_SUCCESS) {
            gbvk_pipe_cache_save(buf, sz);
            gbvk_emit(2, "vkMergePipelineCaches: persisted %zu bytes after merge", sz);
        }
        free(buf);
    }
    return res;
}

/* A7: vkAcquireNextImageKHR , start of new simulation frame.
 * Increment the Reflex frame counter and inject SIMULATION_START marker.
 * Gate: GREENBOOST_REFLEX=1 and VK_NV_low_latency2 resolved at CreateDevice. */
static VKAPI_ATTR VkResult VKAPI_CALL
gbvk_AcquireNextImageKHR(VkDevice       device,
                          VkSwapchainKHR swapchain,
                          uint64_t       timeout,
                          VkSemaphore    semaphore,
                          VkFence        fence,
                          uint32_t      *pImageIndex)
{
    PFN_vkAcquireNextImageKHR fn_ani = NULL;
    PFN_vkSetLatencyMarkerNV  fn_slm = NULL;
    uint64_t frame_id = 0;

    pthread_mutex_lock(&g_mutex);
    GbDevData *d = dev_find(device);
    if (d) {
        fn_ani = d->next_acquire_image;
        fn_slm = d->next_set_latency_marker;
        if (fn_slm) frame_id = ++d->reflex_frame_id;
    }
    pthread_mutex_unlock(&g_mutex);

    if (!fn_ani) return VK_ERROR_INITIALIZATION_FAILED;

    VkResult r = fn_ani(device, swapchain, timeout, semaphore, fence, pImageIndex);

    if (r == VK_SUCCESS && fn_slm) {
        VkSetLatencyMarkerInfoNV info = {
            .sType     = VK_STRUCTURE_TYPE_SET_LATENCY_MARKER_INFO_NV,
            .pNext     = NULL,
            .presentID = frame_id,
            .marker    = VK_LATENCY_MARKER_SIMULATION_START_NV,
        };
        fn_slm(device, swapchain, &info);
    }
    return r;
}

/* PR-GGGG: vkQueuePresentKHR , frame pacing telemetry only.  We measure
 * present-to-present wall-clock intervals to characterise stutters.  The
 * call itself is passed through unchanged. */
static VKAPI_ATTR VkResult VKAPI_CALL
gbvk_QueuePresentKHR(VkQueue queue, const VkPresentInfoKHR *pPresentInfo)
{
    /* Resolve from the device that owns this queue.  Since the layer
     * doesn't track queue→device explicitly, walk the device table for any
     * one with a resolved present pointer; the loader dispatches us per
     * device so all live devices have a valid pointer cached. */
    PFN_vkQueuePresentKHR    fn     = NULL;
    PFN_vkSetLatencyMarkerNV fn_slm = NULL;
    VkDevice reflex_device = VK_NULL_HANDLE;
    uint64_t reflex_frame  = 0;

    pthread_mutex_lock(&g_mutex);
    for (int i = 0; i < GBVK_MAX_DEVICES; i++) {
        if (g_dev[i].device && g_dev[i].next_queue_present_khr) {
            fn             = g_dev[i].next_queue_present_khr;
            fn_slm         = g_dev[i].next_set_latency_marker;
            reflex_device  = g_dev[i].device;
            reflex_frame   = g_dev[i].reflex_frame_id;
            break;
        }
    }
    pthread_mutex_unlock(&g_mutex);
    if (!fn) return VK_ERROR_INITIALIZATION_FAILED;

    /* PR-GGGG: NIS dispatch path.  Submit the pre-recorded sharpen CBs
     * for each swapchain in pPresentInfo, then forward a rewritten
     * VkPresentInfoKHR that waits on our signal semaphores instead of
     * the caller's original ones.  Any precondition failure → passthrough. */
    GbNisPresentRewrite rewrite = {0};
    const VkPresentInfoKHR *forward = pPresentInfo;
    if (getenv("GREENBOOST_NIS_DISPATCH") &&
        getenv("GREENBOOST_NIS_DISPATCH")[0] == '1') {
        if (gbvk_nis_dispatch_present_v2(queue, pPresentInfo, &rewrite))
            forward = &rewrite.info;
    }

    /* Overlay pass. Chained AFTER any NIS rewrite so it draws on top of the
     * sharpened image rather than being sharpened itself, and so it waits on
     * NIS's semaphore instead of the acquire semaphore NIS already consumed. */
    VkPresentInfoKHR hud_info;
    VkSemaphore      hud_sem = VK_NULL_HANDLE;
    if (gbvk_hud_present(queue, forward, &hud_info, &hud_sem))
        forward = &hud_info;

    /* A7: PRESENT_START marker , tells the driver the CPU is about to submit
     * the present; driver uses this with SIMULATION_START to measure latency. */
    VkSwapchainKHR reflex_sc = (pPresentInfo->swapchainCount > 0)
                               ? pPresentInfo->pSwapchains[0] : VK_NULL_HANDLE;
    if (fn_slm && reflex_sc && reflex_device) {
        VkSetLatencyMarkerInfoNV info = {
            .sType     = VK_STRUCTURE_TYPE_SET_LATENCY_MARKER_INFO_NV,
            .pNext     = NULL,
            .presentID = reflex_frame,
            .marker    = VK_LATENCY_MARKER_PRESENT_START_NV,
        };
        fn_slm(reflex_device, reflex_sc, &info);
    }

    /* Sample monotonic time BEFORE the present so the interval includes
     * the GPU-wait portion (which is where shader-comp stutters surface). */
    uint64_t now  = gbvk_mono_ns();
    uint64_t prev = atomic_exchange_explicit(&g_gbvk_present_last_ns, now,
                                             memory_order_relaxed);
    atomic_fetch_add_explicit(&g_gbvk_present_count, 1, memory_order_relaxed);
    if (prev != 0) {
        uint64_t gap = now - prev;
        atomic_fetch_add_explicit(&g_gbvk_present_total_ns, gap, memory_order_relaxed);
        if (gap > 33000000ULL)
            atomic_fetch_add_explicit(&g_gbvk_present_hitches, 1, memory_order_relaxed);
        /* Lock-free max via CAS. */
        uint64_t cur = atomic_load_explicit(&g_gbvk_present_worst_ns, memory_order_relaxed);
        while (gap > cur && !atomic_compare_exchange_weak_explicit(
                &g_gbvk_present_worst_ns, &cur, gap,
                memory_order_relaxed, memory_order_relaxed)) {}
        /* PR-P1: store in ring buffer for percentile computation. */
        uint64_t wh = atomic_fetch_add_explicit(&g_gbvk_ft_head, 1, memory_order_relaxed);
        g_gbvk_ft_buf[wh & (GBVK_FTBUF_SIZE - 1)] = gap;
        atomic_fetch_add_explicit(&g_gbvk_ft_filled, 1, memory_order_relaxed);
    }

    VkResult r = fn(queue, forward);

    /* A7: PRESENT_END marker , present call returned; driver caps CPU sleep
     * to keep the pipeline at the target latency window. */
    if (fn_slm && reflex_sc && reflex_device) {
        VkSetLatencyMarkerInfoNV info = {
            .sType     = VK_STRUCTURE_TYPE_SET_LATENCY_MARKER_INFO_NV,
            .pNext     = NULL,
            .presentID = reflex_frame,
            .marker    = VK_LATENCY_MARKER_PRESENT_END_NV,
        };
        fn_slm(reflex_device, reflex_sc, &info);
    }
    return r;
}

static VKAPI_ATTR VkResult VKAPI_CALL
gbvk_CreateComputePipelines(VkDevice                            device,
                            VkPipelineCache                     pipelineCache,
                            uint32_t                            count,
                            const VkComputePipelineCreateInfo  *pCreateInfos,
                            const VkAllocationCallbacks        *pAllocator,
                            VkPipeline                         *pPipelines)
{
    pthread_mutex_lock(&g_mutex);
    GbDevData *d = dev_find(device);
    PFN_vkCreateComputePipelines fn = d ? d->next_create_compute_pipelines : NULL;
    pthread_mutex_unlock(&g_mutex);
    if (!fn) return VK_ERROR_INITIALIZATION_FAILED;
    uint64_t t0 = gbvk_mono_ns();
    VkResult res = fn(device, pipelineCache, count, pCreateInfos, pAllocator, pPipelines);
    uint64_t dt = gbvk_mono_ns() - t0;
    atomic_fetch_add_explicit(&g_gbvk_pipe_count_c, count, memory_order_relaxed);
    atomic_fetch_add_explicit(&g_gbvk_pipe_total_ns, dt, memory_order_relaxed);
    return res;
}

/* ── Proc-addr dispatch ─────────────────────────────────────────────────── */

static PFN_vkVoidFunction gbvk_GetDeviceProcAddr(VkDevice dev, const char *name);

static PFN_vkVoidFunction gbvk_GetInstanceProcAddr(VkInstance inst, const char *name)
{
#define HOOK(fn)  if (strcmp(name, "vk" #fn) == 0) return (PFN_vkVoidFunction)gbvk_##fn
    HOOK(GetInstanceProcAddr);
    HOOK(CreateInstance);
    HOOK(DestroyInstance);
    HOOK(CreateDevice);
    HOOK(DestroyDevice);
    HOOK(GetPhysicalDeviceMemoryProperties);
    HOOK(GetPhysicalDeviceMemoryProperties2);
    /* KHR alias , same implementation. */
    if (strcmp(name, "vkGetPhysicalDeviceMemoryProperties2KHR") == 0)
        return (PFN_vkVoidFunction)gbvk_GetPhysicalDeviceMemoryProperties2;
    HOOK(AllocateMemory);
    HOOK(FreeMemory);
#undef HOOK
    if (strcmp(name, "vkGetDeviceProcAddr") == 0)
        return (PFN_vkVoidFunction)gbvk_GetDeviceProcAddr;

    pthread_mutex_lock(&g_mutex);
    GbInstData *d = inst ? inst_find(inst) : NULL;
    PFN_vkGetInstanceProcAddr fn = d ? d->next_gipa : NULL;
    pthread_mutex_unlock(&g_mutex);
    if (fn) return fn(inst, name);
    return NULL;
}

static PFN_vkVoidFunction gbvk_GetDeviceProcAddr(VkDevice dev, const char *name)
{
#define HOOK(fn)  if (strcmp(name, "vk" #fn) == 0) return (PFN_vkVoidFunction)gbvk_##fn
    HOOK(GetDeviceProcAddr);
    HOOK(DestroyDevice);
    HOOK(AllocateMemory);
    HOOK(FreeMemory);
    /* PR-GGGG: persistent pipeline cache + compile telemetry + frame pacing. */
    HOOK(CreatePipelineCache);
    HOOK(DestroyPipelineCache);
    HOOK(MergePipelineCaches);
    HOOK(CreateGraphicsPipelines);
    HOOK(CreateComputePipelines);
    HOOK(QueuePresentKHR);
    /* PR-GGGG: image-on-spill detection. */
    HOOK(BindImageMemory);
    HOOK(BindImageMemory2);
    if (strcmp(name, "vkBindImageMemory2KHR") == 0)
        return (PFN_vkVoidFunction)gbvk_BindImageMemory2;
    /* PR-GGGG: NIS post-process swapchain hooks. */
    HOOK(CreateSwapchainKHR);
    HOOK(DestroySwapchainKHR);
    /* A7: Reflex , hook AcquireNextImageKHR for SIMULATION_START marker. */
    HOOK(AcquireNextImageKHR);
#undef HOOK

    pthread_mutex_lock(&g_mutex);
    GbDevData *d = dev ? dev_find(dev) : NULL;
    PFN_vkGetDeviceProcAddr fn = d ? d->next_gdpa : NULL;
    pthread_mutex_unlock(&g_mutex);
    if (fn) return fn(dev, name);
    return NULL;
}

/* ── Loader negotiation entry point ────────────────────────────────────── */

__attribute__((visibility("default")))
VKAPI_ATTR VkResult VKAPI_CALL
vkNegotiateLoaderLayerInterfaceVersion(VkNegotiateLayerInterface *pVersionStruct)
{
    if (pVersionStruct->loaderLayerInterfaceVersion > CURRENT_LOADER_LAYER_INTERFACE_VERSION)
        pVersionStruct->loaderLayerInterfaceVersion = CURRENT_LOADER_LAYER_INTERFACE_VERSION;

    pVersionStruct->pfnGetInstanceProcAddr       = gbvk_GetInstanceProcAddr;
    pVersionStruct->pfnGetDeviceProcAddr         = gbvk_GetDeviceProcAddr;
    pVersionStruct->pfnGetPhysicalDeviceProcAddr = NULL;
    return VK_SUCCESS;
}
