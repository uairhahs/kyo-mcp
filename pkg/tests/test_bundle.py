"""OKF v0.2 bundle export, import, and validation. Section references (§)
are to docs/SPEC.md. The fixtures under tests/fixtures/okf/ are Google's
sample bundles, copied unmodified (see docs/THIRD_PARTY.md)."""

import json
import shutil
from pathlib import Path

import pytest
from kyo_mcp.bundle import (
    EXPORT_MANIFEST,
    KYO_LINKS_KEY,
    LINKS_START,
    BundleError,
    append_links_block,
    assign_bundle_paths,
    body_link_targets,
    export_bundle,
    import_bundle,
    resolve_link,
    strip_links_block,
    validate_bundle,
)
from kyo_mcp.cli import run as cli_run
from kyo_mcp.database import (
    create_concept,
    create_link,
    get_all_links,
    get_concept_by_id,
    get_links,
    query_catalog,
)
from kyo_mcp.okf_schema import parse_frontmatter

FIXTURES = Path(__file__).parent / "fixtures" / "okf"
UPSTREAM_BUNDLES = ["acme_retail", "ga4"]


def node(node_id, title, type_="concept", body=None, **extra):
    create_concept(
        {"id": node_id, "type": type_, "title": title, **extra},
        markdown_body=body if body is not None else f"# {title}\n",
    )


def concept_files(root: Path):
    return {
        p.relative_to(root).as_posix(): p
        for p in root.rglob("*.md")
        if p.name not in ("index.md", "log.md")
    }


def read(path: Path):
    return parse_frontmatter(path.read_text(encoding="utf-8"))


# --- Paths and links ---------------------------------------------------------


class TestPaths:
    def test_type_and_title(self):
        paths = assign_bundle_paths(
            [{"id": "kyo-1", "type": "BigQuery Table", "title": "Customer Orders"}]
        )
        assert paths == {"kyo-1": "bigquery-table/customer-orders"}

    def test_collisions_are_disambiguated(self):
        concepts = [{"id": f"kyo-{i}", "type": "t", "title": "Same"} for i in range(3)]
        paths = assign_bundle_paths(concepts)
        assert len(set(paths.values())) == 3

    @pytest.mark.parametrize("title", ["index", "log", "INDEX"])
    def test_reserved_names_are_never_used(self, title):
        """§3.1: index.md and log.md must not be concept documents."""
        path = assign_bundle_paths([{"id": "kyo-abc", "type": "t", "title": title}])[
            "kyo-abc"
        ]
        assert path.split("/")[-1] not in ("index", "log")

    def test_stored_path_is_kept(self):
        paths = assign_bundle_paths(
            [
                {"id": "a", "type": "t", "title": "New", "bundle_path": "kept/here"},
                {"id": "b", "type": "kept", "title": "Here"},
            ]
        )
        assert paths["a"] == "kept/here"
        assert paths["b"] != "kept/here"

    def test_untitled_falls_back_to_id(self):
        assert (
            assign_bundle_paths([{"id": "kyo-xyz", "type": "t"}])["kyo-xyz"]
            == "t/kyo-xyz"
        )


class TestRelativeLink:
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
        from kyo_mcp.bundle import relative_link

        href = relative_link(target, source)
        assert href == expected
        assert resolve_link(href, source) == target


class TestLinks:
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

    def test_links_in_code_and_images_are_ignored(self):
        body = (
            "See [a](/a.md) and ![img](/img.md).\n\n"
            "```\n[not](/code.md)\n```\n\n`[inline](/inline.md)`\n"
            '[titled](/b.md "Title")\n'
        )
        assert body_link_targets(body) == ["/a.md", "/b.md"]

    @pytest.mark.parametrize("body", ["text\n", "text", "", "a\n\nb\n\n"])
    def test_links_block_round_trips(self, body):
        with_block = append_links_block(body, ["- references: [B](/b.md)"])
        expected = body if body.endswith("\n") else body + "\n"
        assert strip_links_block(with_block) == expected


# --- Export ------------------------------------------------------------------


