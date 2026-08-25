from __future__ import annotations

import getpass
import os
from pathlib import Path

from app.admin_auth import hash_password
from app.config import settings


first = getpass.getpass("New admin password (12+ characters): ")
second = getpass.getpass("Repeat admin password: ")
if first != second:
    raise SystemExit("passwords do not match")
path = Path(settings.secrets_dir) / "admin_password_hash.txt"
path.parent.mkdir(parents=True, exist_ok=True)
path.write_text(hash_password(first) + "\n", encoding="utf-8")
os.chmod(path, 0o600)
print(f"saved={path}")
