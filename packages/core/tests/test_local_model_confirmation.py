"""Confirmação humana não aceita coerção de números para booleano."""

from uuid import uuid4

import pytest
from pydantic import ValidationError

from bees_core.providers.local_models import PullInput


@pytest.mark.parametrize("confirmation", [1, 1.0, False, 0, None, "true", "1", [], {}])
def test_pull_requires_literal_boolean_confirmation(confirmation):
    with pytest.raises(ValidationError):
        PullInput.model_validate(
            {"client_request_id": str(uuid4()), "model": "qwen3", "confirm_download": confirmation}
        )


def test_true_confirmation_preserves_uuid_and_normal_input():
    request_id = uuid4()
    value = PullInput.model_validate_json(
        '{"client_request_id":"' + str(request_id) + '","model":"qwen3","confirm_download":true}'
    )
    assert value.confirm_download is True and value.client_request_id == request_id
