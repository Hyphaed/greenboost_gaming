/*
 * Prints heapBudget[] / heapUsage[] for every device-local heap, with
 * VK_EXT_memory_budget explicitly chained , the exact query DXVK and
 * VKD3D-Proton make, and the one greenboost_vulkan_layer.c's inflate_budget()
 * intercepts.
 *
 * Used to verify the headroom reserve end to end against a real driver:
 *   GREENBOOST_VULKAN=1 ./vk_budget_probe                        # reserved
 *   GREENBOOST_VULKAN=1 GREENBOOST_VK_BUDGET_RESERVE_MAX_MB=0 \
 *       ./vk_budget_probe                                        # gross (old)
 *
 * Build: tests/run_vk_budget_probe.sh
 */
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <vulkan/vulkan.h>

int main(void)
{
    /* No instance extensions requested: vkGetPhysicalDeviceMemoryProperties2
     * is core in Vulkan 1.1, and asking for the KHR alias on top of that made
     * vkCreateInstance fail once the layer was in the chain. */
    VkApplicationInfo app = { .sType = VK_STRUCTURE_TYPE_APPLICATION_INFO,
                              .pApplicationName = "gb-budget-probe",
                              .apiVersion = VK_API_VERSION_1_1 };
    VkInstanceCreateInfo ici = { .sType = VK_STRUCTURE_TYPE_INSTANCE_CREATE_INFO,
                                 .pApplicationInfo = &app };
    VkInstance inst;
    if (vkCreateInstance(&ici, NULL, &inst) != VK_SUCCESS) {
        fprintf(stderr, "vkCreateInstance failed\n");
        return 2;
    }

    uint32_t n = 0;
    vkEnumeratePhysicalDevices(inst, &n, NULL);
    if (!n) { fprintf(stderr, "no physical devices\n"); return 2; }
    VkPhysicalDevice *pd = calloc(n, sizeof *pd);
    vkEnumeratePhysicalDevices(inst, &n, pd);

    PFN_vkGetPhysicalDeviceMemoryProperties2 get2 =
        (PFN_vkGetPhysicalDeviceMemoryProperties2)
        vkGetInstanceProcAddr(inst, "vkGetPhysicalDeviceMemoryProperties2");
    if (!get2) {
        get2 = (PFN_vkGetPhysicalDeviceMemoryProperties2)
            vkGetInstanceProcAddr(inst, "vkGetPhysicalDeviceMemoryProperties2KHR");
    }
    if (!get2) { fprintf(stderr, "no vkGetPhysicalDeviceMemoryProperties2\n"); return 2; }

    for (uint32_t d = 0; d < n; d++) {
        VkPhysicalDeviceProperties props;
        vkGetPhysicalDeviceProperties(pd[d], &props);

        VkPhysicalDeviceMemoryBudgetPropertiesEXT budget = {
            .sType = VK_STRUCTURE_TYPE_PHYSICAL_DEVICE_MEMORY_BUDGET_PROPERTIES_EXT };
        VkPhysicalDeviceMemoryProperties2 mp = {
            .sType = VK_STRUCTURE_TYPE_PHYSICAL_DEVICE_MEMORY_PROPERTIES_2,
            .pNext = &budget };
        get2(pd[d], &mp);

        printf("device: %s\n", props.deviceName);
        for (uint32_t h = 0; h < mp.memoryProperties.memoryHeapCount; h++) {
            int local = (mp.memoryProperties.memoryHeaps[h].flags
                         & VK_MEMORY_HEAP_DEVICE_LOCAL_BIT) != 0;
            if (!local) continue;
            printf("  heap %u (device-local): size=%8.2f GiB  budget=%8.2f GiB  usage=%8.2f GiB\n",
                   h,
                   mp.memoryProperties.memoryHeaps[h].size / 1073741824.0,
                   budget.heapBudget[h] / 1073741824.0,
                   budget.heapUsage[h]  / 1073741824.0);
        }
    }
    return 0;
}
