# GreenBoost Gaming Suite , build glue
# For a full system install run:  sudo ./install.sh
# This Makefile is for developer iteration on the Vulkan layer and NIS shaders.

NIS_GLSLC := $(shell command -v glslc 2>/dev/null)
NIS_SRC   := $(CURDIR)/../NVIDIAImageScaling/NIS/NIS_Main.glsl

VULKAN_SDK_INCLUDES := $(if $(VULKAN_SDK),$(VULKAN_SDK)/include,\
  $(shell pkg-config --variable=includedir vulkan 2>/dev/null || echo /usr/include))

.PHONY: vulkan gl nis-shaders hud-shader install uninstall clean

vulkan: nis-shaders hud-shader greenboost_vulkan_layer.c nis_blobs.S hud_blobs.S greenboost_ioctl.h gb_hud_font.h
	gcc -shared -fPIC -O3 -fvisibility=hidden \
	  -I$(VULKAN_SDK_INCLUDES) \
	  -o libVkLayer_greenboost.so \
	  greenboost_vulkan_layer.c nis_blobs.S hud_blobs.S \
	  -lpthread
	@echo "[GreenBoost] Built libVkLayer_greenboost.so"

gl: greenboost_gl_layer.c greenboost_ioctl.h
	gcc -shared -fPIC -O3 \
	  -o libgb_gl.so \
	  greenboost_gl_layer.c \
	  -ldl -lpthread
	@echo "[GreenBoost] Built libgb_gl.so"

nis-shaders: build/nis_sharpen.spv build/nis_upscale.spv

build/nis_sharpen.spv: $(NIS_SRC) | build
ifeq ($(NIS_GLSLC),)
	@echo "[GreenBoost] glslc not found , install shaderc or Vulkan SDK"; exit 1
endif
	$(NIS_GLSLC) -fshader-stage=compute -O \
	  -DNIS_SCALER=0 -DNIS_HDR_MODE=0 \
	  -DNIS_BLOCK_WIDTH=32 -DNIS_BLOCK_HEIGHT=32 -DNIS_THREAD_GROUP_SIZE=256 \
	  -I$(dir $(NIS_SRC)) -o $@ $<
	@echo "[GreenBoost] Compiled $@"

build/nis_upscale.spv: $(NIS_SRC) | build
ifeq ($(NIS_GLSLC),)
	@echo "[GreenBoost] glslc not found , install shaderc or Vulkan SDK"; exit 1
endif
	$(NIS_GLSLC) -fshader-stage=compute -O \
	  -DNIS_SCALER=1 -DNIS_HDR_MODE=0 \
	  -DNIS_BLOCK_WIDTH=32 -DNIS_BLOCK_HEIGHT=24 -DNIS_THREAD_GROUP_SIZE=256 \
	  -I$(dir $(NIS_SRC)) -o $@ $<
	@echo "[GreenBoost] Compiled $@"

build:
	mkdir -p build

install:
	sudo ./install.sh

uninstall:
	sudo ./install.sh --uninstall

clean:
	rm -f libVkLayer_greenboost.so libgb_gl.so build/*.spv

# ── GreenBoost overlay ────────────────────────────────────────────────
# The font header is checked in; regenerate it with tools/gen_hud_font.py
# only when the source face or cell size changes (needs Pillow).
hud-shader: build/gb_hud.spv

build/gb_hud.spv: shaders/gb_hud.comp | build
	$(if $(NIS_GLSLC),,$(error glslc not found , install shaderc to build the overlay shader))
	$(NIS_GLSLC) -O -fshader-stage=compute $< -o $@
	@echo "[GreenBoost] Compiled overlay shader -> $@"
