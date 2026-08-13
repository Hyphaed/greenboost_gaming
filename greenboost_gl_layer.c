/*
 * greenboost_gl_layer.c , GreenBoost OpenGL LD_PRELOAD interposer
 *
 * Activated by GREENBOOST_OPENGL=1 in the process environment.
 * Injected via LD_PRELOAD=/usr/local/lib/libgb_gl.so by the Proton wrapper.
 *
 * What this does (mirrors the Vulkan layer for the OpenGL path):
 *
 *  1. T2/T3 texture overflow
 *     Intercepts glTexStorage2D / glTextureStorage2D (DSA) and their 3D
 *     variants.  For textures >= GREENBOOST_GL_OVERFLOW_MIN_MB (default 32 MB):
 *       a. Allocate a DMA-BUF from the GreenBoost kernel module (GB_IOCTL_ALLOC).
 *       b. Import it as a GL memory object via GL_EXT_memory_object_fd
 *          (glCreateMemoryObjectsEXT + glImportMemoryFdEXT).
 *       c. Bind storage to the texture via glTexStorageMem2DEXT so the GPU
 *          accesses T2 DDR over PCIe instead of evicting existing VRAM content.
 *     This is the GL equivalent of VK_KHR_external_memory_fd used by the
 *     Vulkan layer.
 *
 *  2. T2/T3 buffer overflow
 *     Same DMA-BUF path for glBufferStorage / glNamedBufferStorage (immutable
 *     VBOs, SSBOs, UBOs) via glBufferStorageMemEXT / glNamedBufferStorageMemEXT.
 *
 *  3. Virtual VRAM inflation
 *     Intercepts glGetIntegerv for GL_GPU_MEMORY_INFO_TOTAL_AVAILABLE_MEMORY_NVX
 *     and GL_GPU_MEMORY_INFO_CURRENT_AVAILABLE_VIDMEM_NVX (NVX_gpu_memory_info).
 *     Games read these to set quality presets; we inflate them with T2 pool size,
 *     matching what the Vulkan layer does for vkGetPhysicalDeviceMemoryProperties.
 *
 *  4. gaming_mode signal
 *     First glXSwapBuffers / eglSwapBuffers call → GB_IOCTL_GAMING_MODE(1) +
 *     GB_IOCTL_SESSION_ACTIVE.  Inference T2 buffers get deprioritised while the
 *     game is running.  Cleared in the context-destroy / destructor path.
 *
 *  5. FPS telemetry + SIGUSR1/2 stats
 *     Same 512-slot ring buffer + worker thread as the Vulkan layer.
 *     kill -USR1 <pid> → one-line human-readable stats on stderr + log file.
 *     kill -USR2 <pid> → same + pool info refresh.
 *
 * Thread-safety rule (same as Vulkan layer):
 *   The mutex guards ONLY the dispatch/hash-table arrays.
 *   All external calls (dlsym targets, ioctl, real GL functions) are made
 *   OUTSIDE the mutex.
 *
 * Fallback guarantee:
 *   If GL_EXT_memory_object_fd is unavailable (Mesa without the ext, or driver
 *   too old), g_ext_resolved stays 0 and every hook falls through to the real
 *   GL function transparently.  No crash, no error.
 */

#define _GNU_SOURCE
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <stdint.h>
#include <stdatomic.h>
#include <unistd.h>
#include <fcntl.h>
#include <sys/ioctl.h>
#include <sys/stat.h>
#include <pthread.h>
#include <signal.h>
#include <syslog.h>
#include <time.h>
#include <stdarg.h>
#include <dlfcn.h>

#include <GL/gl.h>
#include <GL/glx.h>
#include <EGL/egl.h>

#include "greenboost_ioctl.h"

/* ── GL extension tokens (guarded , glext.h on Mesa already defines them) ── */

#ifndef GL_HANDLE_TYPE_OPAQUE_FD_EXT
#define GL_HANDLE_TYPE_OPAQUE_FD_EXT            0x9586u
#endif
#ifndef GL_GPU_MEMORY_INFO_TOTAL_AVAILABLE_MEMORY_NVX
#define GL_GPU_MEMORY_INFO_TOTAL_AVAILABLE_MEMORY_NVX   0x9048u
#endif
#ifndef GL_GPU_MEMORY_INFO_CURRENT_AVAILABLE_VIDMEM_NVX
#define GL_GPU_MEMORY_INFO_CURRENT_AVAILABLE_VIDMEM_NVX 0x9049u
#endif

/* ── Configuration globals ───────────────────────────────────────────── */

static int      g_gb_gl_active   = 0;   /* GREENBOOST_OPENGL=1 required       */
static int      g_gb_gl_debug    = 0;   /* GREENBOOST_GL_DEBUG=1              */

/* Virtual VRAM total (T1 phys + T2 pool).  Reported via glGetIntegerv. */
static uint64_t g_gb_gl_vram_total_bytes = 0;
/* Current available T2 slack (updated every 16 allocs) */
static uint64_t g_gb_gl_vram_free_extra  = 0;

/* Minimum texture/buffer size (bytes) to attempt T2 routing */
static uint64_t g_gb_gl_overflow_min = 32ULL * 1024ULL * 1024ULL;  /* 32 MB */

/* ── Logging ─────────────────────────────────────────────────────────── */

static int             g_gb_gl_log_fd  = -1;
static pthread_mutex_t g_gb_gl_log_mu  = PTHREAD_MUTEX_INITIALIZER;

static void gb_gl_mkdirs(const char *path)
{
    char buf[4096];
    snprintf(buf, sizeof buf, "%s", path);
    for (char *p = buf + 1; *p; p++) {
        if (*p == '/') { *p = '\0'; mkdir(buf, 0755); *p = '/'; }
    }
    mkdir(buf, 0755);
}

__attribute__((constructor(102)))   /* run after gbvk_init_logging which is 101 */
static void gb_gl_init_logging(void)
{
    const char *xdg  = getenv("XDG_DATA_HOME");
    const char *home = getenv("HOME");
    char dir[4096];
    if (xdg && xdg[0])
        snprintf(dir, sizeof dir, "%s/greenboost/proton-logs", xdg);
    else
        snprintf(dir, sizeof dir, "%s/.local/share/greenboost/proton-logs",
                 home && home[0] ? home : "/tmp");
    gb_gl_mkdirs(dir);

    char path[4200];
    snprintf(path, sizeof path, "%s/gl-layer.log", dir);
    g_gb_gl_log_fd = open(path, O_WRONLY | O_CREAT | O_APPEND | O_CLOEXEC, 0644);

    const char *dbg = getenv("GREENBOOST_GL_DEBUG");
    if (dbg && dbg[0] == '1') g_gb_gl_debug = 1;
}

__attribute__((destructor))
static void gb_gl_fini_logging(void)
{
    if (g_gb_gl_log_fd >= 0) { close(g_gb_gl_log_fd); g_gb_gl_log_fd = -1; }
}

static void gb_gl_emit(int level, const char *fmt, ...)
    __attribute__((format(printf, 2, 3)));
static void gb_gl_emit(int level, const char *fmt, ...)
{
    struct timespec ts;
    clock_gettime(CLOCK_REALTIME, &ts);
    struct tm tm;
    gmtime_r(&ts.tv_sec, &tm);

    char msg[2048];
    va_list ap;
    va_start(ap, fmt);
    vsnprintf(msg, sizeof msg, fmt, ap);
    va_end(ap);

    char buf[2304];
    int len = snprintf(buf, sizeof buf,
        "%04d-%02d-%02dT%02d:%02d:%02d.%03ldZ [GB_GL] %s\n",
        tm.tm_year + 1900, tm.tm_mon + 1, tm.tm_mday,
        tm.tm_hour, tm.tm_min, tm.tm_sec, ts.tv_nsec / 1000000L, msg);
    if (len < 0 || len >= (int)sizeof buf) len = (int)sizeof buf - 1;

    if (g_gb_gl_log_fd >= 0) {
        pthread_mutex_lock(&g_gb_gl_log_mu);
        (void)write(g_gb_gl_log_fd, buf, (size_t)len);
        pthread_mutex_unlock(&g_gb_gl_log_mu);
    }
    syslog(level, "[GB_GL] %s", msg);
    (void)write(STDERR_FILENO, buf, (size_t)len);
}

