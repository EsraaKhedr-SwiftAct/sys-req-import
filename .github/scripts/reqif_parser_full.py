#!/usr/bin/env python3
"""
reqif_parser_full.py

Universal ReqIF parser supporting EA, Polarion, DOORS, Jama, PTC.
Features:
- Handles attributes: string, XHTML, enumeration
- Handles optional/mandatory fields
- Preserves hierarchy (parent-child)
- Returns list of ReqIFRequirement objects
"""

import xml.etree.ElementTree as ET

class ReqIFRequirement:
    def __init__(self, identifier, title="", description="", attributes=None, children=None):
        self.id = identifier
        self.identifier = identifier
        self.title = title
        self.description = description
        self.attributes = attributes or {}
        self.children = children or []

    def __repr__(self):
        return f"<ReqIFRequirement {self.id}: {self.title[:30]}>"

class ReqIFParser:
    def __init__(self, reqif_file):
        self.file = reqif_file
        self.ns = {
            "reqif": "http://www.omg.org/spec/ReqIF/20110401/reqif.xsd",
            "xhtml": "http://www.w3.org/1999/xhtml"
        }
        self.spec_objects = {}
        self.child_map = {}  # child_id -> parent_id(s)

    def parse(self):
        tree = ET.parse(self.file)
        root = tree.getroot()

        # -------------------------
        # Step 1: Parse SPEC-OBJECTS
        # -------------------------
        for so in root.findall(".//reqif:SPEC-OBJECT", self.ns):
            identifier = self._get_first_text(so, ".//reqif:ATTRIBUTE-VALUE-STRING[reqif:DEFINITION/reqif:ATTRIBUTE-DEFINITION-STRING-REF='ID_DEF']/reqif:THE-VALUE") or "UNKNOWN"
            title = self._get_first_text(so, ".//reqif:ATTRIBUTE-VALUE-STRING[reqif:DEFINITION/reqif:ATTRIBUTE-DEFINITION-STRING-REF='TITLE_DEF']/reqif:THE-VALUE") or ""
            description = self._get_xhtml_text(so, ".//reqif:ATTRIBUTE-VALUE-XHTML/reqif:THE-VALUE/xhtml:div") or ""

            # Other attributes
            attrs = {}
            # String attributes
            for attr in so.findall(".//reqif:ATTRIBUTE-VALUE-STRING", self.ns):
                key = self._get_def_ref(attr)
                val = self._get_text(attr)
                if key:
                    attrs[key] = val
            # Enumeration attributes
            for attr in so.findall(".//reqif:ATTRIBUTE-VALUE-ENUMERATION", self.ns):
                key = self._get_def_ref(attr)
                values = [v.text.strip() for v in attr.findall(".//reqif:ENUM-VALUE-REF", self.ns) if v.text]
                if key:
                    attrs[key] = values if len(values) > 1 else (values[0] if values else "")

            self.spec_objects[identifier] = ReqIFRequirement(identifier, title, description, attrs)

        # -------------------------
        # Step 2: Parse hierarchy
        # -------------------------
        for parent in root.findall(".//reqif:SPEC-HIERARCHY", self.ns):
            for child_ref in parent.findall(".//reqif:CHILD-OBJECT-REF", self.ns):
                child_id = child_ref.text.strip()
                parent_elem = parent.find("../reqif:SPEC-HIERARCHY-ROOT", self.ns)
                if parent_elem is not None:
                    # The parent of the child
                    parent_refs = parent_elem.findall(".//reqif:CHILD-OBJECT-REF", self.ns)
                    for p in parent_refs:
                        pid = p.text.strip()
                        if pid != child_id:
                            self.child_map[child_id] = pid

        # -------------------------
        # Step 3: Link hierarchy
        # -------------------------
        for child_id, parent_id in self.child_map.items():
            child_req = self.spec_objects.get(child_id)
            parent_req = self.spec_objects.get(parent_id)
            if child_req and parent_req:
                parent_req.children.append(child_req)

        # Return top-level requirements (not children of anyone)
        top_level = [req for req_id, req in self.spec_objects.items() if req_id not in self.child_map]
        return top_level

    # -------------------------
    # Helpers
    # -------------------------
    def _get_def_ref(self, attr_elem):
        """Get the attribute definition reference"""
        def_ref = attr_elem.find(".//reqif:DEFINITION/*", self.ns)
        return def_ref.text.strip() if def_ref is not None else None

    def _get_text(self, elem):
        """Get THE-VALUE text of an element"""
        val = elem.find("reqif:THE-VALUE", self.ns)
        return val.text.strip() if val is not None and val.text else ""

    def _get_first_text(self, parent, xpath):
        elem = parent.find(xpath, self.ns)
        return elem.text.strip() if elem is not None and elem.text else ""

    def _get_xhtml_text(self, parent, xpath):
        elem = parent.find(xpath, self.ns)
        return ET.tostring(elem, encoding="unicode", method="html") if elem is not None else ""
