"""Round-trip and OKF v0.2 conformance tests for OKFConcept's markdown
serialization. Section references (§) are to docs/SPEC.md."""

import re

import pytest
import yaml
from kyo_mcp.common import GENERATOR
from kyo_mcp.database import create_concept, get_concept_by_id
from kyo_mcp.okf_schema import (
    KYO_ID_KEY,
    OKFConcept,
    _OKFLoader,
    normalize_instant,
    trust_tier,
)
from kyo_mcp.ontology import OntologyAnnotation
from pydantic import ValidationError

# Example documents copied from docs/SPEC.md §4.3, §4.4 and §10.2.
SPEC_RESOURCE_CONCEPT = """\
---
type: BigQuery Table
title: Customer Orders
description: One row per completed customer order across all channels.
resource: https://console.cloud.google.com/bigquery?p=acme&d=sales&t=orders
tags: [sales, orders, revenue]
generated: { by: reference_agent/gemini-2.5-pro, at: 2026-05-28T14:30:00Z }
---

# Schema

| Column        | Type      | Description                              |
|---------------|-----------|------------------------------------------|
| `order_id`    | STRING    | Globally unique order identifier.        |
| `customer_id` | STRING    | Foreign key into [customers](/tables/customers.md). |

# Joins

Joined with [customers](/tables/customers.md) on `customer_id`.
"""

SPEC_PLAYBOOK = """\
---
type: Playbook
title: "Incident response: data freshness alert"
description: Steps to triage a freshness alert on the orders pipeline.
tags: [oncall, incident]
generated: { by: human:ahormati, at: 2026-04-12T09:00:00Z }
---

# Trigger

A freshness alert fires when `orders` lags more than 30 minutes behind its
expected SLA. See the [orders table](/tables/orders.md).
"""

SPEC_ATTESTED_COMPUTATION = """\
---
type: Attested Computation
title: Revenue for fiscal year
description: Recognized revenue for a fiscal year, per Finance's definition.
status: stable
runtime: bigquery
parameters:
  - { name: year, type: integer, required: true }
executor:
  resource: references/skills/run-on-bq.md
  receipt: [job_id, executed_sql, result]
attester:
  resource: references/attesters/revenue.py
generated: { by: reference_agent/gemini-2.5-pro, at: 2026-06-20T22:53:05Z }
verified: { by: human:ahormati, at: 2026-06-25T09:00:00Z }
stale_after: 2026-09-23T00:00:00Z
sources:
  - id: rev-policy
    resource: https://wiki.acme/finance/revenue-recognition
    title: Revenue recognition policy
    usage_count: 5000
    last_modified: 2026-05-30T00:00:00Z
usage_window: { from: 2026-06-01T00:00:00Z, to: 2026-06-30T00:00:00Z }
---

# Computation

    SELECT SUM(amount) AS revenue
    FROM finance.recognized_revenue
    WHERE fiscal_year = @year

The computation binds only the declared `parameters`.[^rev-policy]

[^rev-policy]: Revenue recognition policy
"""

SPEC_DOCUMENTS = [SPEC_RESOURCE_CONCEPT, SPEC_PLAYBOOK, SPEC_ATTESTED_COMPUTATION]


def _frontmatter(text: str) -> dict:
    return yaml.load(text.split("---\n")[1], Loader=_OKFLoader)


def _full_concept() -> OKFConcept:
    """A Kyo concept exercising every field, including nested extensions."""
    return OKFConcept.model_validate(
        {
            "id": "kyo-0123456789ab",
            "type": "troubleshooting",
            "title": "Wi-Fi drops: “LOW_ACK” on taizo",
            "description": "Deauthenticated after 1.5 s; see body.",
            "resource": "https://example.com/runbook",
            "tags": ["network", "wifi"],
            "status": "draft",
            "stale_after": "2027-01-01T00:00:00+01:00",
            "generated": {"by": GENERATOR, "at": "2026-11-07T12:00:00Z"},
            "verified": [
                {"by": "human:suddin", "at": "2026-11-07T13:00:00Z"},
                {"by": "process:nightly", "at": "2026-11-08T02:00:00Z", "run": 7},
            ],
            "sources": [
                {
                    "id": "journal",
                    "resource": "journalctl -b -1",
                    "author": "process:journald",
                    "usage_count": 3,
                    "last_modified": "2026-11-07T12:18:01Z",
                    "excerpt_lines": [12, 40],
                }
            ],
            "ontology": {"scheme": "SKOS", "concept_uri": "skos:Concept"},
            "metadata": {
                "device": "taizo",
                "usage_window": {
                    "from": "2026-11-01T00:00:00Z",
                    "to": "2026-11-07T00:00:00Z",
                },
                "nested": {"list": [1, "two", {"three": None}], "flag": True},
            },
        }
    )


BODY = "# Wi-Fi drops\n\nSee [the dock](/hardware/echo-20.md).\n\n- one\n- two\n"


