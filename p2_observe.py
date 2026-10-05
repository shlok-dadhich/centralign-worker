"""P2 gate: log into both mock apps using observe()+click/type, print observations."""
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from agent.browser import Browser


def by_label(obs: dict, *needles: str) -> int:
    nset = [n.lower() for n in needles]
    for el in obs.get("elements") or []:
        blob = f"{el.get('label','')} {el.get('role','')} {el.get('tag','')}".lower()
        if all(n in blob for n in nset):
            return int(el["id"])
    for el in obs.get("elements") or []:
        blob = f"{el.get('label','')}".lower()
        if any(n in blob for n in nset):
            return int(el["id"])
    raise RuntimeError(f"no element matching {needles} in {obs.get('elements')}")


def login(b: Browser, url: str, email: str, password: str) -> dict:
    r = b.open_url(url)
    obs = r["observation"]
    print("opened", obs.get("url"), "title", obs.get("title"))
    b.type_text(by_label(obs, "email"), email)
    obs = b.observe()
    b.type_text(by_label(obs, "password"), password)
    obs = b.observe()
    b.click(by_label(obs, "log in"))
    return b.observe()


def main() -> None:
    run_dir = Path(__file__).resolve().parent / "runs" / "p2"
    run_dir.mkdir(parents=True, exist_ok=True)
    b = Browser(run_dir)
    b.start()
    try:
        p = login(b, "http://127.0.0.1:3001/login", "ap@demo.test", "demo123")
        print("PORTAL observe():")
        print(json.dumps({k: p[k] for k in ("url", "title", "visible_text", "error_banner", "elements") if k in p}, indent=2))
        f = login(b, "http://127.0.0.1:3002/login", "fin@demo.test", "demo123")
        print("FINANCE observe():")
        print(json.dumps({k: f[k] for k in ("url", "title", "visible_text", "error_banner", "elements") if k in f}, indent=2))
        print("screenshots in", run_dir / "screenshots")
        print("P2 GATE: inspect printed observe() JSON and a screenshot file.")
    finally:
        b.stop()


if __name__ == "__main__":
    main()
