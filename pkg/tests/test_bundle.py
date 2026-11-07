"""OKF v0.2 bundle tools (export, import, validate) and the shared format
helpers. Section references (§) are to docs/SPEC.md. The fixtures under
tests/fixtures/okf/ are Google's sample bundles, copied unmodified (see
docs/THIRD_PARTY.md)."""

import json
import shutil
from pathlib import Path

import pytest
from kyo_mcp import database as db
from kyo_mcp import store
from kyo_mcp.bundle import (
    EXPORT_MANIFEST,
    BundleError,
    export_bundle,
    import_bundle,
    validate_bundle,
)
from kyo_mcp.bundle_format import (
    append_links_block,
    body_link_targets,
    concept_files,
    is_valid_bundle_path,
    path_derived_id,
    relative_link,
    resolve_link,
    strip_links_block,
    suggested_path,
)
from kyo_mcp.cli import run as cli_run
from kyo_mcp.okf_schema import parse_frontmatter

FIXTURES = Path(__file__).parent / "fixtures" / "okf"
UPSTREAM_BUNDLES = ["acme_retail", "ga4"]


def save(node_id, title, type_="concept", body=None, **extra):
    return store.save_concept(
        {"id": node_id, "type": type_, "title": title, **extra},
        body if body is not None else f"# {title}\n",
        create=True,
    )


def concept_bytes(root: Path):
    return {f"{path}.md": file.read_bytes() for file, path in concept_files(root)}


def all_files(root: Path):
    return {
        p.relative_to(root).as_posix(): p.read_bytes()
        for p in root.rglob("*")
        if p.is_file()
    }


def make_bundle(root: Path, files):
    for rel, text in files.items():
        path = root / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")
    return str(root)


# --- Format helpers ----------------------------------------------------------


class TestFormat:
    def test_suggested_path(self):
        assert suggested_path("BigQuery Table", "Customer Orders", "kyo-1") == (
            "bigquery-table/customer-orders"
        )
        assert suggested_path(None, None, "kyo-xyz") == "concepts/kyo-xyz"

    @pytest.mark.parametrize(
        "path, valid",
        [
            ("a/b", True),
            ("index", False),
            ("dir/LOG", False),
            ("/abs", False),
            ("a/../b", False),
            ("a/.hidden", False),
            ("a.md", False),
            ("", False),
        ],
    )
    def test_is_valid_bundle_path(self, path, valid):
        assert is_valid_bundle_path(path) is valid

    def test_path_derived_id_is_stable(self):
        assert path_derived_id("tables/orders") == path_derived_id("tables/orders")
        assert path_derived_id("tables/orders") != path_derived_id("tables/order")
        assert path_derived_id("tables/orders").startswith("kyo-")

    @pytest.mark.parametrize(
        "href, from_path, expected",
        [
            ("/tables/orders.md", "x/y", "tables/orders"),
            ("./other.md", "dir/a", "dir/other"),
            ("../tables/orders.md#schema", "dir/a", "tables/orders"),
            ("sub/b.md?x=1", "dir/a", "dir/sub/b"),
            ("/with%20space.md", "a", "with space"),
            ("https://example.com/x.md", "a", None),
            ("mailto:a@b.c", "a", None),
            ("/tables/", "a", None),
            ("image.png", "a", None),
            ("../../outside.md", "a", None),
        ],
    )
    def test_resolve_link(self, href, from_path, expected):
        assert resolve_link(href, from_path) == expected

    @pytest.mark.parametrize(
        "target, source, expected",
        [
            ("concept/beta", "concept/alpha", "./beta.md"),
            ("metric/beta", "concept/alpha", "../metric/beta.md"),
            ("top", "a/b/c", "../../top.md"),
            ("a/b", "root", "./a/b.md"),
        ],
    )
    def test_relative_link(self, target, source, expected):
        href = relative_link(target, source)
        assert href == expected
        assert resolve_link(href, source) == target

    def test_links_in_code_and_images_are_ignored(self):
        body = (
            "See [a](/a.md) and ![img](/img.md).\n\n"
            "```\n[not](/code.md)\n```\n\n`[inline](/inline.md)`\n"
            '[titled](/b.md "Title")\n'
        )
        assert body_link_targets(body) == ["/a.md", "/b.md"]

    @pytest.mark.parametrize("body", ["text\n", "text", "", "a\n\nb\n\n"])
    def test_links_block_round_trips(self, body):
        with_block = append_links_block(body, ["- references: [B](./b.md)"])
        expected = body if body.endswith("\n") else body + "\n"
        assert strip_links_block(with_block) == expected