#define gb_log(fmt, ...) gb_gl_emit(LOG_INFO,  fmt, ##__VA_ARGS__)
#define gb_dbg(fmt, ...) do { if (g_gb_gl_debug) gb_gl_emit(LOG_DEBUG, fmt, ##__VA_ARGS__); } while(0)

/* ── /dev/greenboost fd ──────────────────────────────────────────────── */

static int            g_gb_gl_dev_fd   = -1;
static pthread_once_t g_gb_gl_dev_once = PTHREAD_ONCE_INIT;

static void gb_gl_open_dev(void)
{
    g_gb_gl_dev_fd = open("/dev/greenboost", O_RDWR | O_CLOEXEC);
    if (g_gb_gl_dev_fd < 0)
        gb_log("open /dev/greenboost failed , T2/T3 fallback disabled");
    else
        gb_log("opened /dev/greenboost fd=%d", g_gb_gl_dev_fd);
}

static int gb_gl_dev_fd(void)
{
    pthread_once(&g_gb_gl_dev_once, gb_gl_open_dev);
    return g_gb_gl_dev_fd;
}

/* ── Allocation tracking hash table ──────────────────────────────────── */
/*
 * Key: GLuint object name (texture or buffer).  Cast to uint64_t.
 * Same Fibonacci-hash + linear-probe design as the Vulkan layer.
 */
#define GB_GL_HT_BITS   11
#define GB_GL_HT_SIZE   (1u << GB_GL_HT_BITS)
#define GB_GL_HT_MASK   (GB_GL_HT_SIZE - 1u)
#define GB_GL_HT_LOCKS  8
#define GB_GL_HT_TOMB   ((GLuint)0xFFFFFFFFu)

#define GB_GL_OBJ_TEX   1
#define GB_GL_OBJ_BUF   2

typedef struct {
    GLuint   name;     /* GL texture or buffer name (0=empty, TOMB=tombstone)  */
    GLuint   memobj;   /* GL memory object name                                */
    int      dma_fd;   /* DMA-BUF fd (kept alive , kernel tracks liveness)     */
    int32_t  buf_id;   /* kernel IDR id from gb_alloc_req.fd for madvise       */
    uint64_t size;     /* allocation size in bytes                             */
    uint8_t  obj_type; /* GB_GL_OBJ_TEX or GB_GL_OBJ_BUF                     */
    uint8_t  tier;     /* 2=T2 DDR, 3=T3 NVMe                                 */
    uint8_t  hot;      /* 1 = marked HOT (working set)                         */
    uint8_t  _pad;
} __attribute__((aligned(64))) GbGlHtEntry;

static GbGlHtEntry    g_gb_gl_ht[GB_GL_HT_SIZE];
static pthread_mutex_t g_gb_gl_ht_locks[GB_GL_HT_LOCKS] = {
    PTHREAD_MUTEX_INITIALIZER, PTHREAD_MUTEX_INITIALIZER,
    PTHREAD_MUTEX_INITIALIZER, PTHREAD_MUTEX_INITIALIZER,
    PTHREAD_MUTEX_INITIALIZER, PTHREAD_MUTEX_INITIALIZER,
    PTHREAD_MUTEX_INITIALIZER, PTHREAD_MUTEX_INITIALIZER,
};

static inline uint32_t gb_gl_ht_hash(GLuint name)
{
    return (uint32_t)(((uint64_t)name * UINT64_C(0x9E3779B97F4A7C15)) >> (64 - GB_GL_HT_BITS));
}

static void gb_gl_ht_insert(GLuint name, GLuint memobj, int dma_fd,
                             int32_t buf_id, uint64_t size,
                             uint8_t obj_type, uint8_t tier)
{
    uint32_t h = gb_gl_ht_hash(name);
    pthread_mutex_t *lk = &g_gb_gl_ht_locks[h & (GB_GL_HT_LOCKS - 1)];
    pthread_mutex_lock(lk);
    for (uint32_t i = 0; i < GB_GL_HT_SIZE; i++) {
        uint32_t idx = (h + i) & GB_GL_HT_MASK;
        if (!g_gb_gl_ht[idx].name || g_gb_gl_ht[idx].name == GB_GL_HT_TOMB) {
            g_gb_gl_ht[idx].name     = name;
            g_gb_gl_ht[idx].memobj   = memobj;
            g_gb_gl_ht[idx].dma_fd   = dma_fd;
            g_gb_gl_ht[idx].buf_id   = buf_id;
            g_gb_gl_ht[idx].size     = size;
            g_gb_gl_ht[idx].obj_type = obj_type;
            g_gb_gl_ht[idx].tier     = tier;
            g_gb_gl_ht[idx].hot      = 0;
            break;
        }
    }
    pthread_mutex_unlock(lk);
}

static int gb_gl_ht_remove(GLuint name, GbGlHtEntry *out)
{
    if (!name || name == GB_GL_HT_TOMB) return 0;
    uint32_t h = gb_gl_ht_hash(name);
    pthread_mutex_t *lk = &g_gb_gl_ht_locks[h & (GB_GL_HT_LOCKS - 1)];
    pthread_mutex_lock(lk);
    int found = 0;
    for (uint32_t i = 0; i < GB_GL_HT_SIZE; i++) {
        uint32_t idx = (h + i) & GB_GL_HT_MASK;
        if (!g_gb_gl_ht[idx].name) break;
        if (g_gb_gl_ht[idx].name == name) {
            *out = g_gb_gl_ht[idx];
            g_gb_gl_ht[idx].name = GB_GL_HT_TOMB;
            found = 1;
            break;
        }
    }
    pthread_mutex_unlock(lk);
    return found;
}

/* ── Session statistics ──────────────────────────────────────────────── */

static _Atomic uint32_t g_gb_gl_t2_tex_count = 0;
static _Atomic uint32_t g_gb_gl_t3_tex_count = 0;
static _Atomic uint32_t g_gb_gl_t2_buf_count = 0;
static _Atomic uint32_t g_gb_gl_t3_buf_count = 0;
static _Atomic uint64_t g_gb_gl_t2_bytes     = 0;
static _Atomic uint64_t g_gb_gl_t3_bytes     = 0;
static _Atomic uint32_t g_gb_gl_oom_count    = 0;

/* ── Pool info cache (refreshed every 16 alloc attempts) ─────────────── */

#define GB_GL_INFO_INTERVAL 16
static struct gb_info   g_gb_gl_pool_info;
static _Atomic uint32_t g_gb_gl_alloc_ctr = 0;
static pthread_mutex_t  g_gb_gl_info_mu   = PTHREAD_MUTEX_INITIALIZER;

static void gb_gl_refresh_pool_info(void)
{
    int fd = gb_gl_dev_fd();
    if (fd < 0) return;
    struct gb_info info;
    if (ioctl(fd, GB_IOCTL_GET_INFO, &info) == 0) {
        pthread_mutex_lock(&g_gb_gl_info_mu);
        g_gb_gl_pool_info = info;
        pthread_mutex_unlock(&g_gb_gl_info_mu);

        if (info.vram_physical_mb > 0 && info.max_pool_mb > 0) {
            uint64_t total = (info.vram_physical_mb + info.max_pool_mb)
                             * 1024ULL * 1024ULL;
            if (total != g_gb_gl_vram_total_bytes) {
                g_gb_gl_vram_total_bytes = total;
                gb_dbg("pool refresh: heap target %llu GB (T1=%lluMB + T2=%lluMB)",
                    (unsigned long long)(total >> 30),
                    (unsigned long long)info.vram_physical_mb,
                    (unsigned long long)info.max_pool_mb);
            }
            g_gb_gl_vram_free_extra = info.available_mb * 1024ULL * 1024ULL;
        }
    }
}

static uint32_t gb_gl_t2_pressure(void)
{
    pthread_mutex_lock(&g_gb_gl_info_mu);
    uint32_t p = g_gb_gl_pool_info.t2_pressure;
    pthread_mutex_unlock(&g_gb_gl_info_mu);
    return p;
}

/* ── Frame-time ring buffer (same as Vulkan layer) ───────────────────── */

#define GB_GL_FTBUF_SIZE 512

