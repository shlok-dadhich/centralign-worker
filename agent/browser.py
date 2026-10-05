"""Playwright sync browser. Catch every Playwright error and return it as a failure dict."""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Optional

from playwright.sync_api import Error as PlaywrightError
from playwright.sync_api import TimeoutError as PlaywrightTimeout
from playwright.sync_api import sync_playwright

# WHY inject ids each observe: LLM must not use CSS selectors; ids expire after any nav/click.
INJECT_IDS = """
() => {
  const sel = 'a, button, input, textarea, select';
  const nodes = Array.from(document.querySelectorAll(sel));
  nodes.forEach((el, i) => el.setAttribute('data-ca-id', String(i)));
  const banner = document.querySelector('.error-banner, [role="alert"]');
  const elements = nodes.map((el, i) => {
    const tag = el.tagName.toLowerCase();
    const role = el.getAttribute('role') || el.getAttribute('type') || tag;
    let label = '';
    const id = el.getAttribute('id');
    if (id) {
      const lab = document.querySelector(`label[for="${id}"]`);
      if (lab) label = lab.innerText.trim();
    }
    if (!label) {
      const parent = el.closest('label');
      if (parent) label = parent.innerText.trim();
    }
    if (!label) label = (el.getAttribute('aria-label') || el.getAttribute('placeholder') || el.getAttribute('name') || el.innerText || el.value || '').trim();
    if (label.length > 80) label = label.slice(0, 80);
    return {
      id: i,
      tag,
      role,
      label,
      value: (el.value !== undefined && el.value !== null) ? String(el.value).slice(0, 80) : ''
    };
  });
  const text = (document.body && document.body.innerText) ? document.body.innerText : '';
  return {
    url: location.href,
    title: document.title,
    visible_text: text.slice(0, 1500),
    error_banner: banner ? banner.innerText.trim() : '',
    elements
  };
}
"""


class Browser:
    def __init__(self, run_dir: Path):
        self.run_dir = Path(run_dir)
        self.shots = self.run_dir / "screenshots"
        self.shots.mkdir(parents=True, exist_ok=True)
        self._pw = None
        self._browser = None
        self._context = None
        self.page = None
        self._n = 0

    def start(self) -> None:
        self._pw = sync_playwright().start()
        profile = self.run_dir / "profile"
        profile.mkdir(parents=True, exist_ok=True)
        # Persistent context keeps login cookies for the duration of one task.
        self._context = self._pw.chromium.launch_persistent_context(
            str(profile),
            headless=False,
            viewport={"width": 1100, "height": 800},
        )
        self._context.tracing.start(screenshots=True, snapshots=True)
        self.page = self._context.pages[0] if self._context.pages else self._context.new_page()

    def stop(self) -> Optional[str]:
        trace_path = str(self.run_dir / "trace.zip")
        try:
            if self._context:
                self._context.tracing.stop(path=trace_path)
                self._context.close()
        except Exception:
            trace_path = None
        if self._pw:
            self._pw.stop()
        return trace_path

    def _shot(self) -> Optional[str]:
        self._n += 1
        path = self.shots / f"{self._n:03d}.png"
        try:
            self.page.screenshot(path=str(path), full_page=True)
            return str(path)
        except Exception:
            return None

    def observe(self) -> dict:
        data = self.page.evaluate(INJECT_IDS)
        data["screenshot"] = self._shot()
        return data

    def _ok(self, extra: Optional[dict] = None) -> dict:
        obs = self.observe()
        out = {"ok": True, "error": "", "observation": obs}
        if extra:
            out.update(extra)
        return out

    def _fail(self, error: str) -> dict:
        obs: dict[str, Any] = {}
        try:
            obs = self.observe()
        except Exception:
            obs = {"url": "", "title": "", "visible_text": "", "error_banner": error, "elements": []}
        return {"ok": False, "error": error, "observation": obs}

    def open_url(self, url: str) -> dict:
        try:
            self.page.goto(url, wait_until="domcontentloaded", timeout=15000)
            return self._ok()
        except (PlaywrightTimeout, PlaywrightError) as e:
            return self._fail(str(e))

    def click(self, element_id: int) -> dict:
        try:
            loc = self.page.locator(f'[data-ca-id="{int(element_id)}"]')
            if loc.count() == 0:
                return self._fail(f"element not found: stale id {element_id}")
            loc.first.click(timeout=8000)
            self.page.wait_for_timeout(200)
            return self._ok()
        except (PlaywrightTimeout, PlaywrightError) as e:
            msg = str(e)
            if "Timeout" in msg:
                return self._fail("timeout: " + msg)
            return self._fail(msg)

    def click_by_label(self, label: str, tag: Optional[str] = None, role: Optional[str] = None) -> dict:
        """Recover from a stale id after the page re-rendered: re-observe, find the control whose
        label matches the one the model already saw, and click it. Generic, no CSS selectors.

        When several controls share a label, disambiguate by the original control's tag/role (passed
        in when known) so a re-render picks the same kind of control, not an arbitrary first match.
        """
        try:
            obs = self.observe()
            want = (label or "").strip().lower()
            want_tag = (tag or "").strip().lower()
            want_role = (role or "").strip().lower()

            def matches(el: dict, exact: bool) -> bool:
                blob = (el.get("label") or "").strip().lower()
                hit = blob == want if exact else (want in blob or blob in want)
                return bool(hit and want)

            def same_kind(el: dict) -> bool:
                if want_tag and (el.get("tag") or "").lower() != want_tag:
                    return False
                if want_role and (el.get("role") or "").lower() != want_role:
                    return False
                return True

            elements = obs.get("elements") or []
            target = None
            # 1) exact label + same tag/role  2) exact label  3) partial label + same tag/role  4) partial
            for exact, kind in ((True, True), (True, False), (False, True), (False, False)):
                for el in elements:
                    if matches(el, exact) and (same_kind(el) if kind else True):
                        target = el
                        break
                if target is not None:
                    break
            if target is None:
                return {"ok": False, "error": f"no element matching label {label!r} after retry", "observation": obs}
            return self.click(int(target["id"]))
        except (PlaywrightTimeout, PlaywrightError) as e:
            return self._fail(f"retry click failed: {e}")

    def type_text(self, element_id: int, text: str) -> dict:
        try:
            loc = self.page.locator(f'[data-ca-id="{int(element_id)}"]')
            if loc.count() == 0:
                return self._fail(f"element not found: stale id {element_id}")
            loc.first.fill("")
            loc.first.fill(str(text))
            return self._ok()
        except (PlaywrightTimeout, PlaywrightError) as e:
            msg = str(e)
            if "Timeout" in msg:
                return self._fail("timeout: " + msg)
            return self._fail(msg)


def compact_observation(obs: dict) -> dict:
    """Drop screenshot path from the blob sent to the LLM (file path is not useful to the model)."""
    if not obs:
        return {}
    out = {k: v for k, v in obs.items() if k != "screenshot"}
    return json.loads(json.dumps(out))
