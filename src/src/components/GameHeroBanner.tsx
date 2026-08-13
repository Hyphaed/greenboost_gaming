import { useState } from "react";
import { convertFileSrc } from "@tauri-apps/api/core";
import { Icon } from "../icons";

// Local filesystem paths (Steam's library cache) need Tauri's asset
// protocol conversion before a webview <img> will load them , a raw
// "/home/..." path is not a URL any browser engine accepts. CDN fallback
// URLs (https://...) must pass through unchanged; convertFileSrc mangles
// non-local-path input.
function resolveSrc(image: string): string {
  return image.startsWith("http://") || image.startsWith("https://")
    ? image
    : convertFileSrc(image);
}

export function GameHeroBanner({ image, name }: { image?: string; name: string }) {
  const [err, setErr] = useState(false);
  if (image && !err) {
    return <img src={resolveSrc(image)} alt={name} className="game-hero-img" onError={() => setErr(true)} />;
  }
  return (
    <div className="game-hero-fallback">
      <Icon.Gamepad />
    </div>
  );
}