class TestRoundTrip:
    def test_full_concept_round_trips(self):
        concept = _full_concept()
        parsed, body = OKFConcept.from_markdown(concept.to_markdown(BODY))
        assert parsed == concept
        assert body == BODY

    def test_serialization_is_stable(self):
        """A second pass produces byte-identical text."""
        text = _full_concept().to_markdown(BODY)
        parsed, body = OKFConcept.from_markdown(text)
        assert parsed.to_markdown(body) == text

    def test_generated_body_round_trips(self):
        concept = OKFConcept(type="concept", title="T", description="D")
        parsed, body = OKFConcept.from_markdown(concept.to_markdown())
        assert parsed == concept
        assert body == "# T\n\nD\n"

    @pytest.mark.parametrize("document", SPEC_DOCUMENTS)
    def test_spec_examples_round_trip(self, document):
        concept, body = OKFConcept.from_markdown(document)
        reparsed, rebody = OKFConcept.from_markdown(concept.to_markdown(body))
        assert reparsed == concept
        assert rebody == body

    @pytest.mark.parametrize("document", SPEC_DOCUMENTS)
    def test_spec_examples_keep_every_key(self, document):
        """Kyo rewrites flow-style YAML in block style, but every key and
        value survives. The only changes are equivalences the spec defines:
        a bare `verified` mapping is written as a one-element list (§5.2),
        and an absent `status` is written as `stable` (§5.4)."""
        original = _frontmatter(document)
        concept, body = OKFConcept.from_markdown(document)
        written = _frontmatter(concept.to_markdown(body))
        if isinstance(original.get("verified"), dict):
            original["verified"] = [original["verified"]]
        original.setdefault("status", "stable")
        assert written == original

    def test_persisted_concept_round_trips(self):
        """Through the database and back out to markdown, losslessly."""
        concept = _full_concept()
        create_concept(concept.model_dump(mode="python"), markdown_body=BODY)
        row = get_concept_by_id(concept.id)
        stored = OKFConcept.model_validate(row)
        assert stored == concept
        assert stored.to_markdown(row["body_text"]) == concept.to_markdown(BODY)

    def test_persisted_unverified_concept_round_trips(self):
        """Regression: the database stores no verification as [], which
        used to come back from markdown as None and break equality."""
        create_concept(
            {"id": "kyo-plain", "type": "concept", "title": "Plain"},
            markdown_body="# Plain\n",
        )
        row = get_concept_by_id("kyo-plain")
        stored = OKFConcept.model_validate(row)
        parsed, body = OKFConcept.from_markdown(stored.to_markdown(row["body_text"]))
        assert parsed == stored
        assert body == "# Plain\n"


class TestConsumerRules:
    def test_bare_verified_mapping_is_a_list(self):
        """§5.2 MUST: a bare mapping is a one-element list."""
        concept, _ = OKFConcept.from_markdown(SPEC_ATTESTED_COMPUTATION)
        assert [v.by for v in concept.verified] == ["human:ahormati"]

    def test_resource_is_a_bare_uri(self):
        concept, body = OKFConcept.from_markdown(SPEC_RESOURCE_CONCEPT)
        assert concept.resource == {
            "uri": "https://console.cloud.google.com/bigquery?p=acme&d=sales&t=orders"
        }
        assert _frontmatter(concept.to_markdown(body))["resource"].startswith(
            "https://"
        )

    def test_unknown_keys_are_preserved(self):
        """§4.1: unknown keys survive, here the §10 computation fields."""
        concept, _ = OKFConcept.from_markdown(SPEC_ATTESTED_COMPUTATION)
        assert concept.metadata["runtime"] == "bigquery"
        assert concept.metadata["parameters"] == [
            {"name": "year", "type": "integer", "required": True}
        ]
        assert concept.metadata["executor"]["receipt"] == [
            "job_id",
            "executed_sql",
            "result",
        ]
        assert concept.metadata["usage_window"]["from"] == "2026-06-01T00:00:00Z"

    def test_source_credibility_signals_are_kept(self):
        """§5.1 per-source signals, plus an unknown per-source key."""
        concept = _full_concept()
        source = concept.sources[0]
        assert source.usage_count == 3
        assert source.last_modified == "2026-11-07T12:18:01Z"
        assert source.model_dump(exclude_none=True)["excerpt_lines"] == [12, 40]

    def test_timestamps_stay_strings(self):
        """Unquoted YAML timestamps are kept as written, not made datetimes."""
        concept, _ = OKFConcept.from_markdown(SPEC_ATTESTED_COMPUTATION)
        assert concept.generated.at == "2026-06-20T22:53:05Z"
        assert concept.stale_after == "2026-09-23T00:00:00Z"
        assert concept.sources[0].last_modified == "2026-05-30T00:00:00Z"

    def test_absent_status_is_stable(self):
        concept, _ = OKFConcept.from_markdown("---\ntype: t\nstatus:\n---\n")
        assert concept.status == "stable"

    def test_single_tag_string_is_a_list(self):
        concept, _ = OKFConcept.from_markdown("---\ntype: t\ntags: solo\n---\n")
        assert concept.tags == ["solo"]

    def test_only_type_is_required(self):
        """§11: a concept carrying just `type` is conformant."""
        concept, body = OKFConcept.from_markdown("---\ntype: Metric\n---\n")
        assert concept.type == "Metric" and body == ""

    def test_missing_type_is_rejected(self):
        with pytest.raises(ValidationError):
            OKFConcept.from_markdown("---\ntitle: No type\n---\nbody\n")

    def test_no_frontmatter_is_rejected(self):
        with pytest.raises(ValueError, match="no frontmatter"):
            OKFConcept.from_markdown("# Just markdown\n")

    def test_non_mapping_frontmatter_is_rejected(self):
        with pytest.raises(ValueError, match="mapping"):
            OKFConcept.from_markdown("---\n- a\n- b\n---\n")

    def test_bom_and_crlf(self):
        text = "\ufeff---\r\ntype: t\r\ntitle: CRLF\r\n---\r\nbody\r\n"
        concept, body = OKFConcept.from_markdown(text)
        assert concept.title == "CRLF"
        assert body == "body\r\n"

    def test_closing_fence_inside_body_is_body(self):
        concept, body = OKFConcept.from_markdown("---\ntype: t\n---\nA\n---\nB\n")
        assert body == "A\n---\nB\n"