static uint64_t         g_gb_gl_ft_buf[GB_GL_FTBUF_SIZE];
static _Atomic uint64_t g_gb_gl_ft_head   = 0;
static _Atomic uint64_t g_gb_gl_ft_filled = 0;
static _Atomic uint64_t g_gb_gl_present_count  = 0;
static _Atomic uint64_t g_gb_gl_present_last_ns = 0;
static _Atomic uint64_t g_gb_gl_present_total_ns = 0;
static _Atomic uint64_t g_gb_gl_present_worst_ns = 0;
static _Atomic uint64_t g_gb_gl_present_hitches  = 0;

static uint64_t gb_gl_mono_ns(void)
{
    struct timespec ts;
    clock_gettime(CLOCK_MONOTONIC, &ts);
    return (uint64_t)ts.tv_sec * 1000000000ULL + ts.tv_nsec;
}

/* ── gaming_mode state ───────────────────────────────────────────────── */

static _Atomic int g_gb_gl_gaming_active = 0;

static void gb_gl_gaming_on(void)
{
    if (atomic_exchange(&g_gb_gl_gaming_active, 1)) return;  /* already on */
    int fd = gb_gl_dev_fd();
    if (fd >= 0) {
        struct gb_gaming_req gr = { .active = 1, .reserved = 0 };
        ioctl(fd, GB_IOCTL_GAMING_MODE, &gr);
        struct gb_session_req sr = { .pid = 0, .reserved = 0 };
        ioctl(fd, GB_IOCTL_SESSION_ACTIVE, &sr);
    }
    int sfd = open("/sys/module/greenboost/parameters/gaming_mode", O_WRONLY | O_CLOEXEC);
    if (sfd >= 0) { (void)write(sfd, "1", 1); close(sfd); }
    gb_log("gaming_mode ON , inference T2 deprioritised");
}

static void gb_gl_gaming_off(void)
{
    if (!atomic_exchange(&g_gb_gl_gaming_active, 0)) return;  /* already off */
    int fd = gb_gl_dev_fd();
    if (fd >= 0) {
        struct gb_gaming_req gr = { .active = 0, .reserved = 0 };
        ioctl(fd, GB_IOCTL_GAMING_MODE, &gr);
        struct gb_session_req sr = { .pid = 0, .reserved = 0 };
        ioctl(fd, GB_IOCTL_SESSION_IDLE, &sr);
    }
    int sfd = open("/sys/module/greenboost/parameters/gaming_mode", O_WRONLY | O_CLOEXEC);
    if (sfd >= 0) { (void)write(sfd, "0", 1); close(sfd); }
    gb_log("gaming_mode OFF");
}

/* ── Burst detector ──────────────────────────────────────────────────── */

static _Atomic uint64_t g_gb_gl_last_alloc_ms = 0;
static _Atomic uint32_t g_gb_gl_burst_active  = 0;
#define GB_GL_BURST_QUIET_MS  2000

static uint64_t gb_gl_now_ms(void)
{
    struct timespec ts;
    clock_gettime(CLOCK_MONOTONIC, &ts);
    return (uint64_t)ts.tv_sec * 1000 + ts.tv_nsec / 1000000;
}

static void gb_gl_burst_record(void)
{
    uint64_t now  = gb_gl_now_ms();
    uint64_t prev = atomic_exchange(&g_gb_gl_last_alloc_ms, now);
    int was = atomic_load(&g_gb_gl_burst_active);
    if (!was || (now - prev >= GB_GL_BURST_QUIET_MS)) {
        atomic_store(&g_gb_gl_burst_active, 1);
        gb_gl_gaming_on();  /* first burst or new burst , ensure gaming mode on */
    }
}

static void gb_gl_burst_check(void)
{
    if (!atomic_load(&g_gb_gl_burst_active)) return;
    uint64_t now  = gb_gl_now_ms();
    uint64_t last = atomic_load(&g_gb_gl_last_alloc_ms);
    if (now - last < GB_GL_BURST_QUIET_MS) return;

    /* Burst ended , mark all tracked allocs HOT */
    int fd = gb_gl_dev_fd();
    uint32_t marked = 0;
    for (uint32_t i = 0; i < GB_GL_HT_SIZE; i++) {
        GLuint name = g_gb_gl_ht[i].name;
        if (!name || name == GB_GL_HT_TOMB) continue;
        uint32_t li = gb_gl_ht_hash(name) & (GB_GL_HT_LOCKS - 1);
        pthread_mutex_lock(&g_gb_gl_ht_locks[li]);
        GbGlHtEntry *e = &g_gb_gl_ht[i];
        if (e->name && e->name != GB_GL_HT_TOMB && !e->hot) {
            e->hot = 1;
            if (fd >= 0) {
                struct gb_madvise_req m = { .buf_id = e->buf_id,
                                            .advise = GB_MADVISE_HOT };
                ioctl(fd, GB_IOCTL_MADVISE, &m);
            }
            marked++;
        }
        pthread_mutex_unlock(&g_gb_gl_ht_locks[li]);
    }
    atomic_store(&g_gb_gl_burst_active, 0);
    if (marked)
        gb_log("burst ended: marked %u GL allocs HOT (game working set)", marked);
}

/* ── GL extension function pointers ─────────────────────────────────── */

typedef void (*PFNGLCREATEMEMORYOBJECTSEXT)  (GLsizei n, GLuint *objs);
typedef void (*PFNGLIMPORTMEMORYFDEXT)       (GLuint mem, GLuint64 size,
                                              GLenum handleType, GLint fd);
typedef void (*PFNGLDELETEMEMORYOBJECTSEXT)  (GLsizei n, const GLuint *objs);
typedef void (*PFNGLTEXSTORAGEMEM2DEXT)      (GLenum target, GLsizei levels,
                                              GLenum ifmt, GLsizei w, GLsizei h,
                                              GLuint mem, GLuint64 offset);
typedef void (*PFNGLTEXSTORAGEMEM3DEXT)      (GLenum target, GLsizei levels,
                                              GLenum ifmt, GLsizei w, GLsizei h,
                                              GLsizei d, GLuint mem, GLuint64 offset);
typedef void (*PFNGLTEXTURESTORAGEMEM2DEXT)  (GLuint tex, GLsizei levels,
                                              GLenum ifmt, GLsizei w, GLsizei h,
                                              GLuint mem, GLuint64 offset);
typedef void (*PFNGLTEXTURESTORAGEMEM3DEXT)  (GLuint tex, GLsizei levels,
                                              GLenum ifmt, GLsizei w, GLsizei h,
                                              GLsizei d, GLuint mem, GLuint64 offset);
typedef void (*PFNGLBUFFERSTORAGEMEMEXT)     (GLenum target, GLsizeiptr size,
                                              GLuint mem, GLuint64 offset);
typedef void (*PFNGLNAMEDBUFFERSTORAGEMEMEXT)(GLuint buf, GLsizeiptr size,
                                              GLuint mem, GLuint64 offset);

static PFNGLCREATEMEMORYOBJECTSEXT   real_glCreateMemoryObjectsEXT;
static PFNGLIMPORTMEMORYFDEXT        real_glImportMemoryFdEXT;
static PFNGLDELETEMEMORYOBJECTSEXT   real_glDeleteMemoryObjectsEXT;
static PFNGLTEXSTORAGEMEM2DEXT       real_glTexStorageMem2DEXT;
static PFNGLTEXSTORAGEMEM3DEXT       real_glTexStorageMem3DEXT;
static PFNGLTEXTURESTORAGEMEM2DEXT   real_glTextureStorageMem2DEXT;
static PFNGLTEXTURESTORAGEMEM3DEXT   real_glTextureStorageMem3DEXT;
static PFNGLBUFFERSTORAGEMEMEXT      real_glBufferStorageMemEXT;
static PFNGLNAMEDBUFFERSTORAGEMEMEXT real_glNamedBufferStorageMemEXT;

static int g_ext_resolved = 0;  /* latched once when all extension fns resolved */

