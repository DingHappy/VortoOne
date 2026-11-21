import { StrictMode } from "react";
import { createRoot } from "react-dom/client";
import { I18nextProvider, initReactI18next } from "react-i18next";
import i18next from "i18next";
import { installTitleTips, trackInput } from "@vortoone/ui";
import { ThemeProvider } from "../lib/theme";
import { App } from "./App";
import "../styles.css";
import "./vortoone.css";

await i18next.use(initReactI18next).init({
  lng: "zh-Hans",
  fallbackLng: false,
  resources: {
    "zh-Hans": {
      translation: {
        common: {
          code: "代码",
          close: "关闭",
          copy: "复制",
          copied: "已复制",
          fullScreen: "全屏",
          wrapLines: "自动换行",
          raw: "原文",
        },
      },
    },
  },
  interpolation: { escapeValue: false },
});
trackInput();
installTitleTips();
createRoot(document.getElementById("root")!).render(
  <StrictMode>
    <I18nextProvider i18n={i18next}>
      <ThemeProvider>
        <App />
      </ThemeProvider>
    </I18nextProvider>
  </StrictMode>,
);
