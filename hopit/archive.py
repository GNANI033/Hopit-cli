import os
import sys
import tarfile
import zipfile
from pathlib import Path

def create_archive(out_name: str, target_path: str):
    target = Path(target_path).resolve()
    if not target.exists():
        print(f"Error: Target path '{target_path}' does not exist.")
        sys.exit(1)
        
    out_path = Path(out_name).resolve()
    print(f"Creating archive: {out_path.name} from '{target_path}'...")
    
    if out_name.endswith(".zip"):
        with zipfile.ZipFile(out_path, "w", zipfile.ZIP_DEFLATED) as zf:
            if target.is_file():
                zf.write(target, target.name)
            else:
                for root, _, files in os.walk(target):
                    for file in files:
                        full_p = Path(root) / file
                        arcname = full_p.relative_to(target.parent)
                        zf.write(full_p, arcname)
        print(f"Successfully created zip archive: {out_path}")
    elif any(out_name.endswith(ext) for ext in (".tar.gz", ".tgz", ".tar")):
        mode = "w:gz" if out_name.endswith((".tar.gz", ".tgz")) else "w"
        with tarfile.open(out_path, mode) as tf:
            tf.add(target, arcname=target.name)
        print(f"Successfully created tar archive: {out_path}")
    else:
        # Default to zip if no standard extension matched
        if not out_name.endswith(".zip"):
            out_path = Path(str(out_path) + ".zip")
        with zipfile.ZipFile(out_path, "w", zipfile.ZIP_DEFLATED) as zf:
            if target.is_file():
                zf.write(target, target.name)
            else:
                for root, _, files in os.walk(target):
                    for file in files:
                        full_p = Path(root) / file
                        arcname = full_p.relative_to(target.parent)
                        zf.write(full_p, arcname)
        print(f"Successfully created archive: {out_path}")


def extract_archive(archive_name: str, dest_dir: str = "."):
    arch_path = Path(archive_name).resolve()
    if not arch_path.exists():
        print(f"Error: Archive '{archive_name}' does not exist.")
        sys.exit(1)
        
    dest_path = Path(dest_dir).resolve()
    dest_path_resolved = os.path.abspath(dest_path)
    dest_path.mkdir(parents=True, exist_ok=True)
    
    print(f"Extracting '{arch_path.name}' to '{dest_path_resolved}'...")
    
    if zipfile.is_zipfile(arch_path):
        with zipfile.ZipFile(arch_path, "r") as zf:
            for member in zf.namelist():
                target_path = os.path.abspath(os.path.join(dest_path_resolved, member))
                if os.path.commonpath([dest_path_resolved, target_path]) != dest_path_resolved:
                    print(f"Error: Path traversal detected in zip archive for member: {member}")
                    sys.exit(1)
            zf.extractall(dest_path_resolved)
        print("Extraction complete.")
    elif tarfile.is_tarfile(arch_path):
        with tarfile.open(arch_path, "r:*") as tf:
            for member in tf.getmembers():
                target_path = os.path.abspath(os.path.join(dest_path_resolved, member.name))
                if os.path.commonpath([dest_path_resolved, target_path]) != dest_path_resolved:
                    print(f"Error: Path traversal detected in tar archive for member: {member.name}")
                    sys.exit(1)
            tf.extractall(dest_path_resolved)
        print("Extraction complete.")
    else:
        print(f"Error: Format of '{archive_name}' is not supported (supported: .zip, .tar, .tar.gz, .tgz).")
        sys.exit(1)


from hopit.config import safe_entrypoint

@safe_entrypoint
def main():
    if len(sys.argv) < 2:
        print("Usage:")
        print("  python -m hopit.archive create <output.zip> <folder_or_file>")
        print("  python -m hopit.archive extract <archive_file> [destination_folder]")
        sys.exit(1)
        
    sub = sys.argv[1].lower()
    if sub == "create" and len(sys.argv) >= 4:
        create_archive(sys.argv[2], sys.argv[3])
    elif sub == "extract" and len(sys.argv) >= 3:
        dest = sys.argv[3] if len(sys.argv) >= 4 else "."
        extract_archive(sys.argv[2], dest)
    else:
        print("Invalid parameters for archive command.")
        sys.exit(1)


if __name__ == "__main__":
    main()
