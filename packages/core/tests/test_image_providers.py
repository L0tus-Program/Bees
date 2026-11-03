"""Wire multimodal real nos dois protocolos e recusa local antes do despacho."""

import asyncio
import base64
import io
import json

import httpx
import pytest
from PIL import Image
from pydantic import ValidationError
from test_provider_adapters import config, text_response

from bees_core.images import MAX_IMAGE_BYTES, MAX_IMAGE_PIXELS, validate_image
from bees_core.providers.base import create_adapter
from bees_core.providers.contracts import ChatMessage, ChatRequest, ImageContent
from bees_core.providers.errors import ProviderError


def image():
    output = io.BytesIO()
    Image.new("RGB", (32, 32)).save(output, format="PNG")
    return ImageContent(media_type="image/png", data=base64.b64encode(output.getvalue()).decode())


@pytest.mark.parametrize("kind", ["openai_compatible", "ollama"])
def test_image_serialization_and_capability_check(kind):
    seen = []
    picture = image()
    request = ChatRequest(
        messages=[ChatMessage(role="user", content="Leia a imagem", images=[picture])]
    )

    def handler(incoming):
        body = json.loads(incoming.content) if incoming.content else {}
        seen.append((incoming.url.path, body))
        if incoming.url.path == "/api/tags":
            return httpx.Response(200, json={"models": [{"name": "test-model:latest"}]})
        if incoming.url.path == "/api/show":
            return httpx.Response(200, json={"capabilities": ["completion", "vision"]})
        return httpx.Response(200, json=text_response(kind))

    adapter = create_adapter(kind, transport=httpx.MockTransport(handler))
    with pytest.raises(ProviderError) as error:
        asyncio.run(adapter.complete(config(kind), request))
    assert error.value.code == "image_vision_required"
    assert not seen
    asyncio.run(adapter.complete(config(kind, capabilities={"vision": True}), request))
    body = seen[-1][1]
    if kind == "ollama":
        assert body["messages"][0]["images"] == [picture.data]
    else:
        assert body["messages"][0]["content"][1]["image_url"]["url"].endswith(picture.data)


def test_malformed_or_wrong_role_image_is_not_accepted():
    with pytest.raises(ValidationError):
        ChatMessage(role="system", content="texto", images=[image()])
    for value in ("https://example.org/image.png", base64.b64encode(b"not an image").decode()):
        with pytest.raises(ValidationError):
            ImageContent(media_type="image/png", data=value)


def test_image_limits_include_pixels_and_real_decoding():
    raw = base64.b64decode(image().data)
    with pytest.raises(ValueError):
        validate_image("image/png", raw + bytes(MAX_IMAGE_BYTES))
    with pytest.raises(ValueError):
        validate_image("image/png", raw[:40])
    with pytest.raises(ValueError):
        validate_image("image/jpeg", raw)
    output = io.BytesIO()
    Image.new("1", (MAX_IMAGE_PIXELS // 10 + 1, 10)).save(output, format="PNG")
    with pytest.raises(ValueError):
        validate_image("image/png", output.getvalue())


def test_ollama_does_not_dispatch_images_without_actual_vision():
    seen = []

    def handler(incoming):
        seen.append(incoming.url.path)
        if incoming.url.path == "/api/tags":
            return httpx.Response(200, json={"models": [{"name": "test-model:latest"}]})
        return httpx.Response(200, json={"capabilities": ["completion"]})

    adapter = create_adapter("ollama", transport=httpx.MockTransport(handler))
    request = ChatRequest(messages=[ChatMessage(role="user", content="Dados", images=[image()])])
    with pytest.raises(ProviderError) as error:
        asyncio.run(adapter.complete(config("ollama", capabilities={"vision": True}), request))
    assert error.value.code == "image_vision_required" and "/api/chat" not in seen
