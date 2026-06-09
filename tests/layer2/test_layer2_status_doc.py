from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
STATUS_DOC = ROOT / "docs/LAYER2_IMPLEMENTATION_STATUS.md"


IMPLEMENTED_BLOCKS = {
    "ROAD-C",
    "ROAD-A",
    "ROAD-B",
    "ROAD-F",
    "ROAD-COST",
    "SEA-C",
    "SEA-D",
    "SEA-A",
    "SEA-B",
    "SEA-F",
    "SEA-I",
    "SEA-COST",
    "AIR-C",
    "AIR-D",
    "AIR-A",
    "AIR-B",
    "AIR-E",
    "AIR-F",
    "AIR-H",
    "AIR-I",
}


def _status_doc_text() -> str:
    return STATUS_DOC.read_text(encoding="utf-8")


def test_layer2_status_doc_exists():
    assert STATUS_DOC.exists()


def test_layer2_status_doc_mentions_all_implemented_blocks():
    text = _status_doc_text()

    for block_id in IMPLEMENTED_BLOCKS:
        assert block_id in text


def test_layer2_status_doc_mentions_no_llm():
    text = _status_doc_text().lower()

    assert "no llm" in text


def test_layer2_status_doc_mentions_multimodal_v2_limitation():
    text = _status_doc_text()

    assert "Multimodal v2" in text
    assert "RequestedMode.multimodal" in text
    assert "shipment legs" in text


def test_layer2_status_doc_mentions_201_tests():
    text = _status_doc_text()

    assert "201 tests" in text
