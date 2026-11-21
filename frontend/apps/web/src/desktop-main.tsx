/** The full application rendered in its desktop shell, with the upstream routes and layout intact. */
if (
  import.meta.env.DEV &&
  !(window as { __vortooneDesktop?: unknown }).__vortooneDesktop
) {
  // Appearance metadata only. Native capabilities are never simulated by the browser preview.
  (window as { __vortooneDesktop?: unknown }).__vortooneDesktop = {
    platform: "mac",
    renderer: "gpu",
  };
}
await import("./main");
