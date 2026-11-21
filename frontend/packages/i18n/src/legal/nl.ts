import type { LegalDocument, LegalKind } from "./types";

/** Localized notices until this installation publishes its own policies. */
export const legalNl: Partial<Record<LegalKind, LegalDocument>> = {
  "terms": {
    "title": "Gebruiksvoorwaarden",
    "summary": "Deze server heeft geen pagina met voorwaarden",
    "updated": "2026-11-16",
    "sections": []
  },
  "privacy": {
    "title": "Privacybeleid",
    "summary": "Deze server heeft geen privacypagina",
    "updated": "2026-11-16",
    "sections": []
  }
};