# --- Export ------------------------------------------------------------------


class TestExport:
    def test_empty_catalogue(self, tmp_path):
        result = export_bundle(str(tmp_path / "b"))
        assert result["concepts"] == 0 and result["validation"]["conformant"]
        frontmatter, _ = parse_frontmatter((tmp_path / "b" / "index.md").read_text())
        assert frontmatter == {"okf_version": "0.2"}

    def test_copies_the_store_byte_for_byte(self, tmp_path):
        save("kyo-a", "Alpha", "Playbook", description="First.")
        save("kyo-b", "Beta", "Metric")
        store.add_link("kyo-a", "kyo-b", "measures")
        result = export_bundle(str(tmp_path / "b"))
        assert result["validation"] == {
            "conformant": True,
            "okf_version": "0.2",
            "concepts": 2,
            "errors": [],
            "warnings": [],
        }
        assert concept_bytes(tmp_path / "b") == concept_bytes(store.bundle_dir())

    def test_index_files(self, tmp_path):
        """§8: every directory gets an index; only the root has frontmatter,
        and entries carry the concept's description."""
        save("kyo-a", "Alpha", "Playbook", description="First line.\nSecond.")
        export_bundle(str(tmp_path / "b"))
        root_fm, root_body = parse_frontmatter(
            (tmp_path / "b" / "index.md").read_text()
        )
        assert root_fm == {"okf_version": "0.2"}
        assert "* [playbook](playbook/)" in root_body
        sub = (tmp_path / "b" / "playbook" / "index.md").read_text()
        assert not sub.startswith("---")
        assert "* [Alpha](alpha.md) - First line. Second." in sub

    def test_unparseable_files_are_not_exported(self, tmp_path):
        broken = store.bundle_dir() / "broken.md"
        broken.parent.mkdir(parents=True, exist_ok=True)
        broken.write_text("# no frontmatter\n")
        save("kyo-a", "Alpha")
        export_bundle(str(tmp_path / "b"))
        assert set(concept_bytes(tmp_path / "b")) == {"concept/alpha.md"}

    def test_refuses_a_foreign_directory(self, tmp_path):
        (tmp_path / "b").mkdir()
        (tmp_path / "b" / "notes.md").write_text("mine\n")
        with pytest.raises(BundleError, match="not a previous Kyo export"):
            export_bundle(str(tmp_path / "b"), overwrite=True)
        assert (tmp_path / "b" / "notes.md").read_text() == "mine\n"

    def test_refuses_to_export_into_the_store(self):
        with pytest.raises(BundleError, match="overlaps the store"):
            export_bundle(str(store.bundle_dir() / "sub"))
        with pytest.raises(BundleError, match="overlaps the store"):
            export_bundle(str(store.bundle_dir().parent))

    def test_overwrite_needs_the_flag(self, tmp_path):
        export_bundle(str(tmp_path / "b"))
        with pytest.raises(BundleError, match="pass overwrite"):
            export_bundle(str(tmp_path / "b"))

    def test_overwrite_replaces_only_its_own_files(self, tmp_path):
        save("kyo-a", "Alpha")
        save("kyo-b", "Beta", "Other")
        export_bundle(str(tmp_path / "b"))
        (tmp_path / "b" / "README.txt").write_text("keep me\n")
        store.delete_concept("kyo-b")
        export_bundle(str(tmp_path / "b"), overwrite=True)
        assert set(concept_bytes(tmp_path / "b")) == {"concept/alpha.md"}
        assert not (tmp_path / "b" / "other").exists()
        assert (tmp_path / "b" / "README.txt").read_text() == "keep me\n"

    def test_export_is_deterministic(self, tmp_path):
        save("kyo-a", "Alpha")
        export_bundle(str(tmp_path / "one"))
        export_bundle(str(tmp_path / "two"))
        assert all_files(tmp_path / "one") == all_files(tmp_path / "two")
        manifest = json.loads((tmp_path / "one" / EXPORT_MANIFEST).read_text())
        assert manifest["files"] == ["concept/alpha.md", "concept/index.md", "index.md"]


# --- Import ------------------------------------------------------------------


