//! Best-effort local update progress. Updating never waits for the WebView.

use serde::Serialize;
use std::{
    sync::{
        atomic::{AtomicU64, Ordering},
        Arc, Mutex,
    },
    time::{Duration, Instant},
};
use tauri::{
    webview::{NewWindowResponse, PageLoadEvent},
    AppHandle, Manager, Url, WebviewUrl, WebviewWindow,
};

const PAGE: &str = "update-progress.html";
const SEND_INTERVAL: Duration = Duration::from_millis(100);
static NEXT_WINDOW: AtomicU64 = AtomicU64::new(0);

#[derive(Clone, Copy, Debug, Eq, Ord, PartialEq, PartialOrd, Serialize)]
#[serde(rename_all = "snake_case")]
pub enum Phase {
    Downloading,
    Verifying,
    Installing,
    Restarting,
}

#[derive(Serialize)]
struct Snapshot {
    version: String,
    phase: Phase,
    received: u64,
    total: Option<u64>,
}

impl Snapshot {
    fn new(version: &str) -> Self {
        Self {
            version: version.into(),
            phase: Phase::Downloading,
            received: 0,
            total: None,
        }
    }

    fn receive(&mut self, received: u64, total: Option<u64>) {
        self.received = self.received.max(received);
        self.total = total
            .filter(|&total| total > 0)
            .or(self.total)
            .filter(|&total| total >= self.received);
    }

    fn script(&self) -> Option<String> {
        let payload = serde_json::to_string(self).ok()?;
        Some(format!(
            "window.__vortooneUpdateProgress={payload};window.dispatchEvent(new Event('vortoone-update-progress'));"
        ))
    }
}

struct State {
    snapshot: Snapshot,
    ready: bool,
    last_download: Option<Instant>,
}

struct LocalWindow {
    app: AppHandle,
    label: String,
    state: Mutex<State>,
}

fn progress_url(url: &Url) -> bool {
    crate::packaged_url(url)
        && url.path().strip_prefix('/') == Some(PAGE)
        && url.query().is_none()
        && url.fragment().is_none()
        && url.username().is_empty()
        && url.password().is_none()
        && url.port().is_none()
}

impl LocalWindow {
    fn send(&self, script: Option<String>) {
        if let Some(script) = script {
            if let Some(window) = self.app.get_webview_window(&self.label) {
                let _ = window.eval(script);
            }
        }
    }

    fn page_load(&self, window: &WebviewWindow, url: &Url, event: PageLoadEvent) {
        if !progress_url(url) {
            return;
        }
        let ready = event == PageLoadEvent::Finished;
        let script = self.state.lock().ok().and_then(|mut state| {
            state.ready = ready;
            if ready {
                state.snapshot.script()
            } else {
                None
            }
        });
        if let Some(script) = script {
            let _ = window.eval(script);
            let _ = window.show();
        }
    }
}

impl Drop for LocalWindow {
    fn drop(&mut self) {
        // Each update owns a distinct native window label.
        let app = self.app.clone();
        let label = self.label.clone();
        tauri::async_runtime::spawn_blocking(move || {
            if let Some(window) = app.get_webview_window(&label) {
                let _ = window.destroy();
            }
        });
    }
}

#[derive(Clone)]
pub struct UpdateProgress(Arc<LocalWindow>);

impl UpdateProgress {
    pub fn open(app: &AppHandle, version: &str) -> Self {
        let snapshot = Snapshot::new(version);
        let initial_script = snapshot.script().unwrap_or_default();
        let sequence = NEXT_WINDOW.fetch_add(1, Ordering::Relaxed);
        let label = format!("update-progress-{sequence}");
        let view = Arc::new(LocalWindow {
            app: app.clone(),
            label: label.clone(),
            state: Mutex::new(State {
                snapshot,
                ready: false,
                last_download: None,
            }),
        });
        let pending = Arc::downgrade(&view);
        let app = app.clone();
        tauri::async_runtime::spawn_blocking(move || {
            if pending.strong_count() == 0 {
                return;
            }
            let page = pending.clone();
            let window = crate::desktop_window(&app, &label, WebviewUrl::App(PAGE.into()))
                .title("VortoOne 更新")
                .inner_size(480.0, 282.0)
                .min_inner_size(350.0, 245.0)
                .center()
                .visible(false)
                .closable(false)
                .initialization_script(initial_script)
                .on_navigation(|url| url.as_str() == "about:blank" || progress_url(url))
                .on_new_window(|_, _| NewWindowResponse::Deny)
                .on_page_load(move |window, payload| {
                    if let Some(view) = page.upgrade() {
                        view.page_load(&window, payload.url(), payload.event());
                    }
                })
                .build();
            // The update can finish while native creation is still queued.
            if pending.strong_count() == 0 {
                if let Ok(window) = window {
                    let _ = window.destroy();
                }
            }
        });
        Self(view)
    }

    pub fn downloaded(&self, received: u64, total: Option<u64>) {
        let script = self.0.state.lock().ok().and_then(|mut state| {
            if state.snapshot.phase != Phase::Downloading {
                return None;
            }
            state.snapshot.receive(received, total);
            let now = Instant::now();
            if !state.ready
                || state
                    .last_download
                    .is_some_and(|last| now.duration_since(last) < SEND_INTERVAL)
            {
                return None;
            }
            state.last_download = Some(now);
            state.snapshot.script()
        });
        self.0.send(script);
    }

    pub fn show_phase(&self, phase: Phase) {
        let script = self.0.state.lock().ok().and_then(|mut state| {
            if phase <= state.snapshot.phase {
                return None;
            }
            state.snapshot.phase = phase;
            if state.ready {
                state.snapshot.script()
            } else {
                None
            }
        });
        self.0.send(script);
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn updater_progress_only_navigates_to_the_exact_packaged_page() {
        for good in [
            "tauri://localhost/update-progress.html",
            "http://tauri.localhost/update-progress.html",
        ] {
            assert!(progress_url(&good.parse().unwrap()), "{good}");
        }
        for bad in [
            "about:blank",
            "https://vortoone.com/update-progress.html",
            "http://tauri.localhost/update-progress.html?other",
            "tauri://localhost/update-progress.html#other",
            "http://tauri.localhost:8080/update-progress.html",
            "http://tauri.localhost/versions.html",
        ] {
            assert!(!progress_url(&bad.parse().unwrap()), "{bad}");
        }
    }

    #[test]
    fn updater_progress_preserves_known_totals_and_rejects_impossible_lengths() {
        let mut snapshot = Snapshot::new("1.2.3");
        snapshot.receive(40, Some(100));
        snapshot.receive(35, None);
        assert_eq!((snapshot.received, snapshot.total), (40, Some(100)));
        snapshot.receive(50, Some(0));
        assert_eq!((snapshot.received, snapshot.total), (50, Some(100)));
        snapshot.receive(120, None);
        assert_eq!((snapshot.received, snapshot.total), (120, None));
        let payload = serde_json::to_value(snapshot).unwrap();
        assert_eq!(
            payload,
            serde_json::json!({
                "version": "1.2.3", "phase": "downloading", "received": 120, "total": null,
            })
        );
    }
}
