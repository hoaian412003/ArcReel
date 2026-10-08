"""Persistent project identity and memory, separate from project source."""

from __future__ import annotations

import io
import re
import shutil
import zipfile
from pathlib import Path
from typing import Any
from uuid import NAMESPACE_URL, uuid5

from lib.infra.data_root_layout import DataRootLayout
from lib.infra.json_io import atomic_write_bytes, atomic_write_json, load_json

MAX_MEMORY_BYTES = 200_000


class AgentWorkspace:
    def __init__(self, layout: DataRootLayout, project_path: Path, project_name: str, user_id: str) -> None:
        self.agent_id = uuid5(NAMESPACE_URL, f"arcreel:claude:{project_name}").hex
        self.root = layout.runtime_dir / "claude_agents" / self.agent_id
        self.root.mkdir(parents=True, exist_ok=True)
        self.manifest_path = self.root / "agent.json"
        if self.manifest_path.exists():
            self.manifest: dict[str, Any] = load_json(self.manifest_path)
        else:
            self.manifest = {
                "agent_id": self.agent_id,
                "project_name": project_name,
                "user_id": user_id,
                "cwd": str(project_path.resolve()),
                "main_session_id": None,
            }
            atomic_write_json(self.manifest_path, self.manifest)
        self.cwd = Path(self.manifest["cwd"])
        if self.cwd.resolve() != project_path.resolve():
            raise ValueError("Project workspace moved; restore the pinned workspace path before resuming")
        self.memory_dir = self.root / "memory"
        if not self.memory_dir.exists():
            native = Path.home() / ".claude" / "projects" / re.sub(r"[^a-zA-Z0-9]", "-", str(self.cwd)) / "memory"
            if native.is_dir():
                shutil.copytree(native, self.memory_dir, symlinks=True)
            else:
                self.memory_dir.mkdir()

    def set_main_session(self, session_id: str, user_id: str) -> None:
        self.manifest["main_session_id"] = session_id
        self.manifest.setdefault("main_sessions", {})[user_id] = session_id
        atomic_write_json(self.manifest_path, self.manifest)

    def main_session(self, user_id: str) -> str | None:
        return self.manifest.get("main_sessions", {}).get(user_id) or (
            self.manifest.get("main_session_id") if self.manifest.get("user_id") == user_id else None
        )

    def clear_main_session(self, user_id: str) -> None:
        main_sessions = self.manifest.get("main_sessions")
        if isinstance(main_sessions, dict):
            main_sessions.pop(user_id, None)
        if self.manifest.get("user_id") == user_id:
            self.manifest["main_session_id"] = None
        atomic_write_json(self.manifest_path, self.manifest)

    def memory(self) -> list[dict[str, str]]:
        return [
            {"name": path.name, "content": path.read_text(encoding="utf-8")}
            for path in sorted(self.memory_dir.glob("*.md"))
            if not path.is_symlink() and path.stat().st_size <= MAX_MEMORY_BYTES
        ]

    def write_memory(self, name: str, content: str) -> None:
        if not re.fullmatch(r"[A-Za-z0-9_-]+\.md", name) or len(content.encode()) > MAX_MEMORY_BYTES:
            raise ValueError("Invalid memory file or content size")
        atomic_write_bytes(self.memory_dir / name, content.encode("utf-8"))

    def promote(self, name: str) -> None:
        notes = {item["name"]: item["content"] for item in self.memory()}
        if name not in notes:
            raise ValueError("Memory file not found")
        rules = self.cwd / ".claude" / "rules"
        rules.mkdir(parents=True, exist_ok=True)
        target = rules / f"memory-{name}"
        if target.exists():
            raise ValueError("This memory already has a project rule")
        atomic_write_bytes(target, notes[name].encode("utf-8"))

    def reset(self) -> None:
        backup = self.root / "memory_backups"
        backup.mkdir(exist_ok=True)
        from uuid import uuid4

        self.memory_dir.rename(backup / uuid4().hex)
        self.memory_dir.mkdir()

    def export(self, history: dict[str, Any], sdk_session_ids: list[str]) -> bytes:
        import json

        output = io.BytesIO()
        with zipfile.ZipFile(output, "w", zipfile.ZIP_DEFLATED) as archive:
            archive.writestr("agent.json", json.dumps(self.manifest, ensure_ascii=False, indent=2))
            archive.writestr("history.json", json.dumps(history, ensure_ascii=False, indent=2))
            for note in self.memory():
                archive.writestr(f"memory/{note['name']}", note["content"])
            native_root = Path.home() / ".claude" / "projects" / re.sub(r"[^a-zA-Z0-9]", "-", str(self.cwd))
            for session_id in sdk_session_ids:
                if not re.fullmatch(r"[A-Za-z0-9_-]+", session_id):
                    continue
                transcript = native_root / f"{session_id}.jsonl"
                if transcript.is_file() and not transcript.is_symlink():
                    archive.write(transcript, f"native/{transcript.name}")
                for entry in (native_root / session_id).rglob("*"):
                    if (
                        entry.is_file()
                        and not entry.is_symlink()
                        and entry.resolve().is_relative_to(native_root.resolve())
                    ):
                        archive.write(entry, f"native/{entry.relative_to(native_root)}")
        return output.getvalue()
