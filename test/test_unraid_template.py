import xml.etree.ElementTree as ET
from pathlib import Path

from src.settings import AppSettings

TEMPLATE = Path(__file__).resolve().parents[1] / "unraid" / "jellyplex-watched.xml"


def _configs() -> dict[str, ET.Element]:
    root = ET.parse(TEMPLATE).getroot()
    return {c.get("Target"): c for c in root.findall("Config")}


def test_template_is_well_formed_container():
    root = ET.parse(TEMPLATE).getroot()
    assert root.tag == "Container" and root.get("version") == "2"
    assert root.findtext("Repository") == "luigi311/jellyplex-watched:latest"
    for tag in ("Name", "Overview", "WebUI", "Category"):
        assert root.findtext(tag)


def test_template_variables_are_real_settings():
    settings = {name.upper() for name in AppSettings.model_fields}
    variables = {
        target: c
        for target, c in _configs().items()
        if c.get("Type") == "Variable" and target.startswith("JPW_")
    }
    assert set(variables) == {"JPW_GUI_ENABLED", "JPW_GUI_TOKEN", "JPW_GUI_HOST"}
    for target in variables:
        assert target.removeprefix("JPW_") in settings


def test_template_gui_defaults_match_the_app():
    configs = _configs()
    defaults = AppSettings.model_fields
    port = configs["8080"]
    assert port.get("Type") == "Port" and port.get("Default") == str(defaults["gui_port"].default)
    assert configs["JPW_GUI_ENABLED"].get("Default") == "false"
    assert configs["JPW_GUI_TOKEN"].get("Mask") == "true"
    root = ET.parse(TEMPLATE).getroot()
    assert "[PORT:8080]" in root.findtext("WebUI")


def test_config_folder_is_writable_for_the_gui_editor():
    config = _configs()["/app/config"]
    assert config.get("Type") == "Path" and config.get("Mode") == "rw"
