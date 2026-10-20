"""Parse federal law XML from gesetze-im-internet.de (official consolidated text, BMJ/juris)."""
import io
import re
import zipfile
import xml.etree.ElementTree as ET

# "Richtlinie 2010/31/EU", "Richtlinie (EU) 2018/844", "Verordnung (EU) Nr. 305/2011" -> CELEX
_EU_ACT = re.compile(r"(Richtlinie|Verordnung)\s+(?:\((?:EU|EG|EWG)\)\s+)?(Nr\.\s+)?(\d{2,4})/(\d{1,4})")


def _text(element) -> str:
    return re.sub(r"\s+", " ", "".join(element.itertext())).strip() if element is not None else ""


def parse_gii_xml(content: bytes) -> dict:
    """Return law metadata and its norms: [{"norm": "§ 71", "title": ..., "text": ...}]."""
    if content[:2] == b"PK":
        archive = zipfile.ZipFile(io.BytesIO(content))
        content = archive.read(next(n for n in archive.namelist() if n.endswith(".xml")))
    root = ET.fromstring(content)
    norms = root.findall("norm")
    if not norms:
        raise ValueError("no <norm> elements in law XML")
    head = norms[0].find("metadaten")
    law = {"abbreviation": _text(head.find("jurabk")), "title": _text(head.find("langue")),
           "signed": _text(head.find("ausfertigung-datum")) or None, "doknr": root.attrib.get("doknr"),
           "status": " | ".join(_text(s.find("standkommentar")) for s in head.findall("standangabe")), "norms": []}
    for norm in norms:
        meta = norm.find("metadaten")
        label = _text(meta.find("enbez"))
        body = norm.find("textdaten/text")
        law["norms"].append({"norm": label or "preamble", "title": _text(meta.find("titel")), "text": _text(body)})
    return law


def implemented_eu_acts(text: str) -> list[str]:
    """CELEX numbers of EU directives/regulations a German law cites (directives: year first; old regulations: number first)."""
    celex = set()
    for kind, numbered, a, b in _EU_ACT.findall(text):
        year, number = (b, a) if kind == "Verordnung" and (numbered or len(b) == 4) else (a, b)
        if len(year) == 2:
            year = ("19" if int(year) > 50 else "20") + year
        if len(year) == 4:
            celex.add(f"3{year}{'L' if kind == 'Richtlinie' else 'R'}{int(number):04d}")
    return sorted(celex)
