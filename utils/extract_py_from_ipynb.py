import json
import sys

with open(sys.argv[1], "r", encoding="utf-8") as f:
    nb = json.load(f)

for cell in nb.get("cells", []):
    if cell.get("cell_type") == "code":
        print("".join(cell.get("source", [])))
        print()
