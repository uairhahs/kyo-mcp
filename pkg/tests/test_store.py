"""The bundle store: OKF concept files are the source of truth and SQLite is
an index derived from them (kyo_mcp/store.py)."""

import os
from pathlib import Path

import pytest
from kyo_mcp import database as db
from kyo_mcp import store
from kyo_mcp.bundle import validate_bundle
from kyo_mcp.bundle_format import KYO_LINKS_KEY, LINKS_START, path_derived_id
from kyo_mcp.okf_schema import parse_frontmatter


def save(node_id, title, type_="concept", body=None, **extra):
    return store.save_concept(
        {"id": node_id, "type": type_, "title": title, **extra},
        body if body is not None else f"# {title}\n",
        create=True,
    )


def file_of(node_id) -> Path:
    return store.concept_file(db.get_concept_by_id(node_id)["bundle_path"])


def edit(path: Path, text: str) -> None:
    """Write as an editor would, making sure the mtime moves."""
    before = path.stat().st_mtime_ns if path.exists() else 0
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    if path.stat().st_mtime_ns == before:
        os.utime(path, ns=(before + 1_000_000, before + 1_000_000))


def frontmatter(node_id):
    return parse_frontmatter(file_of(node_id).read_text())[0]


class TestWrites:
    def test_save_writes_a_conformant_file(self):
        row = save("kyo-a", "Alpha", "Playbook", description="First.")
        assert row["bundle_path"] == "playbook/alpha"
        assert row["file_stamp"]["sha256"]
        assert frontmatter("kyo-a")["kyo_id"] == "kyo-a"
        assert validate_bundle(str(store.bundle_dir()))["conformant"]

    def test_create_refuses_an_existing_id(self):
        save("kyo-a", "Alpha")
        with pytest.raises(db.ConceptExistsError):
            save("kyo-a", "Again")

    def test_update_keeps_the_path(self):
        save("kyo-a", "Alpha")
        store.update_concept("kyo-a", {"title": "Renamed"})
        row = db.get_concept_by_id("kyo-a")
        assert row["bundle_path"] == "concept/alpha"
        assert frontmatter("kyo-a")["title"] == "Renamed"

    def test_update_keeps_the_body(self):
        save("kyo-a", "Alpha", body="Keep me\n")
        store.update_concept("kyo-a", {"title": "New"})
        assert db.get_concept_by_id("kyo-a")["body_text"] == "Keep me\n"

    def test_touch_does_not_write_the_file(self):
        save("kyo-a", "Alpha")
        before = file_of("kyo-a").read_bytes()
        store.update_concept("kyo-a", {})
        assert file_of("kyo-a").read_bytes() == before

    def test_verify_is_written_to_the_file(self):
        save("kyo-a", "Alpha")
        assert store.verify_concept(
            "kyo-a", {"by": "alice", "at": "2026-01-01T00:00:00Z"}
        )
        assert frontmatter("kyo-a")["verified"] == [
            {"by": "human:alice", "at": "2026-01-01T00:00:00Z"}
        ]
        assert not store.verify_concept("kyo-missing", {"by": "x"})

    def test_paths_never_collide(self):
        save("kyo-a", "Same")
        save("kyo-b", "Same")
        paths = {db.get_concept_by_id(i)["bundle_path"] for i in ("kyo-a", "kyo-b")}
        assert len(paths) == 2

    @pytest.mark.parametrize("title", ["index", "log"])
    def test_reserved_names_are_never_used(self, title):
        """§3.1: index.md and log.md must not be concept documents."""
        row = save("kyo-a", title)
        assert row["bundle_path"].split("/")[-1] not in ("index", "log")

    def test_no_temporary_files_are_left(self):
        save("kyo-a", "Alpha")
        store.update_concept("kyo-a", {"title": "B"})
        leftovers = [p for p in store.bundle_dir().rglob("*") if p.name.startswith(".")]
        assert leftovers == []


