#!/usr/bin/env python3
import argparse
import json
import os
import ssl
import sys
import urllib.error
import urllib.parse
import urllib.request


def request(
    url: str,
    method: str = "GET",
    token: str | None = None,
    *,
    verify_tls: bool = True,
) -> tuple[int, bytes, dict[str, str]]:
    headers = {"Accept": "application/vnd.docker.distribution.manifest.v2+json, application/json"}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    req = urllib.request.Request(url, method=method, headers=headers)
    try:
        context = None if verify_tls else ssl._create_unverified_context()
        with urllib.request.urlopen(req, timeout=30, context=context) as response:
            return response.status, response.read(), dict(response.headers.items())
    except urllib.error.HTTPError as exc:
        return exc.code, exc.read(), dict(exc.headers.items())


def api_url(registry: str, repository: str, path: str) -> str:
    return f"{registry.rstrip('/')}/v2/{repository.strip('/')}/{path.lstrip('/')}"


def optional_token_from_env(name: str) -> str | None:
    return os.getenv(name) or None


def tag_created_at(
    registry: str,
    repository: str,
    tag: str,
    token: str | None,
    verify_tls: bool,
) -> tuple[str, str]:
    encoded_tag = urllib.parse.quote(tag, safe="")
    status, body, headers = request(
        api_url(registry, repository, f"manifests/{encoded_tag}"),
        token=token,
        verify_tls=verify_tls,
    )
    if status >= 400:
        raise RuntimeError(f"failed reading manifest for {tag}: HTTP {status} {body.decode(errors='replace')}")
    digest = headers.get("Docker-Content-Digest") or headers.get("docker-content-digest")
    if not digest:
        raise RuntimeError(f"registry did not return Docker-Content-Digest for {tag}")
    try:
        manifest = json.loads(body.decode())
    except json.JSONDecodeError:
        manifest = {}
    created = ""
    config = manifest.get("config") if isinstance(manifest, dict) else None
    digest_path = config.get("digest") if isinstance(config, dict) else None
    if isinstance(digest_path, str):
        status, config_body, _ = request(
            api_url(registry, repository, f"blobs/{digest_path}"),
            token=token,
            verify_tls=verify_tls,
        )
        if status < 400:
            try:
                config_json = json.loads(config_body.decode())
                created = str(config_json.get("created") or "")
            except json.JSONDecodeError:
                created = ""
    return created or tag, digest


def main() -> int:
    parser = argparse.ArgumentParser(description="Keep current + N-1 tags for a Zot/Docker Registry repository.")
    parser.add_argument("--registry", required=True)
    parser.add_argument("--repository", required=True)
    parser.add_argument("--keep", type=int, default=2)
    parser.add_argument(
        "--token-env",
        default="ZOT_TOKEN",
        help="Optional bearer token environment variable. If unset, requests are unauthenticated.",
    )
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--insecure-tls", action="store_true")
    args = parser.parse_args()

    token = optional_token_from_env(args.token_env)
    verify_tls = not args.insecure_tls
    status, body, _ = request(api_url(args.registry, args.repository, "tags/list"), token=token, verify_tls=verify_tls)
    if status >= 400:
        raise RuntimeError(f"failed listing tags: HTTP {status} {body.decode(errors='replace')}")
    tags = json.loads(body.decode()).get("tags") or []
    tag_details = []
    for tag in tags:
        created, digest = tag_created_at(args.registry, args.repository, str(tag), token, verify_tls)
        tag_details.append((str(tag), created, digest))
    tag_details.sort(key=lambda item: item[1], reverse=True)
    delete_tags = tag_details[args.keep :]
    for tag, _, digest in delete_tags:
        digest_path = urllib.parse.quote(digest, safe=":")
        if args.dry_run:
            print(f"would delete {tag} ({digest})")
            continue
        status, body, _ = request(
            api_url(args.registry, args.repository, f"manifests/{digest_path}"),
            "DELETE",
            token,
            verify_tls=verify_tls,
        )
        if status >= 400:
            raise RuntimeError(f"failed deleting {tag}: HTTP {status} {body.decode(errors='replace')}")
        print(f"deleted {tag} ({digest})")
    print(f"kept {min(len(tag_details), args.keep)} tag(s), pruned {len(delete_tags)} tag(s)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
