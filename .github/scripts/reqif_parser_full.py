#!/usr/bin/env python3
"""
reqif_parser_full.py

Universal ReqIF parser supporting EA, Polarion, DOORS, Jama dialects.
Compatible with Python 3.8+ and GitHub Actions environment.
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
    
    def parse(self):
        """Parses all requirements in the ReqIF file."""
        reqs = []
        for spec_obj in self.root.findall(".//reqif:SPEC-OBJECT", self.ns):
            identifier = self._get_attribute_value_by_ref(spec_obj, "ID_DEF") or "UNKNOWN"
            title = self._get_attribute_value_by_ref(spec_obj, "TITLE_DEF") or ""
            description = self._get_attribute_value_by_ref(spec_obj, "DESC_DEF") or ""
            attributes = self._collect_attributes(spec_obj)
            reqs.append(ReqIFRequirement(identifier, title, description, attributes))
        return reqs
    
    def _get_attribute_value_by_ref(self, spec_obj, ref_name):
        """Return THE-VALUE text for a given attribute definition reference.
        Handles STRING, XHTML, and ENUMERATION attributes."""
        
        # 1️⃣ ATTRIBUTE-VALUE-STRING
        for attr in spec_obj.findall("reqif:ATTRIBUTE-VALUE-STRING", self.ns):
            def_ref = attr.find("reqif:DEFINITION/reqif:ATTRIBUTE-DEFINITION-STRING-REF", self.ns)
            if def_ref is not None and def_ref.text == ref_name:
                val = attr.find("reqif:THE-VALUE", self.ns)
                return val.text.strip() if val is not None and val.text else ""
        
        # 2️⃣ ATTRIBUTE-VALUE-XHTML
        for attr in spec_obj.findall("reqif:ATTRIBUTE-VALUE-XHTML", self.ns):
            def_ref = attr.find("reqif:DEFINITION/reqif:ATTRIBUTE-DEFINITION-XHTML-REF", self.ns)
            if def_ref is not None and def_ref.text == ref_name:
                val_elem = attr.find("reqif:THE-VALUE/xhtml:div", self.ns)
                if val_elem is not None:
                    # Extract all inner text from the XHTML div
                    return "".join(val_elem.itertext()).strip()
                # fallback if div not present
                val_elem = attr.find("reqif:THE-VALUE", self.ns)
                if val_elem is not None and val_elem.text:
                    return val_elem.text.strip()
        
        # 3️⃣ ATTRIBUTE-VALUE-ENUMERATION
        for attr in spec_obj.findall("reqif:ATTRIBUTE-VALUE-ENUMERATION", self.ns):
            def_ref = attr.find("reqif:DEFINITION/reqif:ATTRIBUTE-DEFINITION-ENUMERATION-REF", self.ns)
            if def_ref is not None and def_ref.text == ref_name:
                values = attr.findall("reqif:VALUES/reqif:ENUM-VALUE-REF", self.ns)
                if values:
                    return ','.join(v.text for v in values if v.text)
                val_elem = attr.find("reqif:THE-VALUE", self.ns)
                if val_elem is not None and val_elem.text:
                    return val_elem.text.strip()

        # Attribute not found
        return None

    
    def _collect_attributes(self, spec_obj):
        """Collect all attributes of the requirement into a dict"""
        attrs = {}
        # ATTRIBUTE-VALUE-STRING
        for attr in spec_obj.findall("reqif:ATTRIBUTE-VALUE-STRING", self.ns):
            key = self._get_def_name(attr)
            val_elem = attr.find("reqif:THE-VALUE", self.ns)
            val = val_elem.text.strip() if val_elem is not None and val_elem.text else ""
            if key:
                attrs[key] = val
        # ATTRIBUTE-VALUE-INTEGER
        for attr in spec_obj.findall("reqif:ATTRIBUTE-VALUE-INTEGER", self.ns):
            key = self._get_def_name(attr)
            val_elem = attr.find("reqif:THE-VALUE", self.ns)
            val = int(val_elem.text) if val_elem is not None and val_elem.text else 0
            if key:
                attrs[key] = val
        # ATTRIBUTE-VALUE-BOOLEAN
        for attr in spec_obj.findall("reqif:ATTRIBUTE-VALUE-BOOLEAN", self.ns):
            key = self._get_def_name(attr)
            val_elem = attr.find("reqif:THE-VALUE", self.ns)
            val = (val_elem.text.lower() == "true") if val_elem is not None and val_elem.text else False
            if key:
                attrs[key] = val
        # ATTRIBUTE-VALUE-DATE
        for attr in spec_obj.findall("reqif:ATTRIBUTE-VALUE-DATE", self.ns):
            key = self._get_def_name(attr)
            val_elem = attr.find("reqif:THE-VALUE", self.ns)
            val = val_elem.text.strip() if val_elem is not None and val_elem.text else ""
            if key:
                attrs[key] = val
        return attrs
    
    def _get_def_name(self, attr_element):
        """Get the attribute's human-readable name"""
        def_elem = attr_element.find("reqif:DEFINITION", self.ns)
        if def_elem is not None:
            name_elem = def_elem.find("reqif:LONG-NAME", self.ns)
            if name_elem is not None and name_elem.text:
                return name_elem.text.strip()
        return None

