if (!import.meta.env.DEV && !__VORTO_DESKTOP_PREVIEW__)
  throw new Error(
    "Desktop UI fixtures require development or an explicit preview build.",
  );
await import("./desktop-preview-app");
