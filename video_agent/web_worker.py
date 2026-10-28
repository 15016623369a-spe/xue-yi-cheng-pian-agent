from pathlib import Path
import sys
from .jobs import work

if __name__ == "__main__":
    work(Path(sys.argv[1]),sys.argv[2])