static void gb_gl_resolve_ext(void)
{
    /* Try GLX path first, then EGL path.  Separate typed pointers so the
     * calling convention is correct (GLX takes GLubyte *, EGL takes char *). */
    __GLXextFuncPtr (*fn_glx)(const GLubyte *) =
        (__GLXextFuncPtr (*)(const GLubyte *))dlsym(RTLD_NEXT, "glXGetProcAddressARB");
    __eglMustCastToProperFunctionPointerType (*fn_egl)(const char *) = NULL;
    if (!fn_glx)
        fn_egl = (__eglMustCastToProperFunctionPointerType (*)(const char *))
                  dlsym(RTLD_NEXT, "eglGetProcAddress");

    if (!fn_glx && !fn_egl) {
        gb_log("GL extension resolution: no glXGetProcAddressARB / eglGetProcAddress found");
        return;
    }

#define RESOLVE(var, name) do { \
    __GLXextFuncPtr _fp = fn_glx ? fn_glx((const GLubyte *)(name))  \
                                 : (__GLXextFuncPtr)fn_egl(name);     \
    var = (__typeof__(var))_fp; \
} while (0)

    RESOLVE(real_glCreateMemoryObjectsEXT,   "glCreateMemoryObjectsEXT");
    RESOLVE(real_glImportMemoryFdEXT,        "glImportMemoryFdEXT");
    RESOLVE(real_glDeleteMemoryObjectsEXT,   "glDeleteMemoryObjectsEXT");
    RESOLVE(real_glTexStorageMem2DEXT,       "glTexStorageMem2DEXT");
    RESOLVE(real_glTexStorageMem3DEXT,       "glTexStorageMem3DEXT");
    RESOLVE(real_glTextureStorageMem2DEXT,   "glTextureStorageMem2DEXT");
    RESOLVE(real_glTextureStorageMem3DEXT,   "glTextureStorageMem3DEXT");
    RESOLVE(real_glBufferStorageMemEXT,      "glBufferStorageMemEXT");
    RESOLVE(real_glNamedBufferStorageMemEXT, "glNamedBufferStorageMemEXT");

#undef RESOLVE

    if (real_glCreateMemoryObjectsEXT && real_glImportMemoryFdEXT &&
        real_glTexStorageMem2DEXT) {
        g_ext_resolved = 1;
        gb_log("GL_EXT_memory_object_fd resolved , T2/T3 overflow active");
    } else {
        gb_log("GL_EXT_memory_object_fd NOT available , overflow disabled "
               "(driver too old, or mesa without the extension)");
    }
}

/* ── Real function pointers (dlsym RTLD_NEXT) ────────────────────────── */

typedef void      (*pfn_glTexStorage2D)(GLenum, GLsizei, GLenum, GLsizei, GLsizei);
typedef void      (*pfn_glTexStorage3D)(GLenum, GLsizei, GLenum, GLsizei, GLsizei, GLsizei);
typedef void      (*pfn_glTextureStorage2D)(GLuint, GLsizei, GLenum, GLsizei, GLsizei);
typedef void      (*pfn_glTextureStorage3D)(GLuint, GLsizei, GLenum, GLsizei, GLsizei, GLsizei);
typedef void      (*pfn_glDeleteTextures)(GLsizei, const GLuint *);
typedef void      (*pfn_glDeleteBuffers)(GLsizei, const GLuint *);
typedef void      (*pfn_glBufferStorage)(GLenum, GLsizeiptr, const void *, GLbitfield);
typedef void      (*pfn_glNamedBufferStorage)(GLuint, GLsizeiptr, const void *, GLbitfield);
typedef void      (*pfn_glGetIntegerv)(GLenum, GLint *);
typedef void      (*pfn_glGetInteger64v)(GLenum, GLint64 *);
typedef void      (*pfn_glXSwapBuffers)(Display *, GLXDrawable);
typedef EGLBoolean(*pfn_eglSwapBuffers)(EGLDisplay, EGLSurface);
typedef GLXContext (*pfn_glXCreateContextAttribsARB)(Display *, GLXFBConfig,
                                                     GLXContext, Bool, const int *);
typedef void      (*pfn_glXDestroyContext)(Display *, GLXContext);
typedef EGLContext(*pfn_eglCreateContext)(EGLDisplay, EGLConfig,
                                          EGLContext, const EGLint *);
typedef EGLBoolean(*pfn_eglDestroyContext)(EGLDisplay, EGLContext);
/* Use the system header's function-pointer typedef so our declarations
 * match the extern prototypes in glx.h / egl.h exactly. */
typedef __GLXextFuncPtr                          (*pfn_glXGetProcAddressARB)(const GLubyte *);
typedef __eglMustCastToProperFunctionPointerType (*pfn_eglGetProcAddress)(const char *);

static pfn_glTexStorage2D            real_glTexStorage2D;
static pfn_glTexStorage3D            real_glTexStorage3D;
static pfn_glTextureStorage2D        real_glTextureStorage2D;
static pfn_glTextureStorage3D        real_glTextureStorage3D;
static pfn_glDeleteTextures          real_glDeleteTextures;
static pfn_glDeleteBuffers           real_glDeleteBuffers;
static pfn_glBufferStorage           real_glBufferStorage;
static pfn_glNamedBufferStorage      real_glNamedBufferStorage;
static pfn_glGetIntegerv             real_glGetIntegerv;
static pfn_glGetInteger64v           real_glGetInteger64v;
static pfn_glXSwapBuffers            real_glXSwapBuffers;
static pfn_eglSwapBuffers            real_eglSwapBuffers;
static pfn_glXCreateContextAttribsARB real_glXCreateContextAttribsARB;
static pfn_glXDestroyContext         real_glXDestroyContext;
static pfn_eglCreateContext          real_eglCreateContext;
static pfn_eglDestroyContext         real_eglDestroyContext;
static pfn_glXGetProcAddressARB      real_glXGetProcAddressARB;
static pfn_eglGetProcAddress         real_eglGetProcAddress;

/* ── Signal handler + stats worker ──────────────────────────────────── */

static _Atomic int g_gb_gl_dump_req = 0;  /* 1=USR1, 2=USR2 */

static void gb_gl_sigusr(int sig)
{
    atomic_store(&g_gb_gl_dump_req, (sig == SIGUSR1) ? 1 : 2);
}

static void gb_gl_dump_stats(void)
{
    uint32_t t2tc = atomic_load(&g_gb_gl_t2_tex_count);
    uint32_t t3tc = atomic_load(&g_gb_gl_t3_tex_count);
    uint32_t t2bc = atomic_load(&g_gb_gl_t2_buf_count);
    uint32_t t3bc = atomic_load(&g_gb_gl_t3_buf_count);
    uint64_t t2b  = atomic_load(&g_gb_gl_t2_bytes);
    uint64_t t3b  = atomic_load(&g_gb_gl_t3_bytes);
    uint32_t oom  = atomic_load(&g_gb_gl_oom_count);
    uint64_t pcnt = atomic_load(&g_gb_gl_present_count);
    uint64_t ptot = atomic_load(&g_gb_gl_present_total_ns);
    uint64_t pwst = atomic_load(&g_gb_gl_present_worst_ns);
    uint64_t phit = atomic_load(&g_gb_gl_present_hitches);

    double fps = 0.0, mean_ms = 0.0, worst_ms = pwst / 1e6;
    if (pcnt > 1 && ptot > 0) {
        fps     = (double)(pcnt - 1) * 1e9 / (double)ptot;
        mean_ms = (double)ptot / (double)(pcnt > 1 ? pcnt - 1 : 1) / 1e6;
    }

    /* Compute 1% low from ring buffer */
    uint64_t filled = atomic_load(&g_gb_gl_ft_filled);
    uint64_t head   = atomic_load(&g_gb_gl_ft_head);
    uint64_t n_sam  = filled < GB_GL_FTBUF_SIZE ? filled : GB_GL_FTBUF_SIZE;
    double p1_fps   = 0.0;
    if (n_sam > 1) {
        /* Copy relevant slice (no lock needed , ring is single-writer) */
        uint64_t tmp[GB_GL_FTBUF_SIZE];
        uint64_t start = head >= n_sam ? head - n_sam : 0;
        for (uint64_t i = 0; i < n_sam; i++)
            tmp[i] = g_gb_gl_ft_buf[(start + i) & (GB_GL_FTBUF_SIZE - 1)];
        /* Simple selection for 99th-percentile frame time (1% slowest) */
        uint64_t p99_idx = (n_sam * 99) / 100;
        /* Partial insertion sort on small array */
        for (uint64_t i = 1; i < n_sam; i++) {
            uint64_t v = tmp[i]; uint64_t j = i;
            while (j > 0 && tmp[j-1] > v) { tmp[j] = tmp[j-1]; j--; }
            tmp[j] = v;
        }
        uint64_t p99_ns = tmp[p99_idx];
        if (p99_ns > 0) p1_fps = 1e9 / (double)p99_ns;
    }

    /* Human-readable */
    gb_log("--- GL layer stats ---");
    gb_log("T2 textures: %u  (%llu MB)   T3 textures: %u  (%llu MB)",
           t2tc, (unsigned long long)(t2b >> 20),
           t3tc, (unsigned long long)(t3b >> 20));
    gb_log("T2 buffers:  %u               T3 buffers:  %u",
           t2bc, t3bc);
    gb_log("OOM fallbacks: %u", oom);
    gb_log("fps=%.1f  mean_ms=%.2f  p1_fps=%.1f  worst_ms=%.2f  hitches=%llu",
           fps, mean_ms, p1_fps, worst_ms, (unsigned long long)phit);
    /* Machine-readable */
    gb_log("GreenBoost-GL|fps=%.1f|mean_ms=%.2f|p1_fps=%.1f|worst_ms=%.2f"
           "|t2_tex=%u|t3_tex=%u|t2_tex_mb=%llu|t3_tex_mb=%llu|t2_buf=%u|t3_buf=%u|oom=%u",
           fps, mean_ms, p1_fps, worst_ms,
           t2tc, t3tc, (unsigned long long)(t2b >> 20), (unsigned long long)(t3b >> 20),
           t2bc, t3bc, oom);
}

