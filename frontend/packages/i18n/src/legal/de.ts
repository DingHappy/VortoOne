import type { LegalDocument, LegalKind } from "./types";

/** Localized notices until this installation publishes its own policies. */
export const legalDe: Partial<Record<LegalKind, LegalDocument>> = {
  "terms": {
    "title": "Nutzungsbedingungen",
    "summary": "Dieser Server hat keine Seite mit Nutzungsbedingungen",
    "updated": "2026-11-16",
    "sections": []
  },
  "privacy": {
    "title": "Datenschutzerklärung",
    "summary": "Dieser Server hat keine Datenschutzseite",
    "updated": "2026-11-16",
    "sections": []
  }
};