@pytest.mark.parametrize("bundle", UPSTREAM_BUNDLES)
class TestUpstreamInterop:
    """Google's own sample bundles, through Kyo and back out."""

    def test_upstream_bundle_is_conformant(self, bundle):
        result = validate_bundle(str(FIXTURES / bundle))
        assert result["conformant"], result["errors"]

    def test_import_and_export_are_byte_identical(self, bundle, tmp_path):
        result = import_bundle(str(FIXTURES / bundle))
        assert result["skipped"] == [] and result["broken_links"] == []
        assert concept_bytes(store.bundle_dir()) == concept_bytes(FIXTURES / bundle)
        exported = export_bundle(str(tmp_path / "out"))
        assert exported["validation"]["conformant"]
        assert concept_bytes(tmp_path / "out") == concept_bytes(FIXTURES / bundle)

    def test_ids_are_path_derived(self, bundle):
        import_bundle(str(FIXTURES / bundle))
        for row in db.query_catalog():
            assert row["id"] == path_derived_id(row["bundle_path"])

    def test_links_become_graph_edges(self, bundle):
        import_bundle(str(FIXTURES / bundle))
        ids = {r["bundle_path"]: r["id"] for r in db.query_catalog()}
        edges = set()
        for file, path in concept_files(FIXTURES / bundle):
            _, body = parse_frontmatter(file.read_text())
            for href in body_link_targets(body):
                target = resolve_link(href, path)
                if target in ids:
                    edges.add((ids[path], ids[target]))
        assert edges
        assert {
            (link["source_id"], link["target_id"]) for link in db.get_all_links()
        } == edges

    def test_reimport_is_idempotent(self, bundle):
        first = import_bundle(str(FIXTURES / bundle))
        before = sorted((c["id"], c["bundle_path"]) for c in db.query_catalog())
        second = import_bundle(str(FIXTURES / bundle))
        assert first["created"] == first["concepts"]
        assert second["unchanged"] == second["concepts"]
        assert sorted((c["id"], c["bundle_path"]) for c in db.query_catalog()) == before

    def test_same_ids_on_another_device(self, bundle, tmp_path):
        import_bundle(str(FIXTURES / bundle))
        other = tmp_path / "device2" / "kyo.db"
        import_bundle(str(FIXTURES / bundle), db_path=other)
        ids = lambda **kw: sorted(r["id"] for r in db.query_catalog(**kw))  # noqa: E731
        assert ids() == ids(db_path=other)


def test_attested_computation_fields_survive():
    """§10 keys are kept (Kyo stores them, it doesn't run them)."""
    import_bundle(str(FIXTURES / "acme_retail"))
    computations = [
        r for r in db.query_catalog() if r["type"] == "Attested Computation"
    ]
    assert computations
    for row in computations:
        assert {"runtime", "executor", "attester"} <= set(row["metadata"])


