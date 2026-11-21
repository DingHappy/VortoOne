import type { LegalDocument, LegalKind } from "./types";

/** Localized notices until this installation publishes its own policies. */
export const legalPl: Partial<Record<LegalKind, LegalDocument>> = {
  "terms": {
    "title": "Warunki korzystania",
    "summary": "Ten serwer nie ma strony z regulaminem",
    "updated": "2026-11-16",
    "sections": []
  },
  "privacy": {
    "title": "Polityka prywatności",
    "summary": "Ten serwer nie ma strony z polityką prywatności",
    "updated": "2026-11-16",
    "sections": []
  }
};