class TestLinks:
    def test_typed_link_lives_in_the_source_file(self):
        save("kyo-a", "Alpha")
        save("kyo-b", "Beta")
        store.add_link("kyo-a", "kyo-b", "depends_on")
        assert frontmatter("kyo-a")[KYO_LINKS_KEY] == [
            {"rel": "depends_on", "target": "/concept/beta.md"}
        ]
        body = parse_frontmatter(file_of("kyo-a").read_text())[1]
        assert "- depends_on: [Beta](./beta.md)" in body
        assert db.get_links("kyo-a", "out") == [
            {"source_id": "kyo-a", "target_id": "kyo-b", "relation_type": "depends_on"}
        ]
        assert db.get_concept_by_id("kyo-a")["body_text"] == "# Alpha\n"

    def test_remove_typed_link(self):
        save("kyo-a", "Alpha")
        save("kyo-b", "Beta")
        store.add_link("kyo-a", "kyo-b", "depends_on")
        assert store.remove_link("kyo-a", "kyo-b") == (1, False)
        assert KYO_LINKS_KEY not in frontmatter("kyo-a")
        assert LINKS_START not in file_of("kyo-a").read_text()
        assert db.get_all_links() == []

    def test_remove_one_link_keeps_the_others(self):
        """Regression: removing one of several links used to raise."""
        for node_id, title in (
            ("kyo-a", "Alpha"),
            ("kyo-b", "Beta"),
            ("kyo-c", "Gamma"),
        ):
            save(node_id, title)
        store.add_link("kyo-a", "kyo-b", "depends_on")
        store.add_link("kyo-a", "kyo-b", "measures")
        store.add_link("kyo-a", "kyo-c", "depends_on")
        assert store.remove_link("kyo-a", "kyo-b", "measures") == (1, False)
        assert frontmatter("kyo-a")[KYO_LINKS_KEY] == [
            {"rel": "depends_on", "target": "/concept/beta.md"},
            {"rel": "depends_on", "target": "/concept/gamma.md"},
        ]
        assert store.remove_link("kyo-a", "kyo-b") == (1, False)
        assert [link["target_id"] for link in db.get_links("kyo-a", "out")] == ["kyo-c"]

    def test_delete_keeps_links_to_other_concepts(self):
        """Regression: deleting a target the source also links past raised."""
        for node_id, title in (
            ("kyo-a", "Alpha"),
            ("kyo-b", "Beta"),
            ("kyo-c", "Gamma"),
        ):
            save(node_id, title)
        store.add_link("kyo-a", "kyo-b", "depends_on")
        store.add_link("kyo-a", "kyo-c", "depends_on")
        store.delete_concept("kyo-b")
        assert frontmatter("kyo-a")[KYO_LINKS_KEY] == [
            {"rel": "depends_on", "target": "/concept/gamma.md"}
        ]

    def test_body_link_is_a_references_edge_and_stays(self):
        save("kyo-b", "Beta")
        save("kyo-a", "Alpha", body="See [beta](./beta.md).\n")
        assert db.get_links("kyo-a", "out")[0]["relation_type"] == "references"
        assert store.remove_link("kyo-a", "kyo-b") == (0, True)
        assert len(db.get_links("kyo-a", "out")) == 1

    def test_broken_link_resolves_when_the_target_appears(self):
        save("kyo-a", "Alpha", body="See [later](./later.md).\n")
        assert db.get_all_links() == []
        edit(store.bundle_dir() / "concept" / "later.md", "---\ntype: t\n---\n")
        report = store.refresh()
        later = path_derived_id("concept/later")
        assert report["broken_links"] == 0
        assert db.get_links("kyo-a", "out")[0]["target_id"] == later

    def test_delete_removes_file_and_typed_links_to_it(self):
        save("kyo-a", "Alpha", body="Body [beta](./beta.md)\n")
        save("kyo-b", "Beta")
        save("kyo-c", "Gamma")
        store.add_link("kyo-c", "kyo-b", "depends_on")
        b_file = file_of("kyo-b")
        assert store.delete_concept("kyo-b")
        assert not b_file.exists()
        assert db.get_concept_by_id("kyo-b") is None
        assert KYO_LINKS_KEY not in frontmatter("kyo-c")
        # A body link is text: it stays, as a tolerated broken link.
        assert "[beta](./beta.md)" in file_of("kyo-a").read_text()
        assert db.get_all_links() == []
        assert not store.delete_concept("kyo-b")