class TestProducerRules:
    def test_kyo_id_is_an_extension_key(self):
        """OKF's concept ID is the file path (§2), so Kyo's id is written
        as an extension key, not `id`."""
        frontmatter = _frontmatter(_full_concept().to_markdown(BODY))
        assert frontmatter[KYO_ID_KEY] == "kyo-0123456789ab"
        assert "id" not in frontmatter

    def test_ontology_is_written(self):
        frontmatter = _frontmatter(_full_concept().to_markdown(BODY))
        assert frontmatter["ontology"] == {
            "scheme": "SKOS",
            "concept_uri": "skos:Concept",
        }

    def test_extensions_cannot_shadow_standard_keys(self):
        concept = OKFConcept(
            id="kyo-x",
            type="real",
            metadata={"type": "fake", KYO_ID_KEY: "fake", "extra": 1},
        )
        frontmatter = _frontmatter(concept.to_markdown())
        assert frontmatter["type"] == "real"
        assert frontmatter[KYO_ID_KEY] == "kyo-x"
        assert frontmatter["extra"] == 1

    def test_generator_follows_actor_convention(self):
        """§7: tools are `<producer>/<version>`; `process:` is for processes."""
        assert re.fullmatch(r"kyo-mcp/[\w.+-]+", GENERATOR)

    def test_ontology_annotation_type(self):
        assert isinstance(_full_concept().ontology, OntologyAnnotation)


class TestNormalizeInstant:
    @pytest.mark.parametrize(
        "value, expected",
        [
            ("2030-01-01", "2030-01-01T00:00:00Z"),
            ("2030-01-01T12:30:00", "2030-01-01T12:30:00Z"),
            ("2030-01-01T12:30:00Z", "2030-01-01T12:30:00Z"),
            ("2030-01-01T12:30:00+02:00", "2030-01-01T12:30:00+02:00"),
            ("not a date", "not a date"),
            (None, None),
        ],
    )
    def test_cases(self, value, expected):
        assert normalize_instant(value) == expected

    def test_applied_to_every_timestamp(self):
        concept = OKFConcept.model_validate(
            {
                "type": "t",
                "stale_after": "2030-01-01",
                "generated": {"by": GENERATOR, "at": "2026-01-01"},
                "verified": {"by": "human:a", "at": "2026-01-02"},
                "sources": [{"resource": "r", "last_modified": "2026-01-03"}],
            }
        )
        assert concept.stale_after == "2030-01-01T00:00:00Z"
        assert concept.generated.at == "2026-01-01T00:00:00Z"
        assert concept.verified[0].at == "2026-01-02T00:00:00Z"
        assert concept.sources[0].last_modified == "2026-01-03T00:00:00Z"


class TestTrustTier:
    @pytest.mark.parametrize(
        "verified, tier",
        [
            (None, "Unverified"),
            ([], "Unverified"),
            ([{"by": "reference_agent/gemini-2.5-pro"}], "Machine-Confirmed"),
            ([{"by": "process:nightly"}], "Machine-Confirmed"),
            ([{"by": "process:nightly"}, {"by": "human:alice"}], "Human-Reviewed"),
            ({"by": "human:alice"}, "Human-Reviewed"),
        ],
    )
    def test_tiers(self, verified, tier):
        """§5.3: any non-human verifier is machine-confirmed."""
        assert trust_tier(verified) == tier
