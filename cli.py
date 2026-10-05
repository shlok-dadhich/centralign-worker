"""CLI: python cli.py "task" — live events + stdin for questions/approvals."""
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from agent.hooks import Hooks
from agent.worker import Worker


def main() -> None:
    if len(sys.argv) < 2:
        print('usage: python cli.py "task text"')
        sys.exit(2)
    goal = sys.argv[1]
    w = Worker(hooks=Hooks())
    result = w.run(goal)
    print("\n=== RESULT ===")
    print(json.dumps(result, indent=2, default=str))


if __name__ == "__main__":
    main()
