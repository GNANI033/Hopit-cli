import os
import sys
import time
import urllib.parse
import urllib.request
from pathlib import Path

def download_file(url: str, dest: str = None):
    if not url.startswith(("http://", "https://", "ftp://")):
        url = "https://" + url

    parsed = urllib.parse.urlparse(url)
    default_name = os.path.basename(parsed.path) or "downloaded_file"
    
    if not dest:
        dest_path = Path(default_name)
    else:
        dest_p = Path(dest)
        if dest_p.is_dir():
            dest_path = dest_p / default_name
        else:
            dest_path = dest_p

    print(f"Downloading from: {url}")
    print(f"Destination:     {dest_path.resolve()}")
    
    start_time = time.time()
    
    def progress_callback(blocks_transferred, block_size, total_size):
        downloaded = blocks_transferred * block_size
        elapsed = time.time() - start_time
        speed = downloaded / elapsed if elapsed > 0 else 0
        
        if total_size > 0:
            percent = min(100, int((downloaded / total_size) * 100))
            bar_length = 30
            filled = int(bar_length * downloaded / total_size)
            bar = "=" * filled + "-" * (bar_length - filled)
            speed_str = f"{speed / 1024 / 1024:.2f} MB/s" if speed > 1024 * 1024 else f"{speed / 1024:.1f} KB/s"
            sys.stdout.write(f"\r[{bar}] {percent}% ({downloaded / 1024 / 1024:.1f}/{total_size / 1024 / 1024:.1f} MB) @ {speed_str}")
        else:
            speed_str = f"{speed / 1024 / 1024:.2f} MB/s" if speed > 1024 * 1024 else f"{speed / 1024:.1f} KB/s"
            sys.stdout.write(f"\rDownloaded {downloaded / 1024 / 1024:.1f} MB @ {speed_str}")
        sys.stdout.flush()

    try:
        urllib.request.urlretrieve(url, dest_path, reporthook=progress_callback)
        print("\nDownload complete!")
    except Exception as e:
        print(f"\nDownload failed: {e}")
        sys.exit(1)


def main():
    if len(sys.argv) < 2:
        print("Usage: python -m hopit.download <URL> [destination_path]")
        sys.exit(1)
        
    url = sys.argv[1]
    dest = sys.argv[2] if len(sys.argv) >= 3 else None
    download_file(url, dest)


if __name__ == "__main__":
    main()
