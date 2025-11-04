#!/usr/bin/env python3
"""
reqif_parser_full.py

Universal ReqIF parser supporting EA, Polarion, DOORS, Jama dialects.
Compatible with Python 3.8+ and GitHub Actions environment.
Now dynamically resolves definition references (handles XHTML Description properly).
"""

import xml.etree.ElementTree as ET

class ReqIFRequirement:
    """Represents a single ReqIF requirement."""
    def __init__(self, identifier, title="", description="", attributes=None):
        self.id = identifier
        self.title = title
        self.description = description
        self.attributes = attributes if attributes else {}

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
        self.def_map = self._build_definition_map()  # 🧩 new step

    # ----------------------------------------------------------
    # Build a definition map (DEF-ID → LONG-NAME)
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
    # Parse all requirements
    # ----------------------------------------------------------
    def parse(self):
        """Parses all requirements in the ReqIF file."""
        reqs = []
        for spec_obj in self.root.findall(".//reqif:SPEC-OBJECT", self.ns):
            identifier = spec_obj.get("IDENTIFIER", "UNKNOWN")

            attributes = self._collect_attributes(spec_obj)
            title = attributes.get("Title") or attributes.get("Name") or ""
            description = attributes.get("Description") or attributes.get("Desc") or ""

            reqs.append(ReqIFRequirement(identifier, title, description, attributes))
        return reqs

    # ----------------------------------------------------------
    # Collect all attributes for a SPEC-OBJECT
    # ----------------------------------------------------------
    def _collect_attributes(self, spec_obj):
        attrs = {}

        # Handle all ATTRIBUTE-VALUE-* nodes
        for attr in spec_obj.findall("./reqif:VALUES/*", self.ns):
            key = self._resolve_definition_name(attr)
            if not key:
                continue
            value = self._extract_value(attr)
            attrs[key] = value
        return attrs

    # ----------------------------------------------------------
    # Resolve definition name from DEFINITION reference
    # ----------------------------------------------------------
    def _resolve_definition_name(self, attr):
        """Return human-readable LONG-NAME from DEFINITION reference."""
        for def_type in [
            "ATTRIBUTE-DEFINITION-STRING-REF",
            "ATTRIBUTE-DEFINITION-XHTML-REF",
            "ATTRIBUTE-DEFINITION-ENUMERATION-REF",
            "ATTRIBUTE-DEFINITION-INTEGER-REF",
            "ATTRIBUTE-DEFINITION-BOOLEAN-REF",
            "ATTRIBUTE-DEFINITION-DATE-REF"
        ]:
            ref = attr.find(f"reqif:DEFINITION/reqif:{def_type}", self.ns)
            if ref is not None and ref.text in self.def_map:
                return self.def_map[ref.text]
        return None

    # ----------------------------------------------------------
    # Extract THE-VALUE text
    # ----------------------------------------------------------
    def _extract_value(self, attr):
        tag = attr.tag.split("}")[-1]

        # Simple text
        if tag == "ATTRIBUTE-VALUE-STRING":
            val = attr.find("reqif:THE-VALUE", self.ns)
            return val.text.strip() if val is not None and val.text else ""

        # XHTML description (e.g. <xhtml:div>)
        if tag == "ATTRIBUTE-VALUE-XHTML":
            div = attr.find("reqif:THE-VALUE/xhtml:div", self.ns)
            if div is not None:
                return "".join(div.itertext()).strip()
            val = attr.find("reqif:THE-VALUE", self.ns)
            return val.text.strip() if val is not None and val.text else ""

        # Enumeration
        if tag == "ATTRIBUTE-VALUE-ENUMERATION":
            vals = attr.findall("reqif:VALUES/reqif:ENUM-VALUE-REF", self.ns)
            return ",".join(v.text for v in vals if v.text)

        # Integers
        if tag == "ATTRIBUTE-VALUE-INTEGER":
            val = attr.find("reqif:THE-VALUE", self.ns)
            return int(val.text) if val is not None and val.text else 0

        # Boolean
        if tag == "ATTRIBUTE-VALUE-BOOLEAN":
            val = attr.find("reqif:THE-VALUE", self.ns)
            return (val.text.lower() == "true") if val is not None and val.text else False

        # Dates
        if tag == "ATTRIBUTE-VALUE-DATE":
            val = attr.find("reqif:THE-VALUE", self.ns)
            return val.text.strip() if val is not None and val.text else ""

        return None

