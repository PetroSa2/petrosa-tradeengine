from pathlib import Path

ROOT = Path(__file__).parents[1]
S1 = (
    "MongoDB is the operational store: every live-path read and write goes to "
    "MongoDB. MySQL holds a historic reference copy only (statistical analysis, "
    "backtesting, research) and is never read on the live path."
)
S2 = (
    "`petrosa-data-manager` is the only service that connects to any database; "
    "every other service reads and writes data exclusively through the data-manager API."
)


def test_root_agent_docs_state_data_pillars_verbatim() -> None:
    for name in ("README.md", "docs/agent-rules.md"):
        content = (ROOT / name).read_text()
        assert S1 in content
        assert S2 in content


def test_non_archived_docs_do_not_reintroduce_removed_database_language() -> None:
    files = [ROOT / "README.md", ROOT / "docs/agent-rules.md"]
    files.extend(path for path in (ROOT / "docs").rglob("*") if path.is_file())
    forbidden = (
        "Dual Persistence",
        "SECONDARY BACKUP",
        "DB_ADAPTER=mysql",
        "MYSQL_URI",
        "from shared.mysql_client",
        "AsyncIOMotorClient",
        "mysql -u root",
        "MySQL-backed**",
        "MySQL audit logging",
    )
    for path in files:
        if "docs/archive" in str(path):
            continue
        content = path.read_text()
        for phrase in forbidden:
            assert phrase not in content, f"{phrase} remains in {path}"


def test_position_comments_describe_data_manager_store() -> None:
    dispatcher = (ROOT / "tradeengine/dispatcher.py").read_text()
    position_manager = (ROOT / "tradeengine/position_manager.py").read_text()
    assert "MongoDB fallback" not in dispatcher
    assert "MongoDB for coordination only" not in position_manager


def test_archive_moves_are_complete() -> None:
    assert not (ROOT / "docs/COMMIT_MESSAGE.md").exists()
    assert not (ROOT / "pr_body.md").exists()
    assert (ROOT / "docs/archive/README.md").exists()
