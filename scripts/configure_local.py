"""Copy local Supabase API URL and secret key into gitignored .env."""

import json
import os
import subprocess
import tomllib
from pathlib import Path
from urllib.parse import urlparse


ROOT = Path(__file__).resolve().parents[1]
CLI = ROOT / "node_modules" / ".bin" / ("supabase.cmd" if os.name == "nt" else "supabase")


def key_from_local_studio():
    """Read the key from this project's Studio container without displaying it."""
    config = tomllib.loads((ROOT / "supabase" / "config.toml").read_text(encoding="utf-8"))
    container = "supabase_studio_" + config["project_id"]
    result = subprocess.run(["docker", "inspect", container],
                            capture_output=True, text=True)
    if result.returncode:
        raise SystemExit("Kunci tidak ada di CLI dan container Studio lokal tidak ditemukan")
    try:
        environment = json.loads(result.stdout)[0]["Config"]["Env"]
    except (ValueError, IndexError, KeyError, TypeError):
        raise SystemExit("Gagal membaca konfigurasi container Studio lokal") from None
    values = dict(item.split("=", 1) for item in environment if "=" in item)
    key = values.get("SUPABASE_SECRET_KEY") or values.get("SUPABASE_SERVICE_KEY")
    if not key:
        raise SystemExit("Container Studio lokal tidak memiliki secret key")
    return key


def main():
    if not CLI.exists():
        raise SystemExit("Supabase CLI belum terpasang: jalankan npm.cmd ci")
    env = os.environ.copy()
    env["SUPABASE_HOME"] = str(ROOT / ".supabase-home")
    env["SUPABASE_TELEMETRY_DISABLED"] = "1"
    result = subprocess.run([str(CLI), "status", "-o", "env"], cwd=ROOT,
                            env=env, capture_output=True, text=True)
    if result.returncode:
        raise SystemExit("Supabase lokal belum berjalan; jalankan 'supabase start' dahulu")

    values = {}
    for line in result.stdout.splitlines():
        name, sep, value = line.partition("=")
        if sep:
            values[name.strip()] = value.strip().strip('"')
    url = values.get("API_URL")
    key = values.get("SECRET_KEY") or values.get("SERVICE_ROLE_KEY")
    if not url:
        raise SystemExit("CLI tidak mengembalikan API_URL")
    parsed = urlparse(url)
    if parsed.scheme != "http" or parsed.hostname not in ("127.0.0.1", "localhost", "::1"):
        raise SystemExit("API_URL dari CLI bukan alamat HTTP loopback")

    if not key:
        key = key_from_local_studio()

    target = ROOT / ".env"
    source = target if target.exists() else ROOT / ".env.example"
    lines = source.read_text(encoding="utf-8").splitlines()
    replacements = {"SUPABASE_URL": url, "SUPABASE_SECRET_KEY": key}
    updated = set()
    for index, line in enumerate(lines):
        name, sep, _ = line.partition("=")
        if sep and name in replacements:
            lines[index] = f"{name}={replacements[name]}"
            updated.add(name)
    lines.extend(f"{name}={value}" for name, value in replacements.items()
                 if name not in updated)
    target.write_text("\n".join(lines) + "\n", encoding="utf-8")
    print("Konfigurasi Supabase lokal disimpan di .env (kunci tidak ditampilkan).")


if __name__ == "__main__":
    main()
