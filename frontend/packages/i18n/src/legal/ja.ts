import type { LegalDocument, LegalKind } from "./types";

/** Localized notices until this installation publishes its own policies. */
export const legalJa: Partial<Record<LegalKind, LegalDocument>> = {
  "terms": {
    "title": "利用規約",
    "summary": "このサーバーには利用規約のページがありません",
    "updated": "2026-11-16",
    "sections": []
  },
  "privacy": {
    "title": "プライバシーポリシー",
    "summary": "このサーバーにはプライバシーのページがありません",
    "updated": "2026-11-16",
    "sections": []
  }
};
