# AI 前沿情报局 · Phase 4.5

Self-contained research, source-evidence checks, Simplified Chinese editorial
screening, and local static-site rendering. Python 3.10+; no pip dependencies.

## Controlled workflow

The approved daily workflow at commit
46399168ed4da38e64dd32f618b1b1d8034ea12c allows only a manual dry-run. It has no
schedule, digest commit/push, Pages artifact upload or deployment step.
The workflow file is unchanged by this implementation remediation.

Local integration is based on main e6df73e211798a3f530ccca3d6cac2949d1e86fb.
Its separate manual deployment workflow is retained unchanged and was not run.
The custom-domain CNAME is previewed in memory when the base URL uses
orbisignalmedia.duckdns.org; only an explicit non-dry local build writes it.
Upstream discovery feed configuration and additional AI keywords are retained;
the orchestrator still uses its two original default feeds. No live-source
availability is implied by the configuration.

Cloudflare inference requires CF_API_TOKEN and CF_ACCOUNT_ID, supplied only to
the pipeline step. The client uses @cf/zai-org/glm-4.7-flash,
max_completion_tokens=512 and chat_template_kwargs.enable_thinking=false.
It supports result.response, result.choices, and top-level choices. No pricing
or zero-cost guarantee is made.

## Reproduce the complete independent offline suite

From the repository root:

    python -B tests/run_tests.py

The runner removes real Cloudflare credentials and denies socket connections.
Tests use synthetic RSS/HTML/model responses and temporary local directories;
no live API, GitHub Actions, Pages deployment or public articles are involved.
All required modules, test fixtures and script-screening dictionary data are in
this repository. The originally reported Muse 64-test suite was not recovered;
its result remains unverified. This is a newly implemented independent suite.

## Dry-run and state behavior

CLI --dry-run renders the prospective site and checks it entirely in memory,
including with --landing-only. It does not change draft files, site files,
publication records, run records or Python bytecode. Landing-only previews
retain existing historical articles. Empty or repeated runs leave draft bytes
and modification times untouched.

Validated drafts are appended by canonical source URL with stable ordering.
Existing same-day drafts and source metadata win over repeated URLs; older
dates remain in archives, RSS and sitemap. The daily limit includes already
saved same-day articles. New drafts carry source key-point evidence and an
article-text hash; the full source article is not persisted.

Without --dry-run the pipeline can write a *local* draft/state/site build. It
still cannot deploy, push, or publish. Do not use that path against public
publication data without separate authorization.

## Content boundaries and limits

Sources and every redirect must use HTTPS on an exact allowlisted host.
Credentials in URLs, external redirect targets, XML entities, oversized
responses, malformed state, invalid evidence, unsupported entities/numbers,
Traditional-only characters and obvious instruction/secret patterns fail
closed. Source material is encoded as a data-only JSON message; the fixed
system message forbids following embedded instructions. Attribution, original
date, AI disclosure and the conservative regional note are appended locally.

Public pipeline logs contain fixed event codes and bounded numeric summaries,
not URLs, article excerpts, model responses, raw API errors or exception text.
A failed model/credential/feed operation produces an explicit error result and
does not write publication state.

Summaries explicitly distinguish validated content, landing-only previews,
legitimate no-article outcomes and failures. Empty/filtered/deduplicated sources
and a full daily quota are legitimate no-article outcomes. Requested model
generation/QA failures, missing credentials, article transport/processing failures
and failed feeds return a failed status and nonzero CLI exit, even if another
candidate succeeded. Such failed runs do not write local publication state.

Pattern screening and lexical overlap cannot prove translation accuracy or
eliminate every prompt-injection attack. Chinese-number paraphrases, subtle
semantic fabrication and ambiguous Simplified/Traditional usage require human
review. There are no model tools or model-controlled execution paths.

OpenCC character dictionary data and its upstream Apache 2.0 license/attribution
are bundled under pipeline/vendor/data; the OpenCC runtime is not needed.
