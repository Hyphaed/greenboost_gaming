/// The GreenBoost Vulkan implicit layer is alpha-stage, 2026-09-02, and now
/// defaults off (GREENBOOST_VULKAN_DISABLE=1 for every game , see
/// CHANGELOG.md, 2026-09-02). Every setting that lives entirely inside
/// greenboost_vulkan_layer.c (NIS sharpen/upscale, Reflex, the persistent
/// VkPipelineCache / queue-priority / memory-priority hints, the layer's own
/// debug logging) does nothing while the layer isn't loaded, so the UI hides
/// those rows rather than offer a control with nothing behind it , see
/// Games.tsx and Live.tsx for where this gates rendering, and
/// gameOptimal.ts for where it gates the "N overrides active" / "differs
/// from recommended" counts so they don't get inflated by a field nobody
/// can see or change right now.
///
/// Single shared constant (added 2026-09-02) , Games.tsx and Live.tsx used
/// to each declare their own copy of this exact flag because neither had an
/// import path to the other's view module; a real gap this audit found was
/// gameOptimal.ts's counters not being gated at all, which a second
/// hand-copied constant would have been just as likely to miss again.
export const VULKAN_LAYER_VISIBLE = false;
