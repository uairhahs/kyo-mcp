# Third-party material

Files copied unmodified from
[GoogleCloudPlatform/knowledge-catalog](https://github.com/GoogleCloudPlatform/knowledge-catalog),
licensed under the Apache License 2.0 (copy: [LICENSE.knowledge-catalog](LICENSE.knowledge-catalog)).

| Path in this repo                     | Upstream path                                 | Upstream commit                            |
| ------------------------------------- | --------------------------------------------- | ------------------------------------------ |
| `docs/SPEC.md`                        | `okf/SPEC.md` (OKF v0.2 specification)        | `62432a095456147ee71e70ac6e4dc0d2dea3ac30` |
| `pkg/tests/fixtures/okf/acme_retail/` | `okf/bundles/acme_retail/` (`.md` files only) | `4808f91817fc384934c657db1c9ba1e84d8e726e` |
| `pkg/tests/fixtures/okf/ga4/`         | `okf/bundles/ga4/` (`.md` files only)         | `4808f91817fc384934c657db1c9ba1e84d8e726e` |

The sample bundles are used as interoperability fixtures: Kyo's tests
import them, export them again, and check the result against the
originals. They are excluded from linting so they stay byte-identical to
upstream.