class TestDirectEdits:
    def test_edit_is_picked_up(self):
        save("kyo-a", "Alpha", body="old words\n")
        path = file_of("kyo-a")
        edit(path, path.read_text().replace("old words", "brand new zebra"))
        report = store.refresh()
        assert report["indexed"] == 1
        assert db.get_concept_by_id("kyo-a")["body_text"] == "brand new zebra\n"
        assert [r["id"] for r in db.query_catalog(search_term="zebra")] == ["kyo-a"]

    def test_new_file_gets_a_path_derived_id(self, tmp_path):
        edit(
            store.bundle_dir() / "notes" / "hello.md",
            "---\ntype: Note\ntitle: Hi\n---\nx\n",
        )
        store.refresh()
        [row] = db.query_catalog()
        assert row["id"] == path_derived_id("notes/hello")
        assert row["bundle_path"] == "notes/hello"

    def test_path_derived_ids_agree_across_stores(self, tmp_path):
        """Two devices indexing the same files derive the same ids."""
        text = "---\ntype: Note\n---\n"
        edit(store.bundle_dir() / "a" / "x.md", text)
        store.refresh()
        other = tmp_path / "other" / "kyo.db"
        edit(store.bundle_dir(other) / "a" / "x.md", text)
        store.refresh(other)
        assert db.query_catalog()[0]["id"] == db.query_catalog(db_path=other)[0]["id"]

    def test_edit_keeps_the_id(self):
        edit(store.bundle_dir() / "n.md", "---\ntype: t\n---\nv1\n")
        store.refresh()
        first = db.query_catalog()[0]["id"]
        edit(store.bundle_dir() / "n.md", "---\ntype: t\ntitle: edited\n---\nv2\n")
        store.refresh()
        assert db.query_catalog()[0]["id"] == first

    def test_removed_file_is_removed_from_the_index(self):
        save("kyo-a", "Alpha")
        file_of("kyo-a").unlink()
        assert store.refresh()["removed"] == 1
        assert db.get_concept_by_id("kyo-a") is None

    def test_moved_file_keeps_its_kyo_id_and_sync_state(self):
        save("kyo-a", "Alpha")
        db.set_sync_hash("kyo-a", "hindsight", "abc")
        old = file_of("kyo-a")
        new = store.bundle_dir() / "moved" / "here.md"
        new.parent.mkdir()
        old.rename(new)
        report = store.refresh()
        row = db.get_concept_by_id("kyo-a")
        assert report["removed"] == 0
        assert row["bundle_path"] == "moved/here"
        assert db.get_sync_hash("kyo-a", "hindsight") == "abc"

    def test_moved_file_without_kyo_id_is_a_new_concept(self):
        """As in OKF, where the path is the identity (§2)."""
        edit(store.bundle_dir() / "a.md", "---\ntype: t\n---\n")
        store.refresh()
        (store.bundle_dir() / "a.md").rename(store.bundle_dir() / "b.md")
        store.refresh()
        assert [r["id"] for r in db.query_catalog()] == [path_derived_id("b")]

    def test_broken_edit_keeps_the_last_version(self):
        save("kyo-a", "Alpha", body="good\n")
        path = file_of("kyo-a")
        good = path.read_text()
        edit(path, "---\ntype: [unclosed\n---\nhalf-typed\n")
        report = store.refresh()
        assert report["errors"][0]["path"] == "concept/alpha.md"
        assert "keeping the last indexed version" in report["errors"][0]["message"]
        assert db.get_concept_by_id("kyo-a")["body_text"] == "good\n"
        edit(path, good.replace("good", "fixed"))
        assert store.refresh()["errors"] == []
        assert db.get_concept_by_id("kyo-a")["body_text"] == "fixed\n"

    def test_copied_file_with_the_same_kyo_id(self):
        save("kyo-a", "Alpha")
        copy = store.bundle_dir() / "concept" / "alpha-copy.md"
        edit(copy, file_of("kyo-a").read_text())
        report = store.refresh()
        assert report["warnings"][0]["path"] == "concept/alpha-copy.md"
        assert db.get_concept_by_id("kyo-a")["bundle_path"] == "concept/alpha"
        assert db.get_concept_by_id(path_derived_id("concept/alpha-copy"))

    def test_invalid_kyo_id_falls_back_to_the_path(self):
        edit(store.bundle_dir() / "n.md", "---\ntype: t\nkyo_id: 'has spaces'\n---\n")
        report = store.refresh()
        assert db.query_catalog()[0]["id"] == path_derived_id("n")
        assert "not a valid id" in report["warnings"][0]["message"]

    def test_unchanged_files_are_not_reindexed(self):
        save("kyo-a", "Alpha")
        before = db.get_concept_by_id("kyo-a")["updated_at"]
        report = store.refresh()
        assert report["indexed"] == 0 and report["unchanged"] == 1
        assert db.get_concept_by_id("kyo-a")["updated_at"] == before

    def test_touched_but_identical_file_is_not_reindexed(self):
        save("kyo-a", "Alpha")
        path = file_of("kyo-a")
        stat = path.stat()
        os.utime(path, ns=(stat.st_atime_ns, stat.st_mtime_ns + 5_000_000))
        report = store.refresh()
        assert report["indexed"] == 0
        assert store.refresh()["unchanged"] == 1  # new stamp recorded

    def test_json_escaped_emoji_in_frontmatter(self):
        r"""Regression: "\ud83d\ude00" (json.dumps of an emoji) decoded to
        lone surrogates, which crashed the whole refresh."""
        edit(
            store.bundle_dir() / "e.md",
            '---\ntype: t\ntitle: "hi \\ud83d\\ude00"\n---\n',
        )
        edit(store.bundle_dir() / "lone.md", '---\ntype: t\ntitle: "x \\ud83d"\n---\n')
        edit(store.bundle_dir() / "ok.md", "---\ntype: t\ntitle: fine\n---\n")
        report = store.refresh()
        assert report["errors"] == [] and report["indexed"] == 3
        titles = {r["bundle_path"]: r["title"] for r in db.query_catalog()}
        assert titles == {"e": "hi \U0001f600", "lone": "x \ufffd", "ok": "fine"}

    def test_reserved_and_hidden_files_are_not_concepts(self):
        root = store.bundle_dir()
        edit(root / "index.md", "# Index\n")
        edit(root / "log.md", "# Log\n")
        edit(root / ".git" / "x.md", "---\ntype: t\n---\n")
        edit(root / "notes.txt", "not markdown")
        store.refresh()
        assert db.query_catalog() == []

    def test_bundle_dir_setting(self, monkeypatch, tmp_path):
        monkeypatch.setenv("KYO_BUNDLE_DIR", str(tmp_path / "elsewhere"))
        assert store.bundle_dir() == (tmp_path / "elsewhere").resolve()
        # An explicit database always uses the bundle next to it.
        assert (
            store.bundle_dir(tmp_path / "x" / "k.db")
            == (tmp_path / "x" / "bundle").resolve()
        )