class TestExport:
    def test_empty_catalogue(self, tmp_path):
        result = export_bundle(str(tmp_path / "b"))
        assert result["concepts"] == 0
        assert result["validation"]["conformant"]
        frontmatter, _ = read(tmp_path / "b" / "index.md")
        assert frontmatter == {"okf_version": "0.2"}

    def test_export_is_conformant(self, tmp_path):
        node("kyo-a", "Alpha", "Playbook", description="First.")
        node("kyo-b", "Beta", "Metric")
        result = export_bundle(str(tmp_path / "b"))
        assert result["validation"] == {
            "conformant": True,
            "okf_version": "0.2",
            "concepts": 2,
            "errors": [],
            "warnings": [],
        }
        files = concept_files(tmp_path / "b")
        assert set(files) == {"playbook/alpha.md", "metric/beta.md"}
        frontmatter, body = read(files["playbook/alpha.md"])
        assert frontmatter["type"] == "Playbook"
        assert frontmatter["kyo_id"] == "kyo-a"
        assert body == "# Alpha\n"

    def test_index_files(self, tmp_path):
        """§8: every directory gets an index; only the root has frontmatter,
        and entries carry the concept's description."""
        node("kyo-a", "Alpha", "Playbook", description="First line.\nSecond.")
        export_bundle(str(tmp_path / "b"))
        root_fm, root_body = read(tmp_path / "b" / "index.md")
        assert root_fm == {"okf_version": "0.2"}
        assert "* [playbook](playbook/)" in root_body
        sub_fm, sub_body = read(tmp_path / "b" / "playbook" / "index.md")
        assert sub_fm is None
        assert "* [Alpha](alpha.md) - First line. Second." in sub_body

    def test_paths_are_stable_across_exports(self, tmp_path):
        node("kyo-a", "Alpha")
        first = export_bundle(str(tmp_path / "one"))
        create_concept(
            {"id": "kyo-a", "type": "concept", "title": "Renamed"}, markdown_body="x\n"
        )
        second = export_bundle(str(tmp_path / "two"))
        assert first["paths_assigned"] == 1
        assert second["paths_assigned"] == 0
        assert set(concept_files(tmp_path / "two")) == {"concept/alpha.md"}

    def test_assigning_paths_keeps_updated_at(self, tmp_path):
        node("kyo-a", "Alpha")
        before = get_concept_by_id("kyo-a")["updated_at"]
        export_bundle(str(tmp_path / "b"))
        after = get_concept_by_id("kyo-a")
        assert after["updated_at"] == before
        assert after["bundle_path"] == "concept/alpha"

    def test_refuses_a_foreign_directory(self, tmp_path):
        (tmp_path / "b").mkdir()
        (tmp_path / "b" / "notes.md").write_text("mine\n")
        with pytest.raises(BundleError, match="not a previous Kyo export"):
            export_bundle(str(tmp_path / "b"), overwrite=True)
        assert (tmp_path / "b" / "notes.md").read_text() == "mine\n"

    def test_overwrite_needs_the_flag(self, tmp_path):
        export_bundle(str(tmp_path / "b"))
        with pytest.raises(BundleError, match="pass overwrite"):
            export_bundle(str(tmp_path / "b"))

    def test_overwrite_replaces_only_its_own_files(self, tmp_path):
        node("kyo-a", "Alpha")
        node("kyo-b", "Beta", "Other")
        export_bundle(str(tmp_path / "b"))
        (tmp_path / "b" / "README.txt").write_text("keep me\n")
        from kyo_mcp.database import delete_concept

        delete_concept("kyo-b")
        export_bundle(str(tmp_path / "b"), overwrite=True)
        assert set(concept_files(tmp_path / "b")) == {"concept/alpha.md"}
        assert not (tmp_path / "b" / "other").exists()
        assert (tmp_path / "b" / "README.txt").read_text() == "keep me\n"
        assert validate_bundle(str(tmp_path / "b"))["conformant"]

    def test_manifest(self, tmp_path):
        node("kyo-a", "Alpha")
        export_bundle(str(tmp_path / "b"))
        manifest = json.loads((tmp_path / "b" / EXPORT_MANIFEST).read_text())
        assert manifest["okf_version"] == "0.2"
        assert manifest["files"] == ["concept/alpha.md", "concept/index.md", "index.md"]

    def test_typed_links(self, tmp_path):
        """Typed links go in `kyo_links` and, for plain OKF consumers, as a
        markdown link in the generated section (§6.1)."""
        node("kyo-a", "Alpha")
        node("kyo-b", "Beta")
        create_link("kyo-a", "kyo-b", "depends_on")
        export_bundle(str(tmp_path / "b"))
        frontmatter, body = read(tmp_path / "b" / "concept" / "alpha.md")
        assert frontmatter[KYO_LINKS_KEY] == [
            {"rel": "depends_on", "target": "/concept/beta.md"}
        ]
        assert "- depends_on: [Beta](./beta.md)" in body
        assert body.startswith("# Alpha\n\n" + LINKS_START)

    def test_inline_references_are_not_repeated(self, tmp_path):
        node("kyo-b", "Beta")
        node(
            "kyo-a",
            "Alpha",
            body="See [beta](/concept/beta.md).\n",
            bundle_path="concept/alpha",
        )
        create_concept(
            {**get_concept_by_id("kyo-b"), "bundle_path": "concept/beta"},
            markdown_body="# Beta\n",
        )
        create_link("kyo-a", "kyo-b", "references")
        export_bundle(str(tmp_path / "b"))
        frontmatter, body = read(tmp_path / "b" / "concept" / "alpha.md")
        assert KYO_LINKS_KEY not in frontmatter
        assert body == "See [beta](/concept/beta.md).\n"

    def test_typed_link_already_in_body_has_no_block(self, tmp_path):
        node("kyo-b", "Beta", bundle_path="concept/beta")
        node("kyo-a", "Alpha", body="Needs [beta](/concept/beta.md).\n")
        create_link("kyo-a", "kyo-b", "depends_on")
        export_bundle(str(tmp_path / "b"))
        frontmatter, body = read(tmp_path / "b" / "concept" / "alpha.md")
        assert frontmatter[KYO_LINKS_KEY] == [
            {"rel": "depends_on", "target": "/concept/beta.md"}
        ]
        assert LINKS_START not in body


