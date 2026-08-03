# Kyo Knowledge Catalogue MCP

## What is Kyo?

Kyo is a knowledge catalogue that allows agents to store and query knowledge in a structured way.
It implements the OKF (Open Knowledge Format) specification for knowledge representation and provides a simple API for agents to interact with the knowledge catalogue.

## Why Kyo?

Kyo (経) - Sutra/Scripture
The most fitting reading is 経 (kyō), meaning "sutra" or "scripture" - the canonical texts that carry Buddhist teachings. This maps closely onto what OKF actually is: a bundle of structured markdown documents, each representing one curated "concept" that agents read to gain context, much like how a sutra is a discrete unit of teaching that a practitioner or scholar consults.

### Why It Fits OKF Specifically

OKF's actual structure reinforces this: each file is a single, self-contained concept with YAML frontmatter (metadata) followed by free-form teaching content, and a directory of these files forms a "bundle" that agents consult without needing to re-interpret raw documents each time. That's structurally similar to how sutras function as discrete, canonical teaching units that can be read independently or as part of a larger collection (a "canon" or "bundle" of scripture).

Sources:
Kyo: [Kyo](https://jitenon.com/word/6947)
OKF: [Knowledge Catalog - OKF](https://github.com/GoogleCloudPlatform/knowledge-catalog/tree/main/okf)
OKF Spec: [OKF Specification](./docs/OKF.md)
