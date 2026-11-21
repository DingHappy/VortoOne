/// <reference types="vite/client" />
import "@tanstack/react-query";

declare module "@tanstack/react-query" {
  interface Register {
    mutationMeta: { silent?: boolean };
    /** `quiet`: refetches (polling, background) don't show the top progress bar. */
    queryMeta: { quiet?: boolean };
  }
}

declare global {
  /** Enabled only by the dedicated static preview build, never the application build. */
  const __VORTO_DESKTOP_PREVIEW__: boolean;
  interface Window {
    /** index.html's boot fallback: one reload, then a box over the boot sky. Nothing once React replaced the sky. */
    __vortooneBootFailed?: () => void;
  }
}
