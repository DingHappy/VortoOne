import type { LegalDocument, LegalKind } from "./types";

/** Localized notices until this installation publishes its own policies. */
export const legalTr: Partial<Record<LegalKind, LegalDocument>> = {
  "terms": {
    "title": "Kullanım Koşulları",
    "summary": "Bu sunucunun kullanım koşulları sayfası yok",
    "updated": "2026-11-16",
    "sections": []
  },
  "privacy": {
    "title": "Gizlilik Politikası",
    "summary": "Bu sunucunun gizlilik sayfası yok",
    "updated": "2026-11-16",
    "sections": []
  }
};
