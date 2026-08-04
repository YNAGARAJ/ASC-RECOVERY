from __future__ import annotations

from packets.templates import DEFAULT_TEMPLATE, PacketTemplate, select_template


def test_select_template_falls_back_to_default_when_no_override() -> None:
    assert select_template(None) is DEFAULT_TEMPLATE


def test_select_template_uses_payer_override_when_present() -> None:
    override = PacketTemplate(
        salutation="Dear Appeals Department,",
        letterhead="Formal Appeal",
        closing="Respectfully,",
        footer_legal_text="Payer-specific legal citation.",
    )

    result = select_template(override)

    assert result is override
    assert result.salutation == "Dear Appeals Department,"
