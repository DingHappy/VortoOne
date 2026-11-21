import type { LegalDocument, LegalKind } from "./types";

/** Localized notices until this installation publishes its own policies. */
export const legalKo: Partial<Record<LegalKind, LegalDocument>> = {
  "terms": {
    "title": "이용약관",
    "summary": "이 서버에는 이용약관 페이지가 없어요",
    "updated": "2026-11-16",
    "sections": []
  },
  "privacy": {
    "title": "개인정보처리방침",
    "summary": "이 서버에는 개인정보 처리방침 페이지가 없어요",
    "updated": "2026-11-16",
    "sections": []
  }
};
