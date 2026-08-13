import { useState } from "react";
import { convertFileSrc } from "@tauri-apps/api/core";
import { Icon } from "../icons";

// See GameHeroBanner.tsx for why this conversion is needed , local Steam
// cache paths must go through Tauri's asset protocol; CDN URLs must not.
function resolveSrc(image: string): string {
  return image.startsWith("http://") || image.startsWith("https://")
    ? image
    : convertFileSrc(image);
}

export function GameThumb({ image, name, size }: { image?: string; name: string; size: number }) {
  const [err, setErr] = useState(false);
  const cls = size <= 38 ? "game-thumb" : "game-detail-thumb";
  const fallbackCls = size <= 38 ? "game-thumb-fallback" : "game-detail-thumb-fallback";

  if (image && !err) {
    return (
      <img
        src={resolveSrc(image)}
        alt={name}
        className={cls}
        style={{ width: size, height: size }}
        onError={() => setErr(true)}
      />
    );
  }
  return (
    <div className={fallbackCls} style={{ width: size, height: size }}>
      <Icon.Gamepad />
    </div>
  );
}