# --- Import ------------------------------------------------------------------


@pytest.mark.parametrize("bundle", UPSTREAM_BUNDLES)
class TestUpstreamInterop:
    """Google's own sample bundles, through Kyo and back out."""

    def test_upstream_bundle_is_conformant(self, bundle):
        result = validate_bundle(str(FIXTURES / bundle))
        assert result["conformant"], result["errors"]

    def test_import_export_preserves_every_concept(self, bundle, tmp_path):
        source = FIXTURES / bundle
        result = import_bundle(str(source))
        assert result["skipped"] == [] and result["broken_links"] == []
        exported = export_bundle(str(tmp_path / "out"))
        assert exported["validation"]["conformant"]
        assert exported["paths_assigned"] == 0  # every path came from the bundle

        original, written = concept_files(source), concept_files(tmp_path / "out")
        assert set(written) == set(original)
        for rel, path in original.items():
            before, body_before = read(path)
            after, body_after = read(written[rel])
            assert body_after == body_before, rel
            if isinstance(before.get("verified"), dict):
                before["verified"] = [before["verified"]]  # §5.2
            before.setdefault("status", "stable")  # §5.4
            assert {k: v for k, v in after.items() if k != "kyo_id"} == before, rel

    def test_links_become_graph_edges(self, bundle):
        import_bundle(str(FIXTURES / bundle))
        expected = 0
        ids = {c["bundle_path"]: c["id"] for c in query_catalog()}
        edges = set()
        for rel, path in concept_files(FIXTURES / bundle).items():
            _, body = read(path)
            for href in body_link_targets(body):
                target = resolve_link(href, rel[:-3])
                if target in ids:
                    edges.add((ids[rel[:-3]], ids[target]))
        expected = len(edges)
        assert expected > 0
        assert len(get_all_links()) == expected

    def test_reimport_is_idempotent(self, bundle):
        first = import_bundle(str(FIXTURES / bundle))
        before = sorted((c["id"], c["bundle_path"]) for c in query_catalog())
        links_before = sorted(map(str, get_all_links()))
        second = import_bundle(str(FIXTURES / bundle))
        assert first["created"] == first["concepts"] and second["created"] == 0
        assert second["updated"] == second["concepts"]
        assert sorted((c["id"], c["bundle_path"]) for c in query_catalog()) == before
        assert sorted(map(str, get_all_links())) == links_before


