/*
 * Verifies gbvk_reserved_budget() from greenboost_vulkan_layer.c.
 *
 * The function under test is EXTRACTED FROM THE REAL SOURCE at build time by
 * tests/run_vk_budget_reserve_test.sh, not reimplemented here , the same
 * discipline as tests/polkit_rule_test.js, so the test cannot quietly drift
 * from the shipped behaviour.
 */
#include <stdio.h>
#include <stdint.h>

/* Filled in by the extractor. */
static uint64_t g_gbvk_budget_reserve_div;
static uint64_t g_gbvk_budget_reserve_max;

#include "extracted_reserve.inc"

#define MB (1024ULL * 1024ULL)
#define GB (1024ULL * MB)

static int failures = 0;

static void check(const char *label, uint64_t div, uint64_t max,
                  uint64_t capacity, uint64_t want)
{
    g_gbvk_budget_reserve_div = div;
    g_gbvk_budget_reserve_max = max;
    uint64_t got = gbvk_reserved_budget(capacity);
    int ok = (got == want);
    if (!ok) failures++;
    printf("%s  %-52s cap=%6llu MB -> %6llu MB (want %llu MB)\n",
           ok ? "  ok  " : "FAIL  ", label,
           (unsigned long long)(capacity / MB),
           (unsigned long long)(got / MB),
           (unsigned long long)(want / MB));
}

int main(void)
{
    const uint64_t D = 8, M = 1 * GB;   /* shipped defaults */

    /* Real shapes: this box is 12 GB VRAM + a T2 pool. */
    check("12 GB card, no pool", D, M, 12 * GB, 12 * GB - 1 * GB);
    check("12 GB + 64 GB pool  , cap holds at 1 GiB", D, M, 76 * GB, 76 * GB - 1 * GB);
    check("24 GB card          , cap holds, not 3 GB", D, M, 24 * GB, 24 * GB - 1 * GB);

    /* Below the cap the fraction governs: 4 GB / 8 = 512 MB. */
    check("4 GB card           , fraction under cap", D, M, 4 * GB, 4 * GB - 512 * MB);
    check("8 GB card           , fraction hits cap exactly", D, M, 8 * GB, 8 * GB - 1 * GB);

    /* Never hand back nothing, and never hand back less than half.
     * The half-floor only engages when the fraction itself would take more
     * than half, i.e. DIV < 2 , with the shipped DIV=8 it is unreachable,
     * which is the point: it guards a misconfiguration, not normal operation. */
    check("DIV=1               , floor clamps to half", 1, 64 * GB, 512 * MB, 256 * MB);
    check("DIV=8 tiny pool     , floor NOT engaged", D, 64 * GB, 512 * MB, 448 * MB);
    check("zero capacity       , passthrough", D, M, 0, 0);

    /* Escape hatches restore the old gross-capacity behaviour exactly. */
    check("RESERVE_MAX_MB=0    , disabled", D, 0, 76 * GB, 76 * GB);
    check("RESERVE_DIV=0       , disabled", 0, M, 76 * GB, 76 * GB);

    /* A larger divisor reserves less. */
    check("DIV=16              , 6.25% under cap", 16, 64 * GB, 8 * GB, 8 * GB - 512 * MB);

    printf("\n%d failure(s)\n", failures);
    return failures ? 1 : 0;
}