static void *gb_gl_stats_worker(void *arg)
{
    (void)arg;
    for (;;) {
        struct timespec ts = { .tv_sec = 1, .tv_nsec = 0 };
        nanosleep(&ts, NULL);

        int req = atomic_exchange(&g_gb_gl_dump_req, 0);
        if (req == 0) continue;

        gb_gl_dump_stats();
        if (req == 2) {
            /* SIGUSR2: also refresh pool info */
            gb_gl_refresh_pool_info();
            gb_log("SIGUSR2: pool info refreshed");
        }
    }
    return NULL;
}

static pthread_once_t g_gb_gl_sig_once    = PTHREAD_ONCE_INIT;
static pthread_once_t g_gb_gl_thread_once = PTHREAD_ONCE_INIT;

static void gb_gl_install_signals(void)
{
    struct sigaction sa;
    memset(&sa, 0, sizeof sa);
    sa.sa_handler = gb_gl_sigusr;
    sigemptyset(&sa.sa_mask);
    sa.sa_flags = SA_RESTART;
    sigaction(SIGUSR1, &sa, NULL);
    sigaction(SIGUSR2, &sa, NULL);
}

static void gb_gl_start_worker(void)
{
    pthread_t tid;
    pthread_attr_t attr;
    pthread_attr_init(&attr);
    pthread_attr_setdetachstate(&attr, PTHREAD_CREATE_DETACHED);
    pthread_create(&tid, &attr, gb_gl_stats_worker, NULL);
    pthread_attr_destroy(&attr);
}

/* ── Constructor / destructor ────────────────────────────────────────── */

__attribute__((constructor))
static void gb_gl_init(void)
{
    const char *env = getenv("GREENBOOST_OPENGL");
    if (!env || env[0] != '1')
        return;
    g_gb_gl_active = 1;

    /* Overflow threshold */
    const char *min_env = getenv("GREENBOOST_GL_OVERFLOW_MIN_MB");
    if (min_env) {
        long long mb = atoll(min_env);
        if (mb > 0)
            g_gb_gl_overflow_min = (uint64_t)mb * 1024ULL * 1024ULL;
    }

    /* Resolve real function pointers (non-extension: RTLD_NEXT) */
    real_glTexStorage2D           = dlsym(RTLD_NEXT, "glTexStorage2D");
    real_glTexStorage3D           = dlsym(RTLD_NEXT, "glTexStorage3D");
    real_glTextureStorage2D       = dlsym(RTLD_NEXT, "glTextureStorage2D");
    real_glTextureStorage3D       = dlsym(RTLD_NEXT, "glTextureStorage3D");
    real_glDeleteTextures         = dlsym(RTLD_NEXT, "glDeleteTextures");
    real_glDeleteBuffers          = dlsym(RTLD_NEXT, "glDeleteBuffers");
    real_glBufferStorage          = dlsym(RTLD_NEXT, "glBufferStorage");
    real_glNamedBufferStorage     = dlsym(RTLD_NEXT, "glNamedBufferStorage");
    real_glGetIntegerv            = dlsym(RTLD_NEXT, "glGetIntegerv");
    real_glGetInteger64v          = dlsym(RTLD_NEXT, "glGetInteger64v");
    real_glXSwapBuffers           = dlsym(RTLD_NEXT, "glXSwapBuffers");
    real_eglSwapBuffers           = dlsym(RTLD_NEXT, "eglSwapBuffers");
    real_glXCreateContextAttribsARB = dlsym(RTLD_NEXT, "glXCreateContextAttribsARB");
    real_glXDestroyContext        = dlsym(RTLD_NEXT, "glXDestroyContext");
    real_eglCreateContext         = dlsym(RTLD_NEXT, "eglCreateContext");
    real_eglDestroyContext        = dlsym(RTLD_NEXT, "eglDestroyContext");
    real_glXGetProcAddressARB     = dlsym(RTLD_NEXT, "glXGetProcAddressARB");
    real_eglGetProcAddress        = dlsym(RTLD_NEXT, "eglGetProcAddress");

    /* Virtual VRAM: sysfs → GB_IOCTL_GET_INFO → env var */
    {
        char buf[32];
        FILE *f;
        int phys_gb = -1, virt_gb = -1;
        f = fopen("/sys/module/greenboost/parameters/physical_vram_gb", "r");
        if (f) { if (fgets(buf, sizeof buf, f)) phys_gb = atoi(buf); fclose(f); }
        f = fopen("/sys/module/greenboost/parameters/virtual_vram_gb", "r");
        if (f) { if (fgets(buf, sizeof buf, f)) virt_gb = atoi(buf); fclose(f); }
        if (phys_gb > 0 && virt_gb > 0) {
            g_gb_gl_vram_total_bytes = ((uint64_t)phys_gb + (uint64_t)virt_gb)
                                       * 1024ULL * 1024ULL * 1024ULL;
        } else {
            gb_gl_refresh_pool_info();
        }
    }
    if (g_gb_gl_vram_total_bytes == 0) {
        const char *e = getenv("GREENBOOST_VIRTUAL_VRAM_MB");
        if (e) g_gb_gl_vram_total_bytes = (uint64_t)atoll(e) * 1024ULL * 1024ULL;
    }

    /* Signal handlers + worker thread */
    pthread_once(&g_gb_gl_sig_once,    gb_gl_install_signals);
    pthread_once(&g_gb_gl_thread_once, gb_gl_start_worker);

    gb_log("init: active | overflow>=%llu MB | vram_total=%llu GB",
           (unsigned long long)(g_gb_gl_overflow_min >> 20),
           (unsigned long long)(g_gb_gl_vram_total_bytes >> 30));
}

__attribute__((destructor))
static void gb_gl_fini(void)
{
    if (!g_gb_gl_active) return;
    gb_gl_gaming_off();
    int fd = gb_gl_dev_fd();
    if (fd >= 0) {
        struct gb_release_pid_req rp = { .pid = 0 };
        ioctl(fd, GB_IOCTL_RELEASE_PID, &rp);
    }
    gb_gl_dump_stats();
}

/* ── Format → bytes-per-texel estimation ────────────────────────────── */

/*
 * Returns bytes per texel for common GL internal formats.
 * Compressed formats return the block size (4×4 = 16 texels per block).
 * 0 = unknown → skip T2 routing.
 */