class TestImport:
    def test_kyo_round_trip(self, tmp_path):
        """Store -> bundle -> another store -> bundle: identical files, and
        the same ids, bodies, and typed links in both stores."""
        save("kyo-a", "Alpha", body="Alpha body\n", description="A.", tags=["x"])
        save("kyo-b", "Beta", "Metric")
        save("kyo-c", "Gamma", body="See [beta](../metric/beta.md).\n")
        store.add_link("kyo-a", "kyo-b", "depends_on")
        export_bundle(str(tmp_path / "one"))

        fresh = tmp_path / "device2" / "kyo.db"
        result = import_bundle(str(tmp_path / "one"), db_path=fresh)
        assert result["created"] == 3 and result["broken_links"] == []
        for node_id in ("kyo-a", "kyo-b", "kyo-c"):
            original = db.get_concept_by_id(node_id)
            copy = db.get_concept_by_id(node_id, db_path=fresh)
            for key in (
                "type",
                "title",
                "description",
                "tags",
                "body_text",
                "bundle_path",
            ):
                assert copy[key] == original[key], (node_id, key)
        assert sorted(map(str, db.get_all_links(db_path=fresh))) == sorted(
            map(str, db.get_all_links())
        )
        export_bundle(str(tmp_path / "two"), db_path=fresh)
        assert all_files(tmp_path / "one") == all_files(tmp_path / "two")

    def test_imported_files_are_editable_in_the_store(self, tmp_path):
        import_bundle(str(FIXTURES / "ga4"))
        [row] = [r for r in db.query_catalog() if r["bundle_path"] == "tables/events_"]
        updated = store.update_concept(row["id"], {"title": "Events (edited)"})
        assert updated["title"] == "Events (edited)"
        assert updated["id"] == row["id"]
        assert "kyo_id: " + row["id"] in store.read_concept_file(row["id"])

    def test_conflicting_path_is_skipped(self, tmp_path):
        save("kyo-mine", "X", "t")  # store path t/x
        bundle = make_bundle(tmp_path / "b", {"t/x.md": "---\ntype: t\n---\ntheirs\n"})
        result = import_bundle(bundle)
        assert result["concepts"] == 0
        assert "different concept" in result["skipped"][0]["reason"]
        assert db.get_concept_by_id("kyo-mine")["body_text"] == "# X\n"

    def test_kyo_id_used_elsewhere_is_skipped(self, tmp_path):
        save("kyo-mine", "X", "t")
        bundle = make_bundle(
            tmp_path / "b", {"other/place.md": "---\ntype: t\nkyo_id: kyo-mine\n---\n"}
        )
        result = import_bundle(bundle)
        assert "already in the store" in result["skipped"][0]["reason"]

    def test_same_concept_replaces(self, tmp_path):
        bundle = tmp_path / "b"
        make_bundle(bundle, {"t/x.md": "---\ntype: t\n---\nv1\n"})
        import_bundle(str(bundle))
        make_bundle(bundle, {"t/x.md": "---\ntype: t\n---\nv2\n"})
        result = import_bundle(str(bundle))
        assert result["updated"] == 1
        [row] = db.query_catalog()
        assert row["body_text"] == "v2\n"

    def test_broken_links_are_tolerated(self, tmp_path):
        """§6.1/§11: a broken link isn't malformed; it's kept in the text."""
        bundle = make_bundle(
            tmp_path / "b", {"a.md": "---\ntype: t\n---\n[later](/not-yet.md)\n"}
        )
        result = import_bundle(bundle)
        assert result["broken_links"] == [{"from": "a.md", "to": "/not-yet.md"}]
        assert db.query_catalog()[0]["body_text"] == "[later](/not-yet.md)\n"

    def test_bad_files_are_skipped_not_fatal(self, tmp_path):
        bundle = make_bundle(
            tmp_path / "b",
            {
                "good.md": "---\ntype: t\n---\nok\n",
                "plain.md": "# no frontmatter\n",
                "untyped.md": "---\ntitle: T\n---\n",
                "index.md": "# Index\n",
                ".hidden/x.md": "ignored\n",
            },
        )
        result = import_bundle(bundle)
        assert result["concepts"] == 1
        assert sorted(s["path"] for s in result["skipped"]) == [
            "plain.md",
            "untyped.md",
        ]
        assert not (store.bundle_dir() / "plain.md").exists()

    def test_unknown_types_and_keys_are_accepted(self, tmp_path):
        """§11: consumers must not reject unknown types or extra keys."""
        bundle = make_bundle(
            tmp_path / "b",
            {"a.md": "---\ntype: Something New\nx_custom: {deep: [1]}\n---\n"},
        )
        import_bundle(bundle)
        [row] = db.query_catalog()
        assert row["type"] == "Something New"
        assert row["metadata"] == {"x_custom": {"deep": [1]}}

    def test_not_a_directory(self, tmp_path):
        with pytest.raises(BundleError):
            import_bundle(str(tmp_path / "missing"))


# --- Validation --------------------------------------------------------------


