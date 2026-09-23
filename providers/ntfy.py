"""HTTPS ntfy JSON publishing, using only the Python standard library."""

from dataclasses import dataclass, field
import http.client
import json
import math
from queue import Empty, Queue
import re
from threading import Thread
from typing import Callable, Mapping
import urllib.error
import urllib.parse
import urllib.request

from providers import ConfigurationError, Notification, ProviderError


@dataclass(frozen=True)
class NtfyConfig:
    server: str = field(repr=False)
    topic: str = field(repr=False)
    token: str = field(default="", repr=False)
    timeout: float = 5.0


def load_config(values: Mapping[str, str]) -> NtfyConfig:
    topic = values.get("NTFY_TOPIC", "").strip()
    if not topic:
        raise ConfigurationError("NTFY_TOPIC is required")
    if topic == "replace-with-a-long-random-private-topic":
        raise ConfigurationError("replace the NTFY_TOPIC template with a random private topic")
    if not re.fullmatch(r"[-_A-Za-z0-9]{1,64}", topic):
        raise ConfigurationError("NTFY_TOPIC must use 1-64 letters, digits, underscores or dashes")

    server = values.get("NTFY_SERVER", "https://ntfy.sh").strip()
    try:
        url = urllib.parse.urlsplit(server)
        valid = (
            url.scheme == "https" and bool(url.hostname)
            and url.username is None and url.password is None
            and not url.query and not url.fragment
            and url.path in ("", "/") and url.port != 0
            and server.isascii()
            and not re.search(r"[\s\\\x00-\x1f\x7f]", server)
            and "?" not in server and "#" not in server
        )
    except ValueError:
        valid = False
    if not valid:
        raise ConfigurationError("NTFY_SERVER must be an HTTPS origin without credentials, path, query or fragment")

    token = values.get("NTFY_TOKEN", "").strip()
    if token and not re.fullmatch(r"[A-Za-z0-9._~+/-]+=*", token):
        raise ConfigurationError("NTFY_TOKEN must be a single ASCII bearer token")
    try:
        timeout = float(values.get("CODEX_NOTIFY_TIMEOUT", "5"))
    except ValueError:
        timeout = float("nan")
    if not math.isfinite(timeout) or not 0 < timeout <= 30:
        raise ConfigurationError("CODEX_NOTIFY_TIMEOUT must be greater than 0 and at most 30 seconds")
    return NtfyConfig(server.rstrip("/"), topic, token, timeout)


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        # Never forward topic, body or Authorization to a different endpoint.
        return None


def build_request(config: NtfyConfig, notification: Notification) -> urllib.request.Request:
    body = json.dumps({
        "topic": config.topic,
        "title": notification.title,
        "message": notification.message,
        "priority": 3,
        "tags": ["computer"],
    }, ensure_ascii=False).encode("utf-8")
    # Conservatively bound the entire JSON envelope, not just the message.
    if len(body) > 4096:
        raise ProviderError("notification exceeds the 4096-byte limit")
    headers = {"Content-Type": "application/json; charset=utf-8"}
    if config.token:
        headers["Authorization"] = "Bearer " + config.token
    return urllib.request.Request(config.server + "/", data=body, headers=headers, method="POST")


def send_notification(
    config: NtfyConfig, notification: Notification, *, opener: Callable | None = None,
) -> None:
    request = build_request(config, notification)
    results: Queue[Exception | None] = Queue(maxsize=1)

    def publish() -> None:
        try:
            open_request = opener or urllib.request.build_opener(_NoRedirect()).open
            with open_request(request, timeout=config.timeout) as response:
                if not 200 <= response.status < 300:
                    raise ProviderError("ntfy returned a non-success HTTP status")
                # No need to read a response body (which could be unbounded).
        except urllib.error.HTTPError as exc:
            exc.close()
            results.put(ProviderError(f"ntfy rejected the request (HTTP {exc.code})"))
        except (OSError, urllib.error.URLError, http.client.HTTPException, ValueError):
            # Exception text can include a URL, token, proxy password or response.
            results.put(ProviderError("ntfy connection failed or timed out"))
        except Exception as exc:
            results.put(exc)
        else:
            results.put(None)

    # urllib's socket timeout alone does not bound DNS or slow response headers.
    # A daemon worker bounds the CLI lifetime even if the OS resolver stalls.
    Thread(target=publish, daemon=True, name="ntfy-publish").start()
    try:
        error = results.get(timeout=config.timeout)
    except Empty:
        raise ProviderError("ntfy delivery timed out; delivery status is unknown") from None
    if error is not None:
        raise error
