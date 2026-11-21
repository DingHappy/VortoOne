import type { LegalDocument, LegalKind } from "./types";

/** Localized notices until this installation publishes its own policies. */
export const legalZhHans: Partial<Record<LegalKind, LegalDocument>> = {
  "terms": {
    "title": "使用条款",
    "summary": "此服务器没有服务条款页面",
    "updated": "2026-11-16",
    "sections": []
  },
  "privacy": {
    "title": "隐私政策",
    "summary": "此服务器没有隐私政策页面",
    "updated": "2026-11-16",
    "sections": []
  }
};
