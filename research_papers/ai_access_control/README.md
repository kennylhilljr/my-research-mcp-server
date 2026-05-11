# AI Access Control Research Set

Seeded from the MDPI Informatics article:

- SAFE-GUARD: Semantic Access Control Framework Employing Generative User Assessment and Rule Decisions
- DOI: https://doi.org/10.3390/informatics13010001
- Article: https://www.mdpi.com/2227-9709/13/1/1

This folder is an import-ready source list for indexing into `my-research-mcp-server`.

## Status

- The Codex session could access the article through web search.
- The configured Codex MCP list did not expose `my-research-mcp-server` directly.
- The repo's native indexing path is `server.py` (`download_paper`, `download_paper_by_doi`, `index_paper`, `query_papers`).
- Direct `curl` downloads from the earlier sandbox attempt failed due network/host restrictions and MDPI bot protection.

## Relevant Sources Found

| Priority | Source | Why It Is Relevant | URL |
| --- | --- | --- | --- |
| 1 | SAFE-GUARD: Semantic Access Control Framework Employing Generative User Assessment and Rule Decisions | The linked paper. Uses RAG/LLMs for behavior-aware, explainable healthcare access control and compares against RBAC/ABAC. | https://www.mdpi.com/2227-9709/13/1/1 |
| 1 | RAGent: Retrieval-based Access Control Policy Generation | Directly cited by SAFE-GUARD. Uses RAG for access-control policy generation from high-level requirements. | https://arxiv.org/abs/2409.07489 |
| 1 | LMN: Generating Machine-Enforceable Policies from Natural Language Access Control Rules using LLMs | Directly cited by SAFE-GUARD. Uses LLMs to convert natural-language access-control rules into enforceable ABAC-style policies. | https://arxiv.org/abs/2502.12460 |
| 1 | Intent-Based Access Control: Using LLMs to Intelligently Manage Access Control | Directly cited by SAFE-GUARD as LLM4AC. Converts natural-language access-control matrices into RBAC rules. | https://arxiv.org/abs/2402.07332 |
| 1 | Security Policy Generation and Verification Through Large Language Models | Directly cited by SAFE-GUARD. Proposes translating requirements into policy languages such as XACML/Rego with verification. | https://doi.org/10.1145/3626232.3658635 |
| 1 | A Database-Independent LLM Framework for Real-Time Authorization in Retrieval-Augmented Generation | Closely related. Focuses on real-time authorization for RAG systems handling sensitive data. | https://openreview.net/forum?id=gdgGCQJd9X |
| 1 | Permission-Aware RAG: IAM-Based Access Filtering in Multi-Resource Environments | Closely related. Proposes RAG retrieval that respects provider-controlled IAM across multiple resources. | https://snu.elsevierpure.com/en/publications/permission-aware-rag-identity-and-access-management-iam-based-acc/ |
| 1 | SoK: Authorization in Multi-Agent Retrieval-Augmented Generation Systems | Closely related. Systematizes authorization failure modes in agentic RAG, including semantic overfetch and delegation escalation. | https://doi.org/10.5281/zenodo.18431323 |
| 2 | Risk and UCON-Based Access Control Model for Healthcare Big Data | Directly cited by SAFE-GUARD. Healthcare access-control model using risk quantification and usage control. | https://journalofbigdata.springeropen.com/articles/10.1186/s40537-023-00783-8 |
| 2 | A Systematic Review of Access Control Models: Background, Existing Research, and Challenges | Directly cited by SAFE-GUARD and shares authors. Useful background on access-control model limitations and emerging directions. | https://doi.org/10.1109/ACCESS.2025.3533145 |
| 2 | A Systematic Literature Review for Authorization and Access Control: Definitions, Strategies and Models | Directly cited by SAFE-GUARD. Broad access-control taxonomy and terminology baseline. | https://doi.org/10.1108/IJWIS-04-2022-0077 |
| 2 | A Comprehensive Survey on Requirements, Applications, and Future Challenges for Access Control Models in IoT | Directly cited by SAFE-GUARD. Useful adjacent survey for IoT access-control requirements. | https://www.mdpi.com/2624-831X/6/1/9 |
| 2 | Risk and UCON-Based Access Control Model for Healthcare Big Data | Healthcare-focused adaptive access-control baseline. | https://doi.org/10.1186/s40537-023-00783-8 |
| 2 | Behavior-Based Anomaly Detection in Log Data of Physical Access Control Systems | Directly cited by SAFE-GUARD. Behavior-based anomaly detection for access-control logs. | https://doi.org/10.1109/TDSC.2022.3197265 |
| 2 | Automatic Generation of Attribute-Based Access Control Policies from Natural Language Documents | Additional relevant paper found via search. Deep-learning approach for ABAC policy generation from natural-language documents. | https://doi.org/10.32604/cmc.2024.055167 |

## How To Index

Run:

```bash
./research_papers/ai_access_control/ingest_sources.sh
```

The script imports the local `server.py` module and indexes sources using:

- `download_paper(arxiv_id, auto_index=True)` for arXiv sources.
- `download_paper_by_doi(doi, auto_index=True)` for DOI sources where an open-access PDF is discoverable.

Results are written next to this README as `ingest_results_<timestamp>.json`.

Useful variants:

```bash
./research_papers/ai_access_control/ingest_sources.sh --priority 1
./research_papers/ai_access_control/ingest_sources.sh --dry-run
./research_papers/ai_access_control/ingest_sources.sh --no-index
```
