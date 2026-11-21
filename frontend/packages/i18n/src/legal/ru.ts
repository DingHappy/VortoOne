import type { LegalDocument, LegalKind } from "./types";

/** Localized notices until this installation publishes its own policies. */
export const legalRu: Partial<Record<LegalKind, LegalDocument>> = {
  "terms": {
    "title": "Условия использования",
    "summary": "На этом сервере нет страницы с условиями",
    "updated": "2026-11-16",
    "sections": []
  },
  "privacy": {
    "title": "Политика конфиденциальности",
    "summary": "На этом сервере нет страницы о конфиденциальности",
    "updated": "2026-11-16",
    "sections": []
  }
};
