/** The full application rendered in its desktop shell, with the upstream routes and layout intact. */
import { desktopMetadata, markDesktopPreview } from "./lib/desktop-metadata";

if (import.meta.env.DEV && !desktopMetadata()) markDesktopPreview();
await import("./main");
