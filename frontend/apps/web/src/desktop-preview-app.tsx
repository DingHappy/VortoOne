/** Uses the actual upstream application, not a recreation. All data stays in isolated in-memory fixtures. */
import {
  createHashHistory,
  createRouter,
  RouterProvider,
} from "@tanstack/react-router";
import { StrictMode } from "react";
import { createRoot } from "react-dom/client";
import { installTitleTips, switchTheme } from "@vortoone/ui";
import { createFakeApi, ORG_ID, seedState } from "../demo/fake-api";
import { createFakeAuth } from "../demo/fake-auth";
import { FakeSocket } from "../demo/fake-socket";
import { AppProviders, type AppServices } from "./app/providers";
import { createQueryClient } from "./app/query-client";
import { loadI18n, rememberLocale } from "./i18n";
import { applyDesktopDevice } from "./lib/desktop";
import { markDesktopPreview } from "./lib/desktop-metadata";
import { createToastStore } from "./lib/toast";
import { routeTree } from "./routeTree.gen";
import "./styles.css";

markDesktopPreview();
document.documentElement.dataset.desktop = "mac";
document.documentElement.dataset.preview = "isolated-desktop-ui";
const params = new URLSearchParams(window.location.search);
const locale = params.get("lang") === "zh-Hans" ? "zh-Hans" : "en";
rememberLocale(locale, false);
const i18n = await loadI18n(locale);
document.documentElement.lang = locale;
applyDesktopDevice(i18n, "mac");
switchTheme(params.get("theme") === "dark" ? "dark" : "light");
installTitleTips();
const state = seedState();
if (state.me) state.me = { ...state.me, user: { ...state.me.user, locale } };
const api = createFakeApi(state);
const toasts = createToastStore();
const queryClient = createQueryClient({
  onUnauthorized: () => {},
  onMutationError: (message) => toasts.show(message, "error"),
  describeError: (error) =>
    error instanceof Error ? error.message : String(error),
});
const services: AppServices = {
  api,
  auth: createFakeAuth().auth,
  queryClient,
  i18n,
  toasts,
  createSocket: (url) => {
    const socket = new FakeSocket(url);
    queueMicrotask(() => {
      socket.open();
      socket.push({ type: "ready", orgId: ORG_ID, userId: state.me?.user.id });
    });
    return socket;
  },
};
if (!window.location.hash)
  window.history.replaceState(
    null,
    "",
    `${window.location.pathname}${window.location.search}#/org/${ORG_ID}/home`,
  );
const router = createRouter({
  routeTree,
  history: createHashHistory(),
  context: { api, queryClient },
});
createRoot(document.getElementById("root")!).render(
  <StrictMode>
    <AppProviders services={services}>
      <RouterProvider router={router} />
    </AppProviders>
  </StrictMode>,
);