static uint64_t gb_gl_fmt_bytes_per_texel(GLenum ifmt)
{
    switch (ifmt) {
    /* 8-bit per channel */
    case 0x8229: /* GL_R8           */ return 1;
    case 0x8F94: /* GL_R8_SNORM     */ return 1;
    case 0x822B: /* GL_RG8          */ return 2;
    case 0x8058: /* GL_RGBA8        */ return 4;
    case 0x8051: /* GL_RGB8         */ return 3;
    case 0x8D9A: /* GL_R8UI         */ return 1;
    case 0x8D9B: /* GL_R8I          */ return 1;
    case 0x8238: /* GL_RG8UI        */ return 2;
    /* 16-bit per channel */
    case 0x822D: /* GL_R16          */ return 2;
    case 0x8056: /* GL_RGBA4        */ return 2;
    case 0x8054: /* GL_RGB5_A1      */ return 2;
    case 0x8D61: /* GL_HALF_FLOAT (RG16F approx) */ return 4;
    case 0x822F: /* GL_R16F         */ return 2;
    case 0x822E: /* GL_R16_SNORM    */ return 2;
    case 0x8231: /* GL_R16UI        */ return 2;
    case 0x8233: /* GL_RG16         */ return 4;
    case 0x8235: /* GL_RG16F        */ return 4;
    case 0x881A: /* GL_RGBA16F      */ return 8;
    case 0x8C3A: /* GL_R11F_G11F_B10F */ return 4;
    /* 32-bit per channel */
    case 0x8236: /* GL_R32F         */ return 4;
    case 0x8D70: /* GL_RGBA32UI     */ return 16;
    case 0x8D82: /* GL_RGBA32I      */ return 16;
    case 0x8814: /* GL_RGBA32F      */ return 16;
    case 0x8815: /* GL_RGB32F       */ return 12;
    case 0x8234: /* GL_RG32F        */ return 8;
    /* Depth/stencil */
    case 0x81A6: /* GL_DEPTH_COMPONENT16 */ return 2;
    case 0x81A7: /* GL_DEPTH_COMPONENT32 */ return 4;
    case 0x88F0: /* GL_DEPTH24_STENCIL8  */ return 4;
    case 0x8CAC: /* GL_DEPTH_COMPONENT32F */ return 4;
    /* Compressed , report block size (16 bytes per 4×4=16 texels → same per-texel divisor) */
    case 0x83F0: /* GL_COMPRESSED_RGB_S3TC_DXT1_EXT  */ return 0;  /* 4×4 = 8 bytes */
    case 0x83F1: /* GL_COMPRESSED_RGBA_S3TC_DXT1_EXT */ return 0;  /* 4×4 = 8 bytes */
    case 0x83F2: /* GL_COMPRESSED_RGBA_S3TC_DXT3_EXT */ return 0;  /* 4×4 = 16 bytes */
    case 0x83F3: /* GL_COMPRESSED_RGBA_S3TC_DXT5_EXT */ return 0;  /* 4×4 = 16 bytes */
    case 0x8E8C: /* GL_COMPRESSED_RGBA_BPTC_UNORM (BC7) */ return 0;
    default: return 0;
    }
}

static uint64_t gb_gl_est_tex_size(GLenum ifmt, GLsizei w, GLsizei h,
                                   GLsizei depth_or_1, GLsizei levels)
{
    uint64_t bpt = gb_gl_fmt_bytes_per_texel(ifmt);
    if (!bpt) {
        /* Compressed or unknown: estimate conservatively as RGBA8 / 6 (BC7≈) */
        bpt = 1;
    }
    /* Mip chain: sum = w*h * (1 + 0.25 + 0.0625 + …) ≈ w*h * 4/3 */
    uint64_t base = (uint64_t)w * (uint64_t)h * (uint64_t)(depth_or_1 > 0 ? depth_or_1 : 1) * bpt;
    if (levels > 1) base = base * 4 / 3;  /* approximate mip sum */
    return base;
}

/* ── T2/T3 overflow: texture ─────────────────────────────────────────── */
/*
 * Called from glTexStorage2D / glTextureStorage2D hooks.
 * On success: returns 1 and the caller must NOT call the real function.
 * On failure (ext unavail, OOM, size < threshold): returns 0.
 *
 * name == 0 means "use GL_TEXTURE_BINDING, caller knows current target".
 * name > 0 means DSA path (glTextureStorage2D).
 */
static int gb_gl_try_tex_t2(GLuint name, GLenum target, GLsizei levels,
                             GLenum ifmt, GLsizei w, GLsizei h, GLsizei depth)
{
    uint64_t est = gb_gl_est_tex_size(ifmt, w, h, depth, levels);
    if (est < g_gb_gl_overflow_min)
        return 0;

    /* Skip if T2 pool is critical and this alloc is very large */
    uint32_t ctr = atomic_fetch_add(&g_gb_gl_alloc_ctr, 1);
    if ((ctr & (GB_GL_INFO_INTERVAL - 1)) == 0)
        gb_gl_refresh_pool_info();
    if (gb_gl_t2_pressure() == GB_T2_PRESSURE_CRITICAL && est >= 256ULL * 1024 * 1024)
        return 0;

    /* Resolve extension functions lazily (needs an active GL context) */
    if (!g_ext_resolved) {
        gb_gl_resolve_ext();
        if (!g_ext_resolved) return 0;
    }

    /* Allocate DMA-BUF from kernel module */
    int fd = gb_gl_dev_fd();
    if (fd < 0) return 0;

    struct gb_alloc_req req;
    memset(&req, 0, sizeof req);
    req.size  = est;
    req.flags = GB_ALLOC_WEIGHTS | GB_ALLOC_SESSION_PROTECTED;

    if (ioctl(fd, GB_IOCTL_ALLOC, &req) < 0) {
        atomic_fetch_add(&g_gb_gl_oom_count, 1);
        gb_dbg("T2 tex alloc failed: %llu MB OOM", (unsigned long long)(est >> 20));
        return 0;
    }

    /* Create GL memory object and import DMA-BUF */
    GLuint memobj = 0;
    real_glCreateMemoryObjectsEXT(1, &memobj);
    real_glImportMemoryFdEXT(memobj, (GLuint64)est,
                             GL_HANDLE_TYPE_OPAQUE_FD_EXT, (GLint)req.fd);

    /* Allocate texture storage backed by the memory object */
    int ok = 0;
    if (name > 0) {
        /* DSA path */
        if (depth > 0 && real_glTextureStorageMem3DEXT) {
            real_glTextureStorageMem3DEXT(name, levels, ifmt, w, h, depth, memobj, 0);
            ok = 1;
        } else if (real_glTextureStorageMem2DEXT) {
            real_glTextureStorageMem2DEXT(name, levels, ifmt, w, h, memobj, 0);
            ok = 1;
        }
    } else {
        if (depth > 0 && real_glTexStorageMem3DEXT) {
            real_glTexStorageMem3DEXT(target, levels, ifmt, w, h, depth, memobj, 0);
            ok = 1;
        } else {
            real_glTexStorageMem2DEXT(target, levels, ifmt, w, h, memobj, 0);
            ok = 1;
        }
    }

    if (!ok) {
        real_glDeleteMemoryObjectsEXT(1, &memobj);
        close(req.fd);
        atomic_fetch_add(&g_gb_gl_oom_count, 1);
        return 0;
    }

    /* Track for cleanup */
    GLuint track_name = name;
    if (!track_name) {
        /* Non-DSA: query current binding */
        GLint cur = 0;
        GLenum binding = (target == 0x8C1A /* GL_TEXTURE_2D_ARRAY */ ||
                          target == 0x806F /* GL_TEXTURE_3D */)
                         ? 0x806A /* GL_TEXTURE_BINDING_3D */
                         : 0x8069; /* GL_TEXTURE_BINDING_2D */
        if (real_glGetIntegerv) real_glGetIntegerv(binding, &cur);
        track_name = (GLuint)cur;
    }
    if (track_name)
        gb_gl_ht_insert(track_name, memobj, req.fd, req.fd, est,
                        GB_GL_OBJ_TEX, 2);
    /* Note: req.fd is reused as buf_id; the kernel IDR maps fd→buf */

    atomic_fetch_add(&g_gb_gl_t2_tex_count, 1);
    atomic_fetch_add(&g_gb_gl_t2_bytes, est);
    gb_gl_burst_record();

    gb_dbg("T2 tex ok: name=%u %ux%u ifmt=0x%x %llu MB",
           track_name, w, h, ifmt, (unsigned long long)(est >> 20));
    return 1;
}

/* ── T2/T3 overflow: buffer ─────────────────────────────────────────── */

