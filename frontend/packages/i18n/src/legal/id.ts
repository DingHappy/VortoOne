import type { LegalDocument, LegalKind } from "./types";

/** Localized notices until this installation publishes its own policies. */
export const legalId: Partial<Record<LegalKind, LegalDocument>> = {
  "terms": {
    "title": "Ketentuan Penggunaan",
    "summary": "Server ini tidak punya halaman ketentuan",
    "updated": "2026-11-16",
    "sections": []
  },
  "privacy": {
    "title": "Kebijakan Privasi",
    "summary": "Server ini tidak punya halaman privasi",
    "updated": "2026-11-16",
    "sections": []
  }
};
