interface DesktopMetadata {
  platform?: unknown;
  renderer?: unknown;
  desktopSetupModelSettings?: unknown;
  desktopHomeModelSettings?: unknown;
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
  __vortooneNativeUpdateCheck?: unknown;
}

/** Intercepted by the native live window, which checks the caller's origin and page. */
export const NATIVE_MODEL_SETTINGS_PATH = "/onboarding/native-model-settings";
export const NATIVE_UPDATE_CHECK_PATH = "/settings/native-check-update";

const NATIVE_UPDATE_CHECK_AVAILABILITY = "vortoone:native-update-check-availability";

/** The page and the native window can come from independently updated versions. */
export function desktopMetadata(
  host: DesktopMetadataWindow | undefined = typeof window === "undefined" ? undefined : (window as DesktopMetadataWindow),
) {
  return host?.__vortooneDesktop ?? host?.__agentesDesktop;
}

export function nativeUpdateCheckAvailable() {
  return typeof window !== "undefined" && (window as DesktopMetadataWindow).__vortooneNativeUpdateCheck === true;
}

export function subscribeNativeUpdateCheck(changed: () => void) {
  const host = window;
  host.addEventListener(NATIVE_UPDATE_CHECK_AVAILABILITY, changed);
  return () => host.removeEventListener(NATIVE_UPDATE_CHECK_AVAILABILITY, changed);
}

/** Preview startup simulates only appearance. */
export function markDesktopPreview() {
  const host = window as DesktopMetadataWindow;
  host.__agentesDesktop = host.__vortooneDesktop = Object.freeze({ platform: "mac", renderer: "gpu" });
}