static int gb_gl_try_buf_t2(GLuint name, GLenum target, GLsizeiptr size,
                             const void *data, GLbitfield flags)
{
    if ((uint64_t)size < g_gb_gl_overflow_min) return 0;

    uint32_t ctr = atomic_fetch_add(&g_gb_gl_alloc_ctr, 1);
    if ((ctr & (GB_GL_INFO_INTERVAL - 1)) == 0)
        gb_gl_refresh_pool_info();
    if (gb_gl_t2_pressure() == GB_T2_PRESSURE_CRITICAL &&
        (uint64_t)size >= 256ULL * 1024 * 1024)
        return 0;

    if (!g_ext_resolved) {
        gb_gl_resolve_ext();
        if (!g_ext_resolved) return 0;
    }

    int fd = gb_gl_dev_fd();
    if (fd < 0) return 0;

    struct gb_alloc_req req;
    memset(&req, 0, sizeof req);
    req.size  = (uint64_t)size;
    req.flags = GB_ALLOC_WEIGHTS | GB_ALLOC_SESSION_PROTECTED;

    if (ioctl(fd, GB_IOCTL_ALLOC, &req) < 0) {
        atomic_fetch_add(&g_gb_gl_oom_count, 1);
        return 0;
    }

    GLuint memobj = 0;
    real_glCreateMemoryObjectsEXT(1, &memobj);
    real_glImportMemoryFdEXT(memobj, (GLuint64)size,
                             GL_HANDLE_TYPE_OPAQUE_FD_EXT, (GLint)req.fd);

    int ok = 0;
    if (name > 0 && real_glNamedBufferStorageMemEXT) {
        real_glNamedBufferStorageMemEXT(name, size, memobj, 0);
        ok = 1;
    } else if (real_glBufferStorageMemEXT) {
        real_glBufferStorageMemEXT(target, size, memobj, 0);
        ok = 1;
    }

    if (!ok) {
        real_glDeleteMemoryObjectsEXT(1, &memobj);
        close(req.fd);
        atomic_fetch_add(&g_gb_gl_oom_count, 1);
        return 0;
    }

    /* If the caller provided initial data, upload it now */
    if (data && ok) {
        typedef void (*pfn_glBufferSubData)(GLenum, GLintptr, GLsizeiptr, const void *);
        typedef void (*pfn_glNamedBufferSubData)(GLuint, GLintptr, GLsizeiptr, const void *);
        if (name > 0) {
            pfn_glNamedBufferSubData fn =
                dlsym(RTLD_NEXT, "glNamedBufferSubData");
            if (fn) fn(name, 0, size, data);
        } else {
            pfn_glBufferSubData fn = dlsym(RTLD_NEXT, "glBufferSubData");
            if (fn) fn(target, 0, size, data);
        }
    }

    GLuint track_name = name;
    if (!track_name && real_glGetIntegerv) {
        GLint cur = 0;
        GLenum binding = GL_ARRAY_BUFFER; /* best-effort: assume ARRAY_BUFFER */
        real_glGetIntegerv(0x8894 /* GL_ARRAY_BUFFER_BINDING */, &cur);
        (void)binding;
        track_name = (GLuint)cur;
    }
    if (track_name)
        gb_gl_ht_insert(track_name, memobj, req.fd, req.fd, (uint64_t)size,
                        GB_GL_OBJ_BUF, 2);

    atomic_fetch_add(&g_gb_gl_t2_buf_count, 1);
    atomic_fetch_add(&g_gb_gl_t2_bytes, (uint64_t)size);
    gb_gl_burst_record();

    gb_dbg("T2 buf ok: name=%u size=%llu MB target=0x%x",
           track_name, (unsigned long long)((uint64_t)size >> 20), target);
    return 1;
}

/* ── Cleanup helper ─────────────────────────────────────────────────── */

static void gb_gl_free_tracked(GLuint name)
{
    GbGlHtEntry e;
    if (!gb_gl_ht_remove(name, &e)) return;

    if (real_glDeleteMemoryObjectsEXT && e.memobj)
        real_glDeleteMemoryObjectsEXT(1, &e.memobj);
    if (e.dma_fd >= 0)
        close(e.dma_fd);

    if (e.obj_type == GB_GL_OBJ_TEX) {
        if (e.tier == 2) {
            atomic_fetch_sub(&g_gb_gl_t2_tex_count, 1);
            atomic_fetch_sub(&g_gb_gl_t2_bytes, e.size);
        } else {
            atomic_fetch_sub(&g_gb_gl_t3_tex_count, 1);
            atomic_fetch_sub(&g_gb_gl_t3_bytes, e.size);
        }
    } else {
        if (e.tier == 2) {
            atomic_fetch_sub(&g_gb_gl_t2_buf_count, 1);
            atomic_fetch_sub(&g_gb_gl_t2_bytes, e.size);
        } else {
            atomic_fetch_sub(&g_gb_gl_t3_buf_count, 1);
            atomic_fetch_sub(&g_gb_gl_t3_bytes, e.size);
        }
    }
    gb_dbg("freed tracked %s name=%u tier=%u size=%llu MB",
           e.obj_type == GB_GL_OBJ_TEX ? "tex" : "buf",
           name, e.tier, (unsigned long long)(e.size >> 20));
}

/* ── Exported hook functions ─────────────────────────────────────────── */

void glTexStorage2D(GLenum target, GLsizei levels, GLenum internalformat,
                    GLsizei width, GLsizei height)
{
    if (g_gb_gl_active &&
        gb_gl_try_tex_t2(0, target, levels, internalformat, width, height, 0))
        return;
    if (real_glTexStorage2D)
        real_glTexStorage2D(target, levels, internalformat, width, height);
}

void glTexStorage3D(GLenum target, GLsizei levels, GLenum internalformat,
                    GLsizei width, GLsizei height, GLsizei depth)
{
    if (g_gb_gl_active &&
        gb_gl_try_tex_t2(0, target, levels, internalformat, width, height, depth))
        return;
    if (real_glTexStorage3D)
        real_glTexStorage3D(target, levels, internalformat, width, height, depth);
}

void glTextureStorage2D(GLuint texture, GLsizei levels, GLenum internalformat,
                        GLsizei width, GLsizei height)
{
    if (g_gb_gl_active &&
        gb_gl_try_tex_t2(texture, 0, levels, internalformat, width, height, 0))
        return;
    if (real_glTextureStorage2D)
        real_glTextureStorage2D(texture, levels, internalformat, width, height);
}

void glTextureStorage3D(GLuint texture, GLsizei levels, GLenum internalformat,
                        GLsizei width, GLsizei height, GLsizei depth)
{
    if (g_gb_gl_active &&
        gb_gl_try_tex_t2(texture, 0, levels, internalformat, width, height, depth))
        return;
    if (real_glTextureStorage3D)
        real_glTextureStorage3D(texture, levels, internalformat, width, height, depth);
}

void glDeleteTextures(GLsizei n, const GLuint *textures)
{
    if (g_gb_gl_active && textures) {
        for (GLsizei i = 0; i < n; i++)
            gb_gl_free_tracked(textures[i]);
    }
    if (real_glDeleteTextures)
        real_glDeleteTextures(n, textures);
}

void glDeleteBuffers(GLsizei n, const GLuint *buffers)
{
    if (g_gb_gl_active && buffers) {
        for (GLsizei i = 0; i < n; i++)
            gb_gl_free_tracked(buffers[i]);
    }
    if (real_glDeleteBuffers)
        real_glDeleteBuffers(n, buffers);
}

void glBufferStorage(GLenum target, GLsizeiptr size, const void *data, GLbitfield flags)
{
    if (g_gb_gl_active &&
        gb_gl_try_buf_t2(0, target, size, data, flags))
        return;
    if (real_glBufferStorage)
        real_glBufferStorage(target, size, data, flags);
}

void glNamedBufferStorage(GLuint buffer, GLsizeiptr size,
                          const void *data, GLbitfield flags)
{
    if (g_gb_gl_active &&
        gb_gl_try_buf_t2(buffer, 0, size, data, flags))
        return;
    if (real_glNamedBufferStorage)
        real_glNamedBufferStorage(buffer, size, data, flags);
}

/* ── Virtual VRAM inflation (NVX_gpu_memory_info) ──────────────────── */

