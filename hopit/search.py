import os
import sys
import re
from pathlib import Path

def search_text_or_file(query: str, search_path: str = "."):
    target_dir = Path(search_path).resolve()
    if not target_dir.exists():
        print(f"Error: Search directory '{search_path}' does not exist.")
        sys.exit(1)

    try:
        pattern = re.compile(query, re.IGNORECASE)
    except re.error as e:
        print(f"Error: Invalid regular expression pattern '{query}': {e}")
        sys.exit(1)

    print(f"Searching for '{query}' in '{target_dir}'...\n")
    
    matches_found = 0
    max_matches = 100
    
    for root, _, files in os.walk(target_dir):
        if ".git" in root or "__pycache__" in root or ".venv" in root:
            continue
            
        for file in files:
            full_path = Path(root) / file
            rel_path = full_path.relative_to(target_dir)
            
            # Check filename match
            if pattern.search(file):
                print(f"\033[1;32m[Filename Match]\033[0m {rel_path}")
                matches_found += 1
                if matches_found >= max_matches:
                    break
            
            # Check content match for text files
            try:
                with open(full_path, "r", encoding="utf-8", errors="ignore") as f:
                    for line_no, line in enumerate(f, 1):
                        if pattern.search(line):
                            snippet = line.strip()
                            if len(snippet) > 120:
                                snippet = snippet[:117] + "..."
                            print(f"\033[1;36m{rel_path}\033[0m:\033[33m{line_no}\033[0m: {snippet}")
                            matches_found += 1
                            if matches_found >= max_matches:
                                break
            except Exception:
                continue

            if matches_found >= max_matches:
                print("\n(Reached 100 matches cap, stopping search)")
                return

    if matches_found == 0:
        print("No matching text or files found.")


def main():
    if len(sys.argv) < 2:
        print("Usage: python -m hopit.search <search_term> [directory]")
        sys.exit(1)
        
    query = sys.argv[1]
    search_path = sys.argv[2] if len(sys.argv) >= 3 else "."
    search_text_or_file(query, search_path)


if __name__ == "__main__":
    main()
