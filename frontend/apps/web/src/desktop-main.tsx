/** The full application rendered in its desktop shell, with the upstream routes and layout intact. */
if (
  import.meta.env.DEV &&
  !(window as { __agentesDesktop?: unknown }).__agentesDesktop
) {
  // Appearance metadata only. Native capabilities are never simulated by the browser preview.
  (window as { __agentesDesktop?: unknown }).__agentesDesktop = {
    platform: "mac",
    renderer: "gpu",
  };
}
await import("./main");
