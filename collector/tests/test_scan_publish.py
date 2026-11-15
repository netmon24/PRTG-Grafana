"""scripts/scan_publish.py findet Zugangsdaten und Kundenbezug vor einer Veröffentlichung."""

import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


def _repo(tmp_path: Path, files: dict[str, str]) -> Path:
    for name, text in files.items():
        path = tmp_path / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")
    run = ["git", "-C", str(tmp_path)]
    ident = ["-c", "user.name=t", "-c", "user.email=t@example.org"]
    subprocess.run([*run, "init", "-q"], check=True)
    subprocess.run([*run, "add", "-A"], check=True)
    subprocess.run(
        [*run, *ident, "commit", "-q", "-m", "x"],
        check=True,
    )
    return tmp_path


def test_clean_repository_has_no_findings(tmp_path, load_script):
    repo = _repo(tmp_path, {"a.md": "Beispiel 10.0.0.1 und https://prtg.firma.de\n"})
    assert load_script("scan_publish").scan(repo, deny=[]) == []


def test_token_private_key_and_personal_mail_are_found(tmp_path, load_script):
    token = "gh" + "o_" + "a" * 36
    repo = _repo(
        tmp_path,
        {
            "t.txt": f"key={token}\n",
            "k.txt": "-----BEGIN " + "PRIVATE KEY-----\n",
            "m.txt": "mail " + "max" + "@mustermail.net\n",
        },
    )
    kinds = {f.kind for f in load_script("scan_publish").scan(repo, deny=[])}
    assert {"token", "private-key", "e-mail"} <= kinds


def test_denied_customer_term_is_found_in_tree_and_history(tmp_path, load_script):
    repo = _repo(tmp_path, {"a.md": "Kunde acme-gmbh\n"})
    (repo / "a.md").write_text("sauber\n", encoding="utf-8")
    ident = ["-c", "user.name=t", "-c", "user.email=t@example.org"]
    subprocess.run(["git", "-C", str(repo), *ident, "commit", "-qam", "y"], check=True)
    findings = load_script("scan_publish").scan(repo, deny=["acme-gmbh"])
    assert [f.where for f in findings if f.kind == "begriff"] == ["history"]


def test_sensitive_file_names_are_found(tmp_path, load_script):
    repo = _repo(tmp_path, {".env": "A=1\n", "ok.txt": "x\n"})
    assert any(
        f.kind == "datei" and f.what == ".env"
        for f in load_script("scan_publish").scan(repo, deny=[])
    )


def test_stray_secret_files_and_more_token_forms_are_found(tmp_path, load_script):
    repo = _repo(
        tmp_path,
        {
            "prod.env": "A=1\n",
            ".env.local": "A=1\n",
            "backup/id_rsa": "x\n",
            "grafana.txt": "key=glsa_" + "a" * 32 + "_abcdef12\n",
            "jwt.txt": "t=eyJ" + "a" * 20 + "." + "b" * 20 + "." + "c" * 20 + "\n",
            "influx.txt": "T=" + "A" * 70 + "==\n",
            "ok.txt": "ein .env-Beispiel\n",
        },
    )
    found = load_script("scan_publish").scan(repo, deny=[])
    files = {f.file for f in found if f.kind == "datei"}
    assert files == {"prod.env", ".env.local", "backup/id_rsa"}
    assert {f.file for f in found if f.kind == "token"} == {"grafana.txt", "jwt.txt", "influx.txt"}


def test_env_example_is_not_a_sensitive_file(tmp_path, load_script):
    repo = _repo(tmp_path, {".env.example": "A=\n", "cores.yaml.example": "x: 1\n"})
    assert load_script("scan_publish").scan(repo, deny=[]) == []
