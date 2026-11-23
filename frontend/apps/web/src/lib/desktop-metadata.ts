interface DesktopMetadata {
  platform?: unknown;
  renderer?: unknown;
  desktopSetupModelSettings?: unknown;
  system?: unknown;
  connectorReturn?: unknown;
  workspaces?: unknown;
  servers?: unknown;
  appIcon?: unknown;
  files?: unknown;
}

interface DesktopMetadataWindow {
  __vortooneDesktop?: DesktopMetadata;
  __agentesDesktop?: DesktopMetadata;
}

/** The page and the native window can come from independently updated versions. */
export function desktopMetadata(
  host: DesktopMetadataWindow | undefined = typeof window === "undefined" ? undefined : (window as DesktopMetadataWindow),
) {
  return host?.__vortooneDesktop ?? host?.__agentesDesktop;
}

/** Preview startup simulates only appearance. */
export function markDesktopPreview() {
  const host = window as DesktopMetadataWindow;
  host.__agentesDesktop = host.__vortooneDesktop = Object.freeze({ platform: "mac", renderer: "gpu" });
}
