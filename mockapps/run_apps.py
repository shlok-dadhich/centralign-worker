"""Start portal :3001 and finance :3002 in this process (two uvicorn threads)."""
import sys
import threading
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import uvicorn

from mockapps.seed import seed_finance, seed_portal


def _run(app_path: str, port: int) -> None:
    uvicorn.run(app_path, host="127.0.0.1", port=port, log_level="info", reload=False)


def main() -> None:
    seed_portal(reset=False)
    seed_finance(reset=False)
    t1 = threading.Thread(target=_run, args=("mockapps.portal:app", 3001), daemon=True)
    t2 = threading.Thread(target=_run, args=("mockapps.finance:app", 3002), daemon=True)
    t1.start()
    t2.start()
    print("Portal  http://127.0.0.1:3001  (ap@demo.test / demo123)")
    print("Finance http://127.0.0.1:3002  (fin@demo.test / demo123)")
    print("Finance admin http://127.0.0.1:3002/admin")
    t1.join()
    t2.join()


if __name__ == "__main__":
    main()