def test_attested_computation_fields_survive(tmp_path):
    """§10 keys are carried as extensions (Kyo stores, doesn't run them)."""
    import_bundle(str(FIXTURES / "acme_retail"))
    export_bundle(str(tmp_path / "out"))
    computations = [
        fm
        for fm, _ in (read(p) for p in concept_files(tmp_path / "out").values())
        if fm["type"] == "Attested Computation"
    ]
    assert computations
    for frontmatter in computations:
        assert {"runtime", "executor", "attester"} <= set(frontmatter)


class TestImport:
    def test_kyo_round_trip(self, tmp_path):
        """A Kyo catalogue exported and imported into a fresh database comes
        back with the same ids, bodies, and typed links, and re-exports to
        identical files."""
        node("kyo-a", "Alpha", body="Alpha body\n", description="A.", tags=["x"])
        node("kyo-b", "Beta", "Metric")
        node(
            "kyo-c",
            "Gamma",
            body="See [beta](/metric/beta.md).\n",
            bundle_path="notes/gamma",
        )
        create_concept(
            {**get_concept_by_id("kyo-b"), "bundle_path": "metric/beta"},
            markdown_body="# Beta\n",
        )
        create_link("kyo-a", "kyo-b", "depends_on")
        create_link("kyo-c", "kyo-b", "references")
        export_bundle(str(tmp_path / "one"))

        fresh = tmp_path / "fresh.db"
        result = import_bundle(str(tmp_path / "one"), db_path=fresh)
        assert result["created"] == 3 and result["broken_links"] == []
        for node_id in ("kyo-a", "kyo-b", "kyo-c"):
            original = get_concept_by_id(node_id)
            copy = get_concept_by_id(node_id, db_path=fresh)
            for key in (
                "type",
                "title",
                "description",
                "tags",
                "body_text",
                "bundle_path",
            ):
                assert copy[key] == original[key], (node_id, key)
        assert sorted(map(str, get_all_links(db_path=fresh))) == sorted(
            map(str, get_all_links())
        )

        export_bundle(str(tmp_path / "two"), db_path=fresh)
        files = lambda root: {  # noqa: E731
            p.relative_to(root).as_posix(): p.read_bytes()
            for p in root.rglob("*")
            if p.is_file()
        }
        # Every file, including index.md files and the manifest.
        assert files(tmp_path / "one") == files(tmp_path / "two")

    def test_identity_by_path_without_kyo_id(self, tmp_path):
        bundle = tmp_path / "b"
        (bundle / "t").mkdir(parents=True)
        (bundle / "t" / "x.md").write_text("---\ntype: t\ntitle: X\n---\nv1\n")
        first = import_bundle(str(bundle))
        (bundle / "t" / "x.md").write_text("---\ntype: t\ntitle: X\n---\nv2\n")
        second = import_bundle(str(bundle))
        assert first["created"] == 1 and second["updated"] == 1
        [concept] = query_catalog()
        assert concept["body_text"] == "v2\n" and concept["bundle_path"] == "t/x"

    def test_broken_links_are_tolerated(self, tmp_path):
        """§6.1/§11: a broken link isn't malformed; it's kept in the text."""
        bundle = tmp_path / "b"
        bundle.mkdir()
        (bundle / "a.md").write_text("---\ntype: t\n---\nSee [later](/not-yet.md).\n")
        result = import_bundle(str(bundle))
        assert result["broken_links"] == [{"from": "a.md", "to": "/not-yet.md"}]
        [concept] = query_catalog()
        assert concept["body_text"] == "See [later](/not-yet.md).\n"

    def test_bad_files_are_skipped_not_fatal(self, tmp_path):
        bundle = tmp_path / "b"
        bundle.mkdir()
        (bundle / "good.md").write_text("---\ntype: t\n---\nok\n")
        (bundle / "plain.md").write_text("# no frontmatter\n")
        (bundle / "untyped.md").write_text("---\ntitle: T\n---\n")
        (bundle / "index.md").write_text("# Index\n")
        (bundle / ".hidden").mkdir()
        (bundle / ".hidden" / "x.md").write_text("ignored\n")
        result = import_bundle(str(bundle))
        assert result["concepts"] == 1
        assert sorted(s["path"] for s in result["skipped"]) == [
            "plain.md",
            "untyped.md",
        ]

    def test_unknown_types_and_keys_are_accepted(self, tmp_path):
        """§11: consumers must not reject unknown types or extra keys."""
        bundle = tmp_path / "b"
        bundle.mkdir()
        (bundle / "a.md").write_text(
            "---\ntype: Something New\nx_custom: {deep: [1]}\n---\n"
        )
        import_bundle(str(bundle))
        [concept] = query_catalog()
        assert concept["type"] == "Something New"
        assert concept["metadata"] == {"x_custom": {"deep": [1]}}

    def test_reimport_replaces_removed_links(self, tmp_path):
        bundle = tmp_path / "b"
        bundle.mkdir()
        (bundle / "b.md").write_text("---\ntype: t\n---\n")
        (bundle / "a.md").write_text("---\ntype: t\n---\n[b](b.md)\n")
        import_bundle(str(bundle))
        assert len(get_all_links()) == 1
        (bundle / "a.md").write_text("---\ntype: t\n---\nno links now\n")
        import_bundle(str(bundle))
        assert get_all_links() == []

    def test_typed_links_from_kyo_links(self, tmp_path):
        bundle = tmp_path / "b"
        bundle.mkdir()
        (bundle / "b.md").write_text("---\ntype: t\n---\n")
        (bundle / "a.md").write_text(
            "---\ntype: t\nkyo_links:\n- {rel: depends_on, target: /b.md}\n"
            "- {rel: x, target: /missing.md}\n- not-a-mapping\n---\n"
        )
        result = import_bundle(str(bundle))
        a = next(c for c in query_catalog() if c["bundle_path"] == "a")
        assert [link["relation_type"] for link in get_links(a["id"], "out")] == [
            "depends_on"
        ]
        assert result["broken_links"] == [{"from": "a.md", "to": "/missing.md"}]
        assert KYO_LINKS_KEY not in a["metadata"]

    def test_not_a_directory(self, tmp_path):
        with pytest.raises(BundleError):
            import_bundle(str(tmp_path / "missing"))


# --- Validation --------------------------------------------------------------


def make_bundle(root: Path, files):
    for rel, text in files.items():
        path = root / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")
    return str(root)


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


# --- Interfaces --------------------------------------------------------------


class TestInterfaces:
    @pytest.mark.asyncio
    async def test_mcp_tools(self, tmp_path):
        from kyo_mcp.mcp_server import (
            export_okf_bundle,
            import_okf_bundle,
            validate_okf_bundle,
        )
        from mcp.server.mcpserver.exceptions import ToolError

        shutil.copytree(FIXTURES / "ga4", tmp_path / "ga4")
        imported = await import_okf_bundle(str(tmp_path / "ga4"))
        assert "Imported 9 concept(s)" in imported
        exported = await export_okf_bundle(str(tmp_path / "out"))
        assert "Exported 9 concept(s)" in exported and "conformant: yes" in exported
        assert "conformant with OKF v0.2" in await validate_okf_bundle(
            str(tmp_path / "out")
        )
        with pytest.raises(ToolError, match="pass overwrite"):
            await export_okf_bundle(str(tmp_path / "out"))

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
