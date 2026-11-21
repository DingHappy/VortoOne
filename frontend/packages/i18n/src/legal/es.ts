import type { LegalDocument, LegalKind } from "./types";

/** Localized notices until this installation publishes its own policies. */
export const legalEs: Partial<Record<LegalKind, LegalDocument>> = {
  "terms": {
    "title": "Términos de uso",
    "summary": "Este servidor no tiene página de términos",
    "updated": "2026-11-16",
    "sections": []
  },
  "privacy": {
    "title": "Política de privacidad",
    "summary": "Este servidor no tiene página de privacidad",
    "updated": "2026-11-16",
    "sections": []
  }
};
