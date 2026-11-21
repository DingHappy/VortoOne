import type { LegalDocument, LegalKind } from "./types";

/** Localized notices until this installation publishes its own policies. */
export const legalIt: Partial<Record<LegalKind, LegalDocument>> = {
  "terms": {
    "title": "Termini di utilizzo",
    "summary": "Questo server non ha una pagina dei termini",
    "updated": "2026-11-16",
    "sections": []
  },
  "privacy": {
    "title": "Informativa sulla privacy",
    "summary": "Questo server non ha una pagina sulla privacy",
    "updated": "2026-11-16",
    "sections": []
  }
};
