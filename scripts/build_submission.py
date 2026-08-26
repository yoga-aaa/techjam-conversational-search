from __future__ import annotations

import argparse
import shutil
import tempfile
import zipfile
from pathlib import Path


def main() -> None:
    parser = argparse.ArgumentParser(description="Build a clean participant submission archive")
    parser.add_argument("--output", default="artifacts/submission.zip")
    args = parser.parse_args()

    root = Path(__file__).resolve().parents[1]
    output = Path(args.output).resolve()
    output.parent.mkdir(parents=True, exist_ok=True)

    with tempfile.TemporaryDirectory() as directory:
        bundle = Path(directory) / "submission"
        bundle.mkdir()
        shutil.copy2(root / "starter" / "agent.py", bundle / "agent.py")
        shutil.copytree(root / "shopping_copilot", bundle / "shopping_copilot", ignore=shutil.ignore_patterns("__pycache__", "*.pyc"))
        shutil.copytree(root / "configs", bundle / "configs")
        shutil.copy2(root / "requirements.txt", bundle / "requirements.txt")
        shutil.copy2(root / "README.md", bundle / "README.md")

        with zipfile.ZipFile(output, "w", compression=zipfile.ZIP_DEFLATED) as archive:
            for path in bundle.rglob("*"):
                if path.is_file():
                    archive.write(path, path.relative_to(bundle.parent))

    print(f"Built {output}")


if __name__ == "__main__":
    main()