class TestValidate:
    @pytest.mark.parametrize(
        "text, rule",
        [
            ("# no frontmatter\n", "§11.1"),
            ("---\ntype: [unclosed\n---\n", "§11.1"),
            ("---\n- a list\n---\n", "§11.1"),
            ("---\ntitle: no type\n---\n", "§11.2"),
            ("---\ntype: ''\n---\n", "§11.2"),
            ("---\ntype: 42\n---\n", "§11.2"),
        ],
    )
    def test_concept_errors(self, tmp_path, text, rule):
        result = validate_bundle(make_bundle(tmp_path, {"c.md": text}))
        assert not result["conformant"]
        assert [e["rule"] for e in result["errors"]] == [rule]

    def test_minimal_concept_is_conformant(self, tmp_path):
        result = validate_bundle(make_bundle(tmp_path, {"c.md": "---\ntype: t\n---\n"}))
        assert result["conformant"] and result["concepts"] == 1

    def test_index_rules(self, tmp_path):
        root = make_bundle(
            tmp_path,
            {
                "index.md": "---\nokf_version: '0.2'\ntitle: nope\n---\n# A\n",
                "sub/index.md": "---\nokf_version: '0.2'\n---\n# B\n",
                "ok/index.md": "# Fine\n\n* [x](x.md) - y\n",
            },
        )
        errors = validate_bundle(root)["errors"]
        assert {(e["path"], e["rule"]) for e in errors} == {
            ("index.md", "§8"),
            ("sub/index.md", "§8"),
        }

    def test_log_rules(self, tmp_path):
        root = make_bundle(
            tmp_path,
            {
                "log.md": "# Log\n\n## 2026-05-01\n* a\n\n## 2026-06-01\n* b\n",
                "bad/log.md": "# Log\n\n## May 2026\n* a\n\n## 2026-02-30\n* b\n",
            },
        )
        result = validate_bundle(root)
        assert [e["path"] for e in result["errors"]] == ["bad/log.md", "bad/log.md"]
        assert [w["path"] for w in result["warnings"]] == ["log.md"]  # not newest first

    def test_broken_links_are_warnings(self, tmp_path):
        root = make_bundle(tmp_path, {"a.md": "---\ntype: t\n---\n[x](/gone.md)\n"})
        result = validate_bundle(root)
        assert result["conformant"]
        assert [w["rule"] for w in result["warnings"]] == ["§6.1"]

    def test_hidden_directories_are_ignored(self, tmp_path):
        root = make_bundle(
            tmp_path, {".git/x.md": "not okf\n", "a.md": "---\ntype: t\n---\n"}
        )
        assert validate_bundle(root)["conformant"]

    def test_the_store_itself_is_conformant(self):
        save("kyo-a", "Alpha")
        save("kyo-b", "Beta")
        store.add_link("kyo-a", "kyo-b", "depends_on")
        assert validate_bundle(str(store.bundle_dir()))["conformant"]


# --- Interfaces --------------------------------------------------------------


class TestInterfaces:
    @pytest.mark.asyncio
    async def test_mcp_tools(self, tmp_path):
        from kyo_mcp.mcp_server import (
            export_okf_bundle,
            get_kyo_node,
            import_okf_bundle,
            refresh_okf_bundle,
            search_knowledge,
            validate_okf_bundle,
        )
        from mcp.server.mcpserver.exceptions import ToolError

        shutil.copytree(FIXTURES / "ga4", tmp_path / "ga4")
        assert "Imported 9 concept(s)" in await import_okf_bundle(str(tmp_path / "ga4"))
        exported = await export_okf_bundle(str(tmp_path / "out"))
        assert "Exported 9 concept(s)" in exported and "conformant: yes" in exported
        assert "conformant with OKF v0.2" in await validate_okf_bundle(
            str(tmp_path / "out")
        )
        with pytest.raises(ToolError, match="pass overwrite"):
            await export_okf_bundle(str(tmp_path / "out"))

        # A direct edit to a file is seen by the next tool call.
        row = db.find_concept_by_bundle_path("tables/events_")
        path = store.concept_file("tables/events_")
        path.write_text(path.read_text().replace("# ", "# platypus ", 1))
        assert row["id"] in await search_knowledge("platypus")
        assert "platypus" in await get_kyo_node(row["id"])
        assert "Bundle:" in await refresh_okf_bundle()

    @pytest.mark.asyncio
    async def test_cli(self, tmp_path, capsys):
        assert await cli_run(["validate_bundle", str(FIXTURES / "ga4")]) == 0
        assert json.loads(capsys.readouterr().out)["conformant"] is True
        bad = make_bundle(tmp_path / "bad", {"c.md": "# none\n"})
        assert await cli_run(["validate_bundle", bad]) == 1
        assert "conformance error" in capsys.readouterr().err
        assert await cli_run(["import_bundle", str(FIXTURES / "ga4")]) == 0
        assert json.loads(capsys.readouterr().out)["created"] == 9
        assert await cli_run(["export_bundle", str(tmp_path / "out")]) == 0
        assert json.loads(capsys.readouterr().out)["validation"]["conformant"]
        assert await cli_run(["refresh"]) == 0
        assert json.loads(capsys.readouterr().out)["errors"] == []
