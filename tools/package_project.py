"""Create a source-only archive. Real comments and local secrets stay excluded."""
from pathlib import Path
from zipfile import ZipFile, ZIP_DEFLATED
import hashlib
import json


def main() -> None:
    root = Path(__file__).resolve().parents[1]
    output = root.parent / "output" / "kujiequ-research.zip"
    output.parent.mkdir(parents=True, exist_ok=True)
    sources = [root / name for name in ("README.md", "LICENSE", "requirements.txt", "pyproject.toml", "run.py", ".gitignore")]
    for folder, pattern in (("src", "*.py"), ("tests", "test_*.py"), ("docs", "*.md"), ("docs/assets", "*.svg"), ("config", "*.json"), ("examples", "*.txt"), ("tools", "*.py")):
        sources.extend((root / folder).rglob(pattern))
    public_names = {"summary.json", "topics.csv", "daily_counts.csv", "sources.csv", "index.html", "README.md"}
    if (root / "research").exists():
        for study in (root / "research").iterdir():
            if study.is_dir():
                sources.extend(file for file in study.iterdir() if file.is_file() and file.name in public_names)
    sources = sorted(set(sources))
    temporary = output.with_suffix(".zip.tmp")
    with ZipFile(temporary, "w", ZIP_DEFLATED) as archive:
        for file in sources:
            if not file.is_file() or "__pycache__" in file.parts:
                continue
            relative = file.relative_to(root)
            archive.write(file, Path(root.name) / relative)
    temporary.replace(output)
    with ZipFile(output) as archive:
        names = archive.namelist()
        assert archive.testzip() is None
        assert not any(part in {"data", "output", ".author_salt", ".env"} for name in names for part in Path(name).parts)
        count = len(names)
    print(json.dumps({"archive": str(output), "files": count, "bytes": output.stat().st_size,
                      "sha256": hashlib.sha256(output.read_bytes()).hexdigest()}, indent=2))


if __name__ == "__main__":
    main()
