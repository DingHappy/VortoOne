if (!import.meta.env.DEV)
  throw new Error("Desktop UI fixtures are available only in development.");
await import("./desktop-preview-app");
