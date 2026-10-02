"""Check documentation links and accidental private paths or bearer credentials."""
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
MARKDOWN = [ROOT / "README.md", *sorted((ROOT / "docs").rglob("*.md"))]
FORBIDDEN = [r"C:\Users", "AppData", "Bearer "]
issues = []
for path in MARKDOWN:
    content = path.read_text(encoding="utf-8")
    for token in FORBIDDEN:
        if token.lower() in content.lower():
            issues.append(f"{path.relative_to(ROOT)} contains forbidden text: {token}")
    for target in re.findall(r"\]\(([^)]+)\)", content):
        if re.match(r"^[a-z]+://", target) or target.startswith("#"):
            continue
        relative = target.split("#", 1)[0]
        if not (path.parent / relative).exists():
            issues.append(f"{path.relative_to(ROOT)} has missing link: {target}")

if issues:
    raise SystemExit("\n".join(issues))
print(f"Checked {len(MARKDOWN)} Markdown files, links, and private-data markers")