void glGetIntegerv(GLenum pname, GLint *params)
{
    if (real_glGetIntegerv)
        real_glGetIntegerv(pname, params);

    if (!g_gb_gl_active || !params || g_gb_gl_vram_total_bytes == 0)
        return;

    /* Values are in KiB */
    if (pname == GL_GPU_MEMORY_INFO_TOTAL_AVAILABLE_MEMORY_NVX) {
        GLint extra_kib = (GLint)(g_gb_gl_vram_total_bytes / 1024);
        *params += extra_kib;
    } else if (pname == GL_GPU_MEMORY_INFO_CURRENT_AVAILABLE_VIDMEM_NVX) {
        GLint extra_kib = (GLint)(g_gb_gl_vram_free_extra / 1024);
        *params += extra_kib;
    }
}

void glGetInteger64v(GLenum pname, GLint64 *params)
{
    if (real_glGetInteger64v)
        real_glGetInteger64v(pname, params);

    if (!g_gb_gl_active || !params || g_gb_gl_vram_total_bytes == 0)
        return;

    if (pname == GL_GPU_MEMORY_INFO_TOTAL_AVAILABLE_MEMORY_NVX) {
        *params += (GLint64)(g_gb_gl_vram_total_bytes / 1024);
    } else if (pname == GL_GPU_MEMORY_INFO_CURRENT_AVAILABLE_VIDMEM_NVX) {
        *params += (GLint64)(g_gb_gl_vram_free_extra / 1024);
    }
}

/* ── Swap / frame boundary hooks ─────────────────────────────────────── */

static void gb_gl_on_swap(void)
{
    /* First swap → gaming mode on */
    gb_gl_gaming_on();

    /* Frame-time telemetry */
    uint64_t now = gb_gl_mono_ns();
    uint64_t cnt = atomic_fetch_add(&g_gb_gl_present_count, 1);
    if (cnt > 0) {
        uint64_t prev = atomic_exchange(&g_gb_gl_present_last_ns, now);
        uint64_t delta = now - prev;
        atomic_fetch_add(&g_gb_gl_present_total_ns, delta);
        if (delta > 33000000ULL)  /* >33 ms = hitch at 30 fps */
            atomic_fetch_add(&g_gb_gl_present_hitches, 1);
        uint64_t cur_worst = atomic_load(&g_gb_gl_present_worst_ns);
        while (delta > cur_worst &&
               !atomic_compare_exchange_weak(&g_gb_gl_present_worst_ns,
                                             &cur_worst, delta))
            {}

        uint64_t h = atomic_fetch_add(&g_gb_gl_ft_head, 1);
        g_gb_gl_ft_buf[h & (GB_GL_FTBUF_SIZE - 1)] = delta;
        atomic_fetch_add(&g_gb_gl_ft_filled, 1);
    } else {
        atomic_store(&g_gb_gl_present_last_ns, now);
    }

    /* Burst check every 64 frames */
    if ((cnt & 63) == 0)
        gb_gl_burst_check();
}

void glXSwapBuffers(Display *dpy, GLXDrawable drawable)
{
    if (g_gb_gl_active) gb_gl_on_swap();
    if (real_glXSwapBuffers)
        real_glXSwapBuffers(dpy, drawable);
}

EGLBoolean eglSwapBuffers(EGLDisplay dpy, EGLSurface surface)
{
    if (g_gb_gl_active) gb_gl_on_swap();
    if (real_eglSwapBuffers)
        return real_eglSwapBuffers(dpy, surface);
    return EGL_TRUE;
}

/* ── Context lifecycle ───────────────────────────────────────────────── */

GLXContext glXCreateContextAttribsARB(Display *dpy, GLXFBConfig config,
                                      GLXContext share, Bool direct,
                                      const int *attribs)
{
    if (!real_glXCreateContextAttribsARB) return NULL;
    GLXContext ctx = real_glXCreateContextAttribsARB(dpy, config, share,
                                                     direct, attribs);
    gb_dbg("glXCreateContextAttribsARB: ctx=%p", (void *)ctx);
    return ctx;
}

void glXDestroyContext(Display *dpy, GLXContext ctx)
{
    gb_dbg("glXDestroyContext: ctx=%p", (void *)ctx);
    if (g_gb_gl_active) gb_gl_gaming_off();
    if (real_glXDestroyContext)
        real_glXDestroyContext(dpy, ctx);
}

EGLContext eglCreateContext(EGLDisplay dpy, EGLConfig config,
                            EGLContext share, const EGLint *attribs)
{
    if (!real_eglCreateContext) return EGL_NO_CONTEXT;
    EGLContext ctx = real_eglCreateContext(dpy, config, share, attribs);
    gb_dbg("eglCreateContext: ctx=%p", (void *)ctx);
    return ctx;
}

EGLBoolean eglDestroyContext(EGLDisplay dpy, EGLContext ctx)
{
    gb_dbg("eglDestroyContext: ctx=%p", (void *)ctx);
    if (g_gb_gl_active) gb_gl_gaming_off();
    if (real_eglDestroyContext)
        return real_eglDestroyContext(dpy, ctx);
    return EGL_TRUE;
}

/* ── glXGetProcAddressARB / eglGetProcAddress interception ──────────── */
/*
 * Engines often resolve GL function pointers via glXGetProcAddressARB at startup
 * and then call them directly, bypassing the PLT.  Without this hook, those
 * engines would bypass our glTexStorage2D, glBufferStorage, etc.
 * We intercept and return our own stubs where applicable.
 */

/* Hook table: fn is stored as __GLXextFuncPtr (= void (*)(void)) which is
 * the generic function-pointer type used by both GLX and EGL proc lookups.
 * GNU C allows casting between function pointer types as an extension. */
typedef struct { const char *name; __GLXextFuncPtr fn; } GbGlHook;

static const GbGlHook g_gb_gl_hook_table[] = {
    { "glTexStorage2D",       (__GLXextFuncPtr)glTexStorage2D       },
    { "glTexStorage3D",       (__GLXextFuncPtr)glTexStorage3D       },
    { "glTextureStorage2D",   (__GLXextFuncPtr)glTextureStorage2D   },
    { "glTextureStorage3D",   (__GLXextFuncPtr)glTextureStorage3D   },
    { "glDeleteTextures",     (__GLXextFuncPtr)glDeleteTextures     },
    { "glDeleteBuffers",      (__GLXextFuncPtr)glDeleteBuffers      },
    { "glBufferStorage",      (__GLXextFuncPtr)glBufferStorage      },
    { "glNamedBufferStorage", (__GLXextFuncPtr)glNamedBufferStorage },
    { "glGetIntegerv",        (__GLXextFuncPtr)glGetIntegerv        },
    { "glGetInteger64v",      (__GLXextFuncPtr)glGetInteger64v      },
    { "glXSwapBuffers",       (__GLXextFuncPtr)glXSwapBuffers       },
    { "eglSwapBuffers",       (__GLXextFuncPtr)eglSwapBuffers       },
    { NULL, NULL }
};

static __GLXextFuncPtr gb_gl_get_hook(const char *name)
{
    if (!name) return NULL;
    for (const GbGlHook *h = g_gb_gl_hook_table; h->name; h++)
        if (strcmp(h->name, name) == 0)
            return h->fn;
    return NULL;
}

__GLXextFuncPtr glXGetProcAddressARB(const GLubyte *procname)
{
    __GLXextFuncPtr fn = real_glXGetProcAddressARB
                       ? real_glXGetProcAddressARB(procname) : NULL;
    if (g_gb_gl_active && procname) {
        __GLXextFuncPtr hook = gb_gl_get_hook((const char *)procname);
        if (hook) fn = hook;
    }
    return fn;
}

/* Many callers use the unversioned alias */
__GLXextFuncPtr glXGetProcAddress(const GLubyte *procname)
{
    return glXGetProcAddressARB(procname);
}

__eglMustCastToProperFunctionPointerType eglGetProcAddress(const char *procname)
{
    __eglMustCastToProperFunctionPointerType fn = real_eglGetProcAddress
             ? real_eglGetProcAddress(procname) : NULL;
    if (g_gb_gl_active && procname) {
        __eglMustCastToProperFunctionPointerType hook =
            (__eglMustCastToProperFunctionPointerType)gb_gl_get_hook(procname);
        if (hook) fn = hook;
    }
    return fn;
}
