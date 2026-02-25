#!/usr/bin/env python3
"""Run production AEX v2.1 real-call verification checks."""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
from datetime import datetime, UTC

from prod_checks.client import ProdCheckClient
from prod_checks.models import RunContext, RunSummary
from prod_checks.checks import smoke, auth, proxy, idempotency


def _env(name: str, default: str = "") -> str:
    return (os.getenv(name) or default).strip()


def _write_report_files(summary: RunSummary, out_dir: Path) -> tuple[Path, Path]:
    out_dir.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    json_path = out_dir / f"prod_check_{stamp}.json"
    md_path = out_dir / f"prod_check_{stamp}.md"

    json_path.write_text(json.dumps(summary.to_dict(), indent=2, ensure_ascii=True) + "\n")

    lines = [
        "# AEX v2.1 Production Check Report",
        "",
        f"- Timestamp (UTC): {summary.ts_utc}",
        f"- Base URL: {summary.base_url}",
        f"- Total: {summary.total}",
        f"- Passed: {summary.passed}",
        f"- Failed: {summary.failed}",
        "",
        "| Name | Category | Result | HTTP | Latency(ms) | Detail |",
        "|---|---|---|---:|---:|---|",
    ]
    for check in summary.checks:
        lines.append(
            "| {name} | {category} | {result} | {status} | {latency} | {detail} |".format(
                name=check["name"],
                category=check["category"],
                result="PASS" if check["passed"] else "FAIL",
                status=check["status_code"] if check["status_code"] is not None else "-",
                latency=check["latency_ms"] if check["latency_ms"] is not None else "-",
                detail=str(check["detail"]).replace("|", "/"),
            )
        )

    md_path.write_text("\n".join(lines) + "\n")
    return json_path, md_path


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run production checks against AEX v2.1.")
    parser.add_argument("--base-url", default=_env("AEX_PROD_BASE_URL"), help="Production AEX base URL")
    parser.add_argument(
        "--token",
        default=_env("AEX_PROD_AGENT_TOKEN", _env("AEX_AGENT_TOKEN")),
        help="AEX agent token used for authenticated endpoints",
    )
    parser.add_argument(
        "--chat-model",
        default=_env("AEX_TEST_CHAT_MODEL", "gpt-oss-20b"),
        help="Model for chat/responses checks",
    )
    parser.add_argument(
        "--embedding-model",
        default=_env("AEX_TEST_EMBEDDING_MODEL", "text-embedding-3-small"),
        help="Model for embeddings checks",
    )
    parser.add_argument(
        "--skip-embeddings",
        action="store_true",
        help="Skip /v1/embeddings proxy validation (useful when embeddings are out of scope).",
    )
    parser.add_argument("--tenant-id", default=_env("AEX_TENANT_ID"), help="Optional tenant header")
    parser.add_argument("--project-id", default=_env("AEX_PROJECT_ID"), help="Optional project header")
    parser.add_argument(
        "--provider-api-key",
        default=_env("AEX_PROVIDER_API_KEY"),
        help="Optional passthrough provider key for x-aex-provider-key header",
    )
    parser.add_argument(
        "--use-passthrough-provider-key",
        action="store_true",
        help="Send x-aex-provider-key on proxy calls (agent must allow passthrough)",
    )
    parser.add_argument("--timeout-seconds", type=float, default=45.0, help="Per-request timeout")
    parser.add_argument(
        "--output-dir",
        default="scripts/prod_checks/results",
        help="Directory for JSON/Markdown report outputs",
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    if not args.base_url:
        raise SystemExit("Missing --base-url (or set AEX_PROD_BASE_URL).")
    if not args.token:
        raise SystemExit("Missing --token (or set AEX_PROD_AGENT_TOKEN / AEX_AGENT_TOKEN).")
    if args.use_passthrough_provider_key and not args.provider_api_key:
        raise SystemExit("--use-passthrough-provider-key requires --provider-api-key / AEX_PROVIDER_API_KEY.")

    ctx = RunContext(
        base_url=args.base_url,
        token=args.token,
        chat_model=args.chat_model,
        embedding_model=args.embedding_model,
        tenant_id=args.tenant_id or None,
        project_id=args.project_id or None,
        provider_api_key=args.provider_api_key or None,
        timeout_seconds=float(args.timeout_seconds),
    )

    client = ProdCheckClient(ctx)
    try:
        results = []
        results.extend(smoke.run(client))
        results.extend(auth.run(client, chat_model=ctx.chat_model))
        results.extend(
            proxy.run(
                client,
                chat_model=ctx.chat_model,
                embedding_model=ctx.embedding_model,
                passthrough_provider_key=args.use_passthrough_provider_key,
                include_embeddings=not args.skip_embeddings,
            )
        )
        results.extend(
            idempotency.run(
                client,
                chat_model=ctx.chat_model,
                passthrough_provider_key=args.use_passthrough_provider_key,
            )
        )
    finally:
        client.close()

    summary = RunSummary.from_results(base_url=ctx.base_url, results=results)
    json_path, md_path = _write_report_files(summary, Path(args.output_dir))

    print(f"base_url={summary.base_url}")
    print(f"total={summary.total} passed={summary.passed} failed={summary.failed}")
    print(f"json_report={json_path}")
    print(f"md_report={md_path}")
    for check in results:
        state = "PASS" if check.passed else "FAIL"
        print(
            f"[{state}] {check.category}/{check.name} "
            f"status={check.status_code} latency_ms={check.latency_ms} detail={check.detail}"
        )

    return 0 if summary.failed == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
