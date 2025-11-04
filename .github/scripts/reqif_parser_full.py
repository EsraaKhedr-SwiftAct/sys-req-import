#!/usr/bin/env python3
"""
reqif_parser_full.py

Universal ReqIF parser supporting EA, Polarion, DOORS, and Jama dialects.
Compatible with Python 3.8+ and GitHub Actions environment.

──────────────────────────────────────────────
What's new in this version:
──────────────────────────────────────────────
✅ Dynamic DEF resolution — works with GUID-style identifiers 
   (e.g., d3f2-8c14-...) instead of assuming DESC_DEF/TITLE_DEF.

✅ Full XHTML extraction — parses <ATTRIBUTE-VALUE-XHTML> even when 
   nested under <xhtml:div> or directly inside <THE-VALUE>.

✅ Automatic title/description detection — supports flexible naming 
   such as "Title", "Name", "Req Title", "Description", "Desc", "Text".
──────────────────────────────────────────────
"""

import xml.etree.ElementTree as ET


class ReqIFRequirement:
    """Represents a single ReqIF requirement."""
    def __init__(self, identifier, title="", description="", attributes=None):
        self.id = identifier
        self.title = title
        self.description = description
        self.attributes = attributes if attributes else {}

    def __repr__(self):
        return f"<ReqIFRequirement id={self.id} title={self.title!r}>"


