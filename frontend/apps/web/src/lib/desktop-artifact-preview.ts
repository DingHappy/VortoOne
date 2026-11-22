import { desktopMetadata } from "./desktop-metadata";

type DesktopPreviewHost = NonNullable<Parameters<typeof desktopMetadata>[0]> & {
  location?: Pick<Location, "protocol">;
  __TAURI_INTERNALS__?: { invoke?: unknown };
};

/** The packaged window is real, even when the same page can also run in a browser preview. */
export function hasNativeArtifactFrame(
  host: DesktopPreviewHost | undefined = typeof window === "undefined" ? undefined : (window as DesktopPreviewHost),
): boolean {
  const protocol = host?.location?.protocol;
  return (
    (protocol === "http:" || protocol === "https:") &&
    desktopMetadata(host)?.platform === "mac" &&
    typeof host?.__TAURI_INTERNALS__?.invoke === "function"
  );
}

export const DESKTOP_ARTIFACT_PREVIEW_PATH = "/artifact-preview.html";
export const DESKTOP_ARTIFACT_SANDBOX = "allow-scripts";

export function desktopArtifactPreviewStatus(event: MessageEvent, child: Window | null): "ready" | "failed" | null {
  if (!child || event.source !== child || event.origin !== "null") return null;
  const status = event.data?.vortooneArtifactPreview;
  return status === "ready" || status === "failed" ? status : null;
}
