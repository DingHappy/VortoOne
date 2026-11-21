import type { LegalDocument, LegalKind } from "./types";

/** Localized notices until this installation publishes its own policies. */
export const legalFr: Partial<Record<LegalKind, LegalDocument>> = {
  "terms": {
    "title": "Conditions d'utilisation",
    "summary": "Ce serveur n'a pas de page de conditions",
    "updated": "2026-11-16",
    "sections": []
  },
  "privacy": {
    "title": "Politique de confidentialité",
    "summary": "Ce serveur n'a pas de page de confidentialité",
    "updated": "2026-11-16",
    "sections": []
  }
};