class ReqIFParser:
    """Universal parser for .reqif files."""

    REQIF_NS = {
        "reqif": "http://www.omg.org/spec/ReqIF/20110401/reqif.xsd",
        "xhtml": "http://www.w3.org/1999/xhtml"
    }

    def __init__(self, filename):
        self.filename = filename
        self.ns = self.REQIF_NS
        self.tree = ET.parse(filename)
        self.root = self.tree.getroot()
        self.def_map = self._build_definition_map()  # 🧩 DEF-ID → LONG-NAME

    # ----------------------------------------------------------
    # Build definition map (DEF-ID → LONG-NAME)
    # ----------------------------------------------------------
    def _build_definition_map(self):
        mapping = {}
        for def_type in [
            "ATTRIBUTE-DEFINITION-STRING",
            "ATTRIBUTE-DEFINITION-XHTML",
            "ATTRIBUTE-DEFINITION-ENUMERATION",
            "ATTRIBUTE-DEFINITION-INTEGER",
            "ATTRIBUTE-DEFINITION-BOOLEAN",
            "ATTRIBUTE-DEFINITION-DATE"
        ]:
            for attr_def in self.root.findall(f".//reqif:{def_type}", self.ns):
                def_id = attr_def.get("IDENTIFIER")
                long_name = attr_def.findtext("reqif:LONG-NAME", default="", namespaces=self.ns)
                if def_id and long_name:
                    mapping[def_id] = long_name
        return mapping

    # ----------------------------------------------------------
    # Parse all SPEC-OBJECT (requirements)
    # ----------------------------------------------------------
    def parse(self):
        reqs = []
        for spec_obj in self.root.findall(".//reqif:SPEC-OBJECT", self.ns):
            identifier = spec_obj.get("IDENTIFIER", "UNKNOWN")
            attributes = self._collect_attributes(spec_obj)

            # Automatic title/description field detection
            title = self._find_flexible(attributes, ["Title", "Name", "Req Title", "Requirement", "Header"])
            description = self._find_flexible(attributes, ["Description", "Desc", "Text", "Body", "Content"])

            reqs.append(ReqIFRequirement(identifier, title, description, attributes))
        return reqs

    # ----------------------------------------------------------
    # Collect all attributes for a SPEC-OBJECT
    # ----------------------------------------------------------
    def _collect_attributes(self, spec_obj):
        attrs = {}

        all_attrs = (
            spec_obj.findall(".//reqif:ATTRIBUTE-VALUE-STRING", self.ns)
            + spec_obj.findall(".//reqif:ATTRIBUTE-VALUE-XHTML", self.ns)
            + spec_obj.findall(".//reqif:ATTRIBUTE-VALUE-ENUMERATION", self.ns)
            + spec_obj.findall(".//reqif:ATTRIBUTE-VALUE-INTEGER", self.ns)
            + spec_obj.findall(".//reqif:ATTRIBUTE-VALUE-BOOLEAN", self.ns)
            + spec_obj.findall(".//reqif:ATTRIBUTE-VALUE-DATE", self.ns)
        )

        for attr in all_attrs:
            key = self._resolve_definition_name(attr)
            if not key:
                continue
            value = self._extract_value(attr)
            attrs[key] = value

        return attrs

    # ----------------------------------------------------------
    # Resolve definition name dynamically (handles GUIDs)
    # ----------------------------------------------------------
    def _resolve_definition_name(self, attr):
        """Return LONG-NAME for referenced DEF-ID (handles GUIDs or partial matches)."""
        for def_type in [
            "ATTRIBUTE-DEFINITION-STRING-REF",
            "ATTRIBUTE-DEFINITION-XHTML-REF",
            "ATTRIBUTE-DEFINITION-ENUMERATION-REF",
            "ATTRIBUTE-DEFINITION-INTEGER-REF",
            "ATTRIBUTE-DEFINITION-BOOLEAN-REF",
            "ATTRIBUTE-DEFINITION-DATE-REF"
        ]:
            ref_elem = attr.find(f"reqif:DEFINITION/reqif:{def_type}", self.ns)
            if ref_elem is not None and ref_elem.text:
                ref_id = ref_elem.text.strip()
                # Direct match
                if ref_id in self.def_map:
                    return self.def_map[ref_id]
                # 🔁 Fallback for nonstandard or partial ID matches (nonstandard block)
                for known_id, long_name in self.def_map.items():
                    if ref_id.lower() in known_id.lower() or known_id.lower() in ref_id.lower():
                        return long_name
        return None

    # ----------------------------------------------------------
    # Extract THE-VALUE content (StrictDoc-compatible)
    # ----------------------------------------------------------
    def _extract_value(self, attr):
        tag = attr.tag.split("}")[-1]

        if tag == "ATTRIBUTE-VALUE-STRING":
            val = attr.find("reqif:THE-VALUE", self.ns)
            return val.text.strip() if val is not None and val.text else ""

        if tag == "ATTRIBUTE-VALUE-XHTML":
            # StrictDoc-style XHTML resolution
            val_elem = attr.find("reqif:THE-VALUE", self.ns)
            if val_elem is None:
                return ""
            # Case 1: <xhtml:div> inside <THE-VALUE>
            div_elem = val_elem.find("xhtml:div", self.ns)
            if div_elem is not None:
                return "".join(div_elem.itertext()).strip()
            # Case 2: direct XHTML content (no <div>)
            if val_elem.text and val_elem.text.strip():
                return val_elem.text.strip()
            # Case 3: nested or mixed XHTML — join all inner text
            return "".join(val_elem.itertext()).strip()

        if tag == "ATTRIBUTE-VALUE-ENUMERATION":
            vals = attr.findall("reqif:VALUES/reqif:ENUM-VALUE-REF", self.ns)
            return ",".join(v.text for v in vals if v.text)

        if tag == "ATTRIBUTE-VALUE-INTEGER":
            val = attr.find("reqif:THE-VALUE", self.ns)
            return int(val.text) if val is not None and val.text else 0

        if tag == "ATTRIBUTE-VALUE-BOOLEAN":
            val = attr.find("reqif:THE-VALUE", self.ns)
            return (val.text.lower() == "true") if val is not None and val.text else False

        if tag == "ATTRIBUTE-VALUE-DATE":
            val = attr.find("reqif:THE-VALUE", self.ns)
            return val.text.strip() if val is not None and val.text else ""

        return None

    # ----------------------------------------------------------
    # Helper: find attribute value by flexible name
    # ----------------------------------------------------------
    def _find_flexible(self, attributes, keys):
        for k in keys:
            for akey, val in attributes.items():
                if k.lower() in akey.lower():
                    return val
        return ""


# ----------------------------------------------------------
# Example usage
# ----------------------------------------------------------
if __name__ == "__main__":
    parser = ReqIFParser("sample.reqif")
    requirements = parser.parse()
    for req in requirements:
        print(f"[{req.id}] {req.title or '(Untitled)'}")
        print(f"  Description: {req.description[:80] if req.description else '(No description found)'}\n")



