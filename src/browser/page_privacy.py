"""DOM projection for the managed browser. Never return form values.

Recognized login/secret forms pause projection before title,
body, URL or labels are read. This is a conservative DOM guard, not a promise to
detect every QR login or arbitrary secret rendered as ordinary page text.
"""

import re
from urllib.parse import unquote

_PROTECTED_PATH = re.compile(
    r"(?:^|/)(?:login|signin|sign-in|oauth|sso|mfa|2fa|two-factor|password|passwd|"
    r"credentials|secrets|tokens?|api[_-]?keys?|security|billing|checkout|payment)"
    r"(?:\.(?:php|html?|aspx?))?(?:/|$)"
)


def protected_path(path: str) -> bool:
    return bool(_PROTECTED_PATH.search(unquote(path).lower()))


_HELPERS = r"""
const visible = el => { const r = el.getBoundingClientRect(), s = getComputedStyle(el);
  return r.width > 0 && r.height > 0 && s.visibility !== 'hidden' && s.display !== 'none'; };
const sensitive = el => {
  const type = (el.getAttribute('type') || '').toLowerCase();
  const autocomplete = (el.getAttribute('autocomplete') || '').toLowerCase();
  const name = ((el.getAttribute('name') || '') + ' ' + (el.getAttribute('id') || '')).toLowerCase();
  return type === 'password' || /(?:current-password|new-password|one-time-code)/.test(autocomplete)
    || /(?:password|passwd|passcode|(?:^|[\s_-])otp(?:$|[\s_-])|verification.?code|security.?code|api.?key|access.?token|refresh.?token|client.?secret)/.test(name);
};
const protectedPage = () => [...document.querySelectorAll('input,textarea')].some(el => visible(el) && sensitive(el));
const safeUrl = () => { try { const u = new URL(location.href); return u.origin + '/'; } catch { return ''; } };
const label = el => (el.getAttribute('aria-label') || el.innerText || el.getAttribute('placeholder')
  || el.getAttribute('title') || el.getAttribute('alt') || '').trim().replace(/\s+/g, ' ').slice(0,80);
"""

AUTH_STATE_JS = "(() => {" + _HELPERS + "return { protected: protectedPage(), frames: [...document.querySelectorAll('iframe,frame')].some(visible) }; })()"

_HANDOFF_STATE = r"""
const state = () => ({ url: location.href, document: performance.timeOrigin, protected: protectedPage(),
  frames: [...document.querySelectorAll('iframe,frame')].some(visible), width: innerWidth, height: innerHeight,
  loaded: document.readyState === 'complete', scrollX, scrollY });
"""

HANDOFF_PREVIEW_STATE_JS = "(() => {" + _HELPERS + _HANDOFF_STATE + "return state();})()"

HANDOFF_CONTROL_STATE_JS = "((kind, nx, ny) => {" + _HELPERS + _HANDOFF_STATE + r"""
const page = state();
const denied = { ...page, allowed: false };
if (page.protected || page.frames) return denied;
if (kind === 'scroll') return { ...page, allowed: true };
if (kind === 'text' || kind === 'backspace') {
  const el = document.activeElement;
  const field = el instanceof HTMLTextAreaElement ||
    (el instanceof HTMLInputElement && ['text', 'search', 'email', 'url', 'tel'].includes(el.type));
  return { ...page, allowed: field && visible(el) && !el.disabled && !el.readOnly && !sensitive(el) };
}
const x = nx * innerWidth / 10000, y = ny * innerHeight / 10000;
const hit = document.elementFromPoint(x, y);
if (!hit || hit.shadowRoot) return denied;
const fieldLabel = hit.closest('label');
const field = hit.closest('input,textarea,select,button') || fieldLabel?.control;
if (field && (field.disabled || field.type === 'file' || sensitive(field))) return denied;
const link = hit.closest('a,area');
if (link?.hasAttribute('href')) {
  const url = new URL(link.href, location.href);
  if (url.origin !== location.origin || url.search || url.hash || url.username || url.password ||
      link.hasAttribute('download') || (link.target && link.target !== '_self')) return denied;
}
return { ...page, allowed: true, x, y };
})"""

SNAPSHOT_JS = "(() => {" + _HELPERS + r"""
if (protectedPage()) return { needs_login: true };
const items = []; let n = 0;
for (const el of document.querySelectorAll('a[href],button,input:not([type=hidden]),textarea,select,[role=button],[role=link],[contenteditable=true]')) {
  if (!visible(el) || n >= 150) continue;
  n += 1; el.setAttribute('data-vorto-ref', String(n));
  const tag = el.tagName.toLowerCase(), type = el.getAttribute('type') || '';
  items.push(`[${n}] <${tag}${type ? ' type=' + type : ''}> ${label(el)}`);
}
const text = (document.body ? document.body.innerText : '').replace(/\n{3,}/g,'\n\n').slice(0,6000);
return { title: document.title, url: safeUrl(), text, items };
})()"""

ELEMENT_JS = "((ref) => {" + _HELPERS + r"""
if (protectedPage()) return { sensitive: true };
const el = document.querySelector(`[data-vorto-ref="${ref}"]`);
if (!el) return null;
if (sensitive(el)) return { sensitive: true };
el.scrollIntoView({block:'center',inline:'center'});
const r=el.getBoundingClientRect();
return { x:r.left+r.width/2, y:r.top+r.height/2, tag:el.tagName.toLowerCase(),
  type:(el.getAttribute('type')||'').toLowerCase(), label:label(el),
  editable:el.isContentEditable || ['input','textarea'].includes(el.tagName.toLowerCase()),
  inForm:!!el.closest('form') };
})(%s)"""