class TestUpgrade:
    """A catalogue from before the bundle store has rows but no files."""

    def test_rows_without_files_are_written_out(self):
        db.create_concept(
            {
                "id": "kyo-old",
                "type": "Metric",
                "title": "Old",
                "verified": [{"by": "human:a"}],
            },
            markdown_body="kept body\n",
        )
        db.create_concept({"id": "kyo-new", "type": "Metric", "title": "Target"}, "x\n")
        db.create_link("kyo-old", "kyo-new", "measures")
        report = store.refresh()
        assert report["written"] == 2 and report["removed"] == 0
        row = db.get_concept_by_id("kyo-old")
        assert row["body_text"] == "kept body\n"
        assert row["verified"] == [{"by": "human:a"}]
        assert frontmatter("kyo-old")[KYO_LINKS_KEY] == [
            {"rel": "measures", "target": "/metric/target.md"}
        ]
        assert db.get_links("kyo-old", "out")[0]["relation_type"] == "measures"

    def test_upgrade_never_overwrites_a_foreign_file(self):
        foreign = store.bundle_dir() / "metric" / "old.md"
        edit(foreign, "---\ntype: Metric\nkyo_id: kyo-someone-else\n---\nmine\n")
        db.create_concept({"id": "kyo-old", "type": "Metric", "title": "Old"}, "x\n")
        store.refresh()
        assert "mine" in foreign.read_text()
        assert db.get_concept_by_id("kyo-old")["bundle_path"] != "metric/old"

    def test_upgrade_keeps_a_stored_path(self):
        db.create_concept(
            {"id": "kyo-old", "type": "t", "title": "T", "bundle_path": "keep/me"},
            "x\n",
        )
        store.refresh()
        assert (store.bundle_dir() / "keep" / "me.md").exists()

    def test_index_only_verification_is_promoted_to_the_file(self):
        save("kyo-a", "Alpha")
        db.update_node_verified("kyo-a", {"by": "alice", "at": "2026-01-01T00:00:00Z"})
        store.refresh()
        assert frontmatter("kyo-a")["verified"] == [
            {"by": "human:alice", "at": "2026-01-01T00:00:00Z"}
        ]

    def test_index_only_write_is_promoted_to_the_file(self):
        """A row written straight to the index loses its file stamp, so the
        next refresh writes it out rather than letting the two disagree."""
        save("kyo-a", "Alpha")
        db.create_concept({"id": "kyo-a", "type": "concept", "title": "Direct"}, "d\n")
        store.refresh()
        assert frontmatter("kyo-a")["title"] == "Direct"
        assert db.get_concept_by_id("kyo-a")["file_stamp"]
